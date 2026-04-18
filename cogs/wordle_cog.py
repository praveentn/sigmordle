"""
Sigmordle — Discord Wordle-style game cog.

Modal design note
─────────────────
Discord modals always close the moment the user submits — the API provides no
mechanism to keep them open or update their content.  The workaround used here:

  • Previous guesses are rendered as pre-filled InputText rows with emoji
    pattern labels (e.g. "2.  🟨⬛🟩⬛⬛  CRANE"), giving the user full board
    context inside the modal without needing to look elsewhere.
  • The new-guess InputText placeholder summarises the letter keyboard state
    (✅ correct · 🟡 present · ❌ absent) so the user knows which letters to
    avoid / target.
  • On submit the modal closes (Discord's constraint) and the board embed
    updates in-place, including any validation errors.

The persistent ephemeral board message is the primary game UI.
The modal is a clean, focused input layer that shows as much context as fits.
"""

import json
import time
from datetime import date, datetime

import discord
from discord.ext import commands
from discord import SlashCommandGroup

from utils import database as db
from utils.words import (
    get_daily_word, get_random_word,
    compute_expected_entropy, information_gained,
    filter_words, get_all_words, get_remaining,
    pattern_to_emoji, letter_states, CORRECT, PRESENT, ABSENT,
)
from utils.display import (
    game_embed, stats_embed, leaderboard_embed,
    daily_results_embed, server_stats_embed, history_embed, help_embed,
)
from game.wordle import WordleGame, EntropyEntry


# ── Helpers ───────────────────────────────────────────────────────────────────

def _today() -> str:
    return date.today().isoformat()


def _elapsed(created_at: str) -> int:
    try:
        start = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").timestamp()
        return max(0, int(time.time() - start))
    except Exception:
        return 0


def _fmt_time(seconds: int) -> str:
    if seconds <= 0:
        return "—"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


def _guess_label(n: int, pattern, guess: str) -> str:
    """InputText label for a previous guess row inside the modal."""
    return f"{n}.  {pattern_to_emoji(pattern)}  {guess}"


def _keyboard_placeholder(game: WordleGame) -> str:
    """Compact keyboard state for the new-guess input placeholder."""
    states = letter_states(game.guesses, game.patterns)
    ok    = " ".join(l for l, s in states.items() if s == CORRECT)
    maybe = " ".join(l for l, s in states.items() if s == PRESENT)
    nope  = " ".join(l for l, s in states.items() if s == ABSENT)
    parts = []
    if ok:    parts.append(f"✅ {ok}")
    if maybe: parts.append(f"🟡 {maybe}")
    if nope:  parts.append(f"❌ {nope}")
    return ("  ·  ".join(parts) or "e.g.  C R A N E")[:100]


# ── Core guess logic (shared by modal + slash command) ────────────────────────

async def _apply_guess(
    game: WordleGame,
    word: str,
    user_id: str,
    guild_id: str,
    username: str,
    created_at: str,
) -> tuple[int, int]:
    """Apply a validated guess, persist, and return (points, elapsed_seconds)."""
    all_words       = get_all_words()
    remaining       = get_remaining(game.guesses, game.patterns, all_words)
    n_before        = len(remaining)
    exp_entropy     = compute_expected_entropy(word, remaining)

    pattern         = game.apply_guess(word, EntropyEntry(word, n_before, 0, exp_entropy, 0.0))
    remaining_after = filter_words(word, pattern, remaining)
    n_after         = len(remaining_after)
    actual_info     = information_gained(n_before, n_after)

    game.entropy_log[-1].n_after     = n_after
    game.entropy_log[-1].actual_bits = actual_info

    today   = _today()
    points  = 0
    elapsed = _elapsed(created_at)

    if game.is_active:
        await db.update_game(
            game.game_id, game.guesses_json(), game.patterns_json(),
            game.entropy_log_json(), "active",
        )
    else:
        stats_row  = await db.get_user_stats(user_id, guild_id)
        cur_streak = (stats_row["current_streak"] if stats_row else 0) + (1 if game.is_won else 0)
        points     = game.compute_points(streak=cur_streak) if game.is_won else 0

        await db.update_game(
            game.game_id, game.guesses_json(), game.patterns_json(),
            game.entropy_log_json(), game.status,
        )
        starting = game.guesses[0] if game.guesses else word
        await db.upsert_user_stats(
            user_id, guild_id, username,
            won=game.is_won, num_guesses=game.num_guesses,
            points=points, game_date=today, starting_word=starting,
            elapsed_seconds=elapsed,
        )
        await db.add_history(
            game_id=game.game_id, user_id=user_id, guild_id=guild_id,
            username=username, target=game.target,
            guesses=game.guesses_json(), entropy_log=game.entropy_log_json(),
            num_guesses=game.num_guesses, won=game.is_won, points=points,
            elapsed_seconds=elapsed, mode=game.mode,
            game_date=today if game.mode == "daily" else None,
        )
        if game.mode == "daily":
            await db.update_server_stats(guild_id, game.is_won, today)

    return points, elapsed


async def _do_giveup(user_id: str, guild_id: str, username: str) -> tuple[WordleGame | None, int]:
    active = await db.get_active_game(user_id, guild_id)
    if not active:
        return None, 0
    game        = WordleGame.from_db(active)
    game.status = "lost"
    today       = _today()
    elapsed     = _elapsed(active.get("created_at", ""))

    await db.update_game(
        game.game_id, game.guesses_json(), game.patterns_json(),
        game.entropy_log_json(), "lost",
    )
    await db.upsert_user_stats(
        user_id, guild_id, username, won=False,
        num_guesses=game.num_guesses, points=0, game_date=today,
        starting_word=game.guesses[0] if game.guesses else "—",
        elapsed_seconds=elapsed,
    )
    await db.add_history(
        game_id=game.game_id, user_id=user_id, guild_id=guild_id,
        username=username, target=game.target,
        guesses=game.guesses_json(), entropy_log=game.entropy_log_json(),
        num_guesses=game.num_guesses, won=False, points=0,
        elapsed_seconds=elapsed, mode=game.mode,
        game_date=today if game.mode == "daily" else None,
    )
    return game, elapsed


def _end_embed(game: WordleGame, username: str, points: int, elapsed: int) -> discord.Embed:
    embed = game_embed(game, username)
    if game.is_won:
        embed.description = (
            f"🎉 **Solved in {game.num_guesses}/{game.max_guesses}!**  "
            f"**+{points} pts**  ·  ⏱ {_fmt_time(elapsed)}"
        )
    else:
        embed.description = (
            f"💀 **Out of guesses!**  The word was **`{game.target}`**  ·  ⏱ {_fmt_time(elapsed)}"
        )
    return embed


# ── Guess modal ───────────────────────────────────────────────────────────────

class GuessModal(discord.ui.Modal):
    """
    Popup word-input modal.

    Shows the last ≤4 guesses as pre-filled, labelled InputText rows so the
    user can see their board history inside the modal.  The final row is the
    editable guess input; its placeholder shows the current letter-state summary.

    Discord closes modals on every submit — this is an API-level constraint with
    no workaround.  Board errors and results update the persistent embed instead.
    """

    def __init__(self, game: WordleGame, user_id: str, guild_id: str, created_at: str):
        num   = game.num_guesses + 1
        max_g = game.max_guesses
        super().__init__(title=f"🟩 Sigmordle  ·  Word {num} of {max_g}")
        self.user_id    = user_id
        self.guild_id   = guild_id
        self.created_at = created_at

        # Previous guess rows — show at most 4 (modal cap = 5 total items)
        recent    = list(zip(game.guesses, game.patterns))[-4:]
        start_idx = len(game.guesses) - len(recent)

        for i, (guess, pattern) in enumerate(recent):
            self.add_item(discord.ui.InputText(
                label=_guess_label(start_idx + i + 1, pattern, guess),
                value=guess,
                min_length=5,
                max_length=5,
                required=False,
                style=discord.InputTextStyle.short,
            ))

        # New-guess input — placeholder shows letter-state summary
        self.add_item(discord.ui.InputText(
            label=f"Your word  ({max_g - game.num_guesses} guess{'es' if max_g - game.num_guesses != 1 else ''} left):",
            placeholder=_keyboard_placeholder(game),
            min_length=5,
            max_length=5,
            required=True,
            style=discord.InputTextStyle.short,
        ))

    async def callback(self, interaction: discord.Interaction):
        word = self.children[-1].value.strip().upper()

        # Re-fetch game (another tab / device might have moved it)
        active = await db.get_active_game(self.user_id, self.guild_id)
        if not active:
            await interaction.response.send_message("❌ No active game found.", ephemeral=True)
            return

        game = WordleGame.from_db(active)
        err  = game.validate(word)

        if err:
            # Show the error on the board embed; button stays active
            embed = game_embed(game, interaction.user.display_name)
            embed.set_footer(text=f"❌  {err}  —  click  ✏️ Guess a Word  to try again")
            await interaction.response.edit_message(
                embed=embed,
                view=WordleView(self.user_id, self.guild_id, game.remaining_guesses),
            )
            return

        points, elapsed = await _apply_guess(
            game, word, self.user_id, self.guild_id,
            interaction.user.display_name, active.get("created_at", ""),
        )

        if not game.is_active:
            embed = _end_embed(game, interaction.user.display_name, points, elapsed)
            await interaction.response.edit_message(embed=embed, view=_done_view())
        else:
            embed = game_embed(game, interaction.user.display_name)
            await interaction.response.edit_message(
                embed=embed,
                view=WordleView(self.user_id, self.guild_id, game.remaining_guesses),
            )


# ── Persistent view (board buttons) ──────────────────────────────────────────

def _done_view() -> discord.ui.View:
    """Empty view replaces buttons when a game ends."""
    return discord.ui.View()


class WordleView(discord.ui.View):
    def __init__(self, user_id: str, guild_id: str, remaining: int):
        super().__init__(timeout=3600)
        self.user_id   = user_id
        self.guild_id  = guild_id
        self.remaining = remaining
        # Dynamic label showing remaining guesses on the primary button
        self.children[0].label = f"Guess a Word  ({remaining} left)"

    def _not_owner(self, interaction: discord.Interaction) -> bool:
        return str(interaction.user.id) != self.user_id

    @discord.ui.button(label="Guess a Word", style=discord.ButtonStyle.primary, emoji="✏️", row=0)
    async def guess_btn(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self._not_owner(interaction):
            await interaction.response.send_message("❌ This isn't your game!", ephemeral=True)
            return
        active = await db.get_active_game(self.user_id, self.guild_id)
        if not active:
            await interaction.response.send_message("No active game.", ephemeral=True)
            return
        game = WordleGame.from_db(active)
        await interaction.response.send_modal(
            GuessModal(game, self.user_id, self.guild_id, active.get("created_at", ""))
        )

    @discord.ui.button(label="Give Up", style=discord.ButtonStyle.danger, emoji="🏳️", row=0)
    async def giveup_btn(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self._not_owner(interaction):
            await interaction.response.send_message("❌ This isn't your game!", ephemeral=True)
            return
        game, elapsed = await _do_giveup(self.user_id, self.guild_id, interaction.user.display_name)
        if not game:
            await interaction.response.send_message("No active game.", ephemeral=True)
            return
        embed = game_embed(game, interaction.user.display_name)
        embed.description = (
            f"🏳️ You gave up.  The word was **`{game.target}`**  ·  ⏱ {_fmt_time(elapsed)}"
        )
        await interaction.response.edit_message(embed=embed, view=_done_view())

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ── Cog ───────────────────────────────────────────────────────────────────────

class WordleCog(commands.Cog):
    wordle = SlashCommandGroup("wordle", "Sigmordle — daily word guessing game")

    # ── /wordle play ──────────────────────────────────────────────────────────
    @wordle.command(name="play", description="Start a Sigmordle game")
    async def play(
        self,
        ctx: discord.ApplicationContext,
        max_guesses: discord.Option(
            int, "Number of word guesses allowed (default 6)",
            required=False, default=6,
        ),  # type: ignore[valid-type]
        mode: discord.Option(
            str, "freeplay = random word (default)  ·  daily = shared word of the day",
            required=False, default="freeplay", choices=["freeplay", "daily"],
        ),  # type: ignore[valid-type]
    ):
        await ctx.defer(ephemeral=True)
        if ctx.guild is None:
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        if max_guesses < 1 or max_guesses > 10:
            await ctx.followup.send("❌ Guesses must be between 1 and 10.", ephemeral=True)
            return

        uid   = str(ctx.author.id)
        gid   = str(ctx.guild.id)
        cid   = str(ctx.channel.id)
        today = _today()

        # Resume active game if one exists
        active = await db.get_active_game(uid, gid)
        if active:
            game  = WordleGame.from_db(active)
            embed = game_embed(game, ctx.author.display_name)
            embed.set_footer(text="You already have an active game — click ✏️ Guess a Word below.")
            await ctx.followup.send(embed=embed, view=WordleView(uid, gid, game.remaining_guesses), ephemeral=True)
            return

        if mode == "daily":
            if await db.check_daily_played(uid, gid, today):
                await ctx.followup.send(
                    "✅ You already played today's daily word!\n"
                    "Use `/wordle daily` to see results, or `/wordle play mode:freeplay` for another round.",
                    ephemeral=True,
                )
                return
            target = get_daily_word(today)
        else:
            target = get_random_word()

        game_id = await db.create_game(uid, gid, cid, target, max_guesses, mode,
                                        today if mode == "daily" else None)
        game = WordleGame(
            game_id=game_id, target=target, guesses=[], patterns=[],
            status="active", max_guesses=max_guesses, mode=mode, entropy_log=[],
        )

        mode_tag = "📅 Daily" if mode == "daily" else "🎲 Free Play"
        embed    = game_embed(game, ctx.author.display_name)
        embed.description = (
            f"🎮 **{mode_tag} — game on!**\n"
            f"Guess the hidden 5-letter word in **{max_guesses}** tries.\n\n"
            "Click **✏️ Guess a Word** — a popup appears where you type your guess.\n"
            "Your previous guesses are shown inside the popup for reference.\n"
            "_You can also use `/wordle guess <word>` if you prefer commands._"
        )
        await ctx.followup.send(embed=embed, view=WordleView(uid, gid, max_guesses), ephemeral=True)

    # ── /wordle guess (text fallback) ─────────────────────────────────────────
    @wordle.command(name="guess", description="Type a word directly (alternative to the board button)")
    async def guess(
        self,
        ctx: discord.ApplicationContext,
        word: discord.Option(str, "Your 5-letter word", required=True),  # type: ignore[valid-type]
    ):
        await ctx.defer(ephemeral=True)
        if ctx.guild is None:
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        uid  = str(ctx.author.id)
        gid  = str(ctx.guild.id)
        word = word.strip().upper()

        active = await db.get_active_game(uid, gid)
        if not active:
            await ctx.followup.send(
                "You don't have an active game. Use `/wordle play` to start one.", ephemeral=True
            )
            return

        game = WordleGame.from_db(active)
        err  = game.validate(word)
        if err:
            await ctx.followup.send(f"❌ {err}", ephemeral=True)
            return

        points, elapsed = await _apply_guess(
            game, word, uid, gid, ctx.author.display_name, active.get("created_at", "")
        )

        if not game.is_active:
            embed = _end_embed(game, ctx.author.display_name, points, elapsed)
            await ctx.followup.send(embed=embed, ephemeral=True)
        else:
            embed = game_embed(game, ctx.author.display_name)
            await ctx.followup.send(
                embed=embed, view=WordleView(uid, gid, game.remaining_guesses), ephemeral=True
            )

    # ── /wordle board ─────────────────────────────────────────────────────────
    @wordle.command(name="board", description="Re-show your current game board with the Guess button")
    async def board(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        if ctx.guild is None:
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        active = await db.get_active_game(str(ctx.author.id), str(ctx.guild.id))
        if not active:
            await ctx.followup.send("No active game. Start one with `/wordle play`.", ephemeral=True)
            return

        game  = WordleGame.from_db(active)
        embed = game_embed(game, ctx.author.display_name)
        await ctx.followup.send(
            embed=embed,
            view=WordleView(str(ctx.author.id), str(ctx.guild.id), game.remaining_guesses),
            ephemeral=True,
        )

    # ── /wordle giveup ────────────────────────────────────────────────────────
    @wordle.command(name="giveup", description="Reveal the word and forfeit your current game")
    async def giveup(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        if ctx.guild is None:
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        game, elapsed = await _do_giveup(str(ctx.author.id), str(ctx.guild.id), ctx.author.display_name)
        if not game:
            await ctx.followup.send("No active game to forfeit.", ephemeral=True)
            return

        embed = game_embed(game, ctx.author.display_name)
        embed.description = f"🏳️ You gave up. The word was **`{game.target}`**  ·  ⏱ {_fmt_time(elapsed)}"
        await ctx.followup.send(embed=embed, ephemeral=True)

    # ── /wordle stats ─────────────────────────────────────────────────────────
    @wordle.command(name="stats", description="Show Wordle stats for yourself or another user")
    async def stats(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(discord.Member, "User to look up (default: yourself)", required=False),  # type: ignore[valid-type]
    ):
        await ctx.defer(ephemeral=True)
        if ctx.guild is None:
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        target_user = user or ctx.author
        row = await db.get_user_stats(str(target_user.id), str(ctx.guild.id))
        if not row:
            await ctx.followup.send(
                f"**{target_user.display_name}** hasn't played any Sigmordle games yet.", ephemeral=True
            )
            return

        embed = stats_embed(row, target_user.display_name)
        await ctx.followup.send(embed=embed, ephemeral=True)

    # ── /wordle leaderboard ───────────────────────────────────────────────────
    @wordle.command(name="leaderboard", description="Show the server Sigmordle leaderboard")
    async def leaderboard(
        self,
        ctx: discord.ApplicationContext,
        limit: discord.Option(int, "Entries to show (default 10)", required=False, default=10),  # type: ignore[valid-type]
    ):
        await ctx.defer()
        if ctx.guild is None:
            await ctx.followup.send("Use this inside a server.")
            return

        limit = max(3, min(limit, 25))
        rows  = await db.get_leaderboard(str(ctx.guild.id), limit)
        embed = leaderboard_embed(rows, ctx.guild.name)
        await ctx.followup.send(embed=embed)

    # ── /wordle daily ─────────────────────────────────────────────────────────
    @wordle.command(name="daily", description="Show today's daily word results for this server")
    async def daily(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        if ctx.guild is None:
            await ctx.followup.send("Use this inside a server.")
            return

        gid   = str(ctx.guild.id)
        uid   = str(ctx.author.id)
        today = _today()
        word  = get_daily_word(today)

        already_played = await db.check_daily_played(uid, gid, today)
        rows  = await db.get_daily_results(gid, today)
        embed = daily_results_embed(rows, word, ctx.guild.name, today, show_word=already_played)

        if already_played and len(rows) >= 2:
            all_g1bits = []
            for r in rows:
                elog = json.loads(r.get("entropy_log") or "[]")
                if elog:
                    all_g1bits.append(elog[0]["actual_bits"])
            if all_g1bits:
                avg = sum(all_g1bits) / len(all_g1bits)
                embed.add_field(
                    name="📐 Entropy Comparison (Guess 1)",
                    value=(
                        f"Server avg: **{avg:.2f} bits**  |  "
                        f"Best: **{max(all_g1bits):.2f} bits**  |  "
                        f"Players: **{len(all_g1bits)}**"
                    ),
                    inline=False,
                )

        await ctx.followup.send(embed=embed)

    # ── /wordle server ────────────────────────────────────────────────────────
    @wordle.command(name="server", description="Show server-wide Sigmordle statistics")
    async def server(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        if ctx.guild is None:
            await ctx.followup.send("Use this inside a server.")
            return

        gid          = str(ctx.guild.id)
        stats        = await db.get_server_stats(gid)
        word_stats   = await db.get_server_word_stats(gid)
        top_starters = await db.get_top_starting_words(gid)
        embed        = server_stats_embed(stats, ctx.guild.name, word_stats, top_starters)
        await ctx.followup.send(embed=embed)

    # ── /wordle history ───────────────────────────────────────────────────────
    @wordle.command(name="history", description="Show your recent Sigmordle games")
    async def history(
        self,
        ctx: discord.ApplicationContext,
        limit: discord.Option(int, "Number of games (default 5)", required=False, default=5),  # type: ignore[valid-type]
    ):
        await ctx.defer(ephemeral=True)
        if ctx.guild is None:
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        limit = max(1, min(limit, 15))
        rows  = await db.get_user_history(str(ctx.author.id), str(ctx.guild.id), limit)
        embed = history_embed(rows, ctx.author.display_name)
        await ctx.followup.send(embed=embed, ephemeral=True)

    # ── /wordle help ──────────────────────────────────────────────────────────
    @wordle.command(name="help", description="How to play Sigmordle")
    async def help(self, ctx: discord.ApplicationContext):
        await ctx.respond(embed=help_embed(), ephemeral=True)


def setup(bot: commands.Bot) -> None:
    bot.add_cog(WordleCog())
