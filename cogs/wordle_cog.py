"""
Sigmordle — Discord Wordle-style game cog.

UX design
─────────
• /wordle play  creates a private thread for the player.
• The board embed lives as the first (pinned) message in that thread and is
  edited in-place after every guess.
• The player types their 5-letter guess as a normal message in the thread —
  no buttons, no modals needed.  The on_message listener picks it up,
  deletes it (keeps thread clean), and updates the board.
• A single "🏳️ Give Up" button stays on the board message for forfeit.
• /wordle guess <word> is kept as a slash-command fallback.
"""

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord.ext import commands, tasks
from discord import SlashCommandGroup

log = logging.getLogger(__name__)

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
    reminder_embed, remind_status_embed,
)
from utils.wordhistory import get_word_fact
from utils.board_image import board_file
from game.wordle import WordleGame, EntropyEntry


# ── Helpers ───────────────────────────────────────────────────────────────────

def _today() -> str:
    return date.today().isoformat()


def _build_mention_content(players: list[dict], prefix: str, cap: int = 2000) -> str:
    """Build a message string with @mentions that never exceeds `cap` chars.
    Stops adding mentions before the limit rather than cutting one in half."""
    result = prefix
    for p in players:
        token = f"<@{p['user_id']}> "
        if len(result) + len(token) > cap:
            break
        result += token
    return result.rstrip()


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


# ── Core guess logic (shared by on_message + slash command) ───────────────────

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
            game.game_id, guild_id, game.guesses_json(), game.patterns_json(),
            game.entropy_log_json(), "active",
        )
    else:
        stats_row  = await db.get_user_stats(user_id, guild_id)
        cur_streak = (stats_row["current_streak"] if stats_row else 0) + (1 if game.is_won else 0)
        points     = game.compute_points(streak=cur_streak) if game.is_won else 0

        await db.update_game(
            game.game_id, guild_id, game.guesses_json(), game.patterns_json(),
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
        game.game_id, guild_id, game.guesses_json(), game.patterns_json(),
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
    is_daily = game.mode == "daily"
    if game.is_won:
        embed.description = (
            f"🎉 **Solved in {game.num_guesses}/{game.max_guesses}!**  "
            f"**+{points} pts**  ·  ⏱ {_fmt_time(elapsed)}"
        )
    else:
        word_reveal = "The word will be revealed tomorrow." if is_daily else f"The word was **`{game.target}`**"
        embed.description = (
            f"💀 **Out of guesses!**  {word_reveal}  ·  ⏱ {_fmt_time(elapsed)}"
        )
    return embed


async def _archive_thread(
    thread: discord.Thread, won: bool, username: str, num_guesses: int = 0
) -> None:
    """Rename and archive the game thread to reflect the result."""
    try:
        name = f"✅ {username} ({num_guesses} guesses)" if won else f"❌ {username}"
        await thread.edit(name=name[:100], archived=True)
    except Exception:
        pass


# ── Persistent view (Give Up button only) ────────────────────────────────────

def _done_view() -> discord.ui.View:
    return discord.ui.View()


class WordleView(discord.ui.View):
    def __init__(self, user_id: str, guild_id: str):
        super().__init__(timeout=3600)
        self.user_id  = user_id
        self.guild_id = guild_id

    def _not_owner(self, interaction: discord.Interaction) -> bool:
        return str(interaction.user.id) != self.user_id

    @discord.ui.button(label="Give Up", style=discord.ButtonStyle.danger, emoji="🏳️")
    async def giveup_btn(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.guild is None or str(interaction.guild_id) != self.guild_id:
            await interaction.response.send_message("❌ This button is not valid here.", ephemeral=True)
            return
        if self._not_owner(interaction):
            await interaction.response.send_message("❌ This isn't your game!", ephemeral=True)
            return

        game, elapsed = await _do_giveup(
            self.user_id, self.guild_id, interaction.user.display_name
        )
        if not game:
            await interaction.response.send_message("No active game.", ephemeral=True)
            return

        embed = game_embed(game, interaction.user.display_name)
        word_reveal = "The word will be revealed tomorrow." if game.mode == "daily" else f"The word was **`{game.target}`**"
        embed.description = (
            f"🏳️ You gave up.  {word_reveal}  ·  ⏱ {_fmt_time(elapsed)}"
        )
        await interaction.response.edit_message(
            embed=embed, view=_done_view(), file=board_file(game),
        )

        if isinstance(interaction.channel, discord.Thread):
            await _archive_thread(
                interaction.channel, won=False, username=interaction.user.display_name
            )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ── Shared game-creation helper ───────────────────────────────────────────────

async def _start_game(
    guild: discord.Guild,
    user: discord.Member,
    channel: discord.TextChannel,
    mode: str,
    max_guesses: int = 6,
) -> tuple[discord.Thread | None, str]:
    """
    Create a new game + private thread for `user` in `channel`.

    Returns:
      (thread, "")           — new game created successfully
      (thread, "resumed")    — user had an active game; returned existing thread
      (None,  "no_thread")   — active game exists but thread is gone
      (None,  "played")      — daily already played today
      (None,  "<error msg>") — permission / Discord error
    """
    uid   = str(user.id)
    gid   = str(guild.id)
    cid   = str(channel.id)
    today = _today()

    active = await db.get_active_game(uid, gid)
    if active:
        thread_id = active.get("thread_id")
        if thread_id:
            t = guild.get_channel_or_thread(int(thread_id))
            if t:
                return t, "resumed"
        return None, "no_thread"

    if mode == "daily":
        if await db.check_daily_played(uid, gid, today):
            return None, "played"
        target = get_daily_word(today)
    else:
        target = get_random_word()

    game_id = await db.create_game(uid, gid, cid, target, max_guesses, mode,
                                   today if mode == "daily" else None)
    game = WordleGame(
        game_id=game_id, target=target, guesses=[], patterns=[],
        status="active", max_guesses=max_guesses, mode=mode, entropy_log=[],
    )

    mode_emoji  = "📅" if mode == "daily" else "🎲"
    thread_name = f"{mode_emoji} Wordle — {user.display_name}"
    thread: discord.Thread | None = None

    try:
        thread = await channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.private_thread,
            auto_archive_duration=1440,
            invitable=False,
        )
        try:
            await thread.add_user(user)
        except discord.HTTPException:
            pass
    except discord.Forbidden:
        await db.update_game(game_id, gid, "[]", "[]", "[]", "cancelled")
        return None, "❌ I need the **Create Private Threads** permission in this channel."
    except discord.HTTPException:
        try:
            starter = await channel.send(f"🎮 **{user.display_name}** started a Wordle game!")
            thread  = await starter.create_thread(name=thread_name, auto_archive_duration=1440)
        except (discord.Forbidden, discord.HTTPException):
            await db.update_game(game_id, gid, "[]", "[]", "[]", "cancelled")
            return None, "❌ Couldn't create a thread. Check the bot's **Create Threads** permission."

    mode_tag = "📅 Daily" if mode == "daily" else "🎲 Free Play"
    embed = game_embed(game, user.display_name)
    embed.description = (
        f"🎮 **{mode_tag} — game on!**\n"
        f"Guess the hidden 5-letter word in **{max_guesses}** "
        f"{'try' if max_guesses == 1 else 'tries'}.\n\n"
        "**Type your 5-letter word** — the board updates automatically.\n"
        "Click **🏳️ Give Up** to forfeit."
    )
    board_msg = await thread.send(embed=embed, view=WordleView(uid, gid), file=board_file(game))
    await db.update_thread_info(game_id, gid, str(thread.id), str(board_msg.id))
    return thread, ""


def _jump_view(guild_id: int, thread_id: int) -> discord.ui.View:
    """A single link button that jumps directly to a thread."""
    v = discord.ui.View()
    v.add_item(discord.ui.Button(
        label="Go to game →",
        url=f"https://discord.com/channels/{guild_id}/{thread_id}",
        style=discord.ButtonStyle.link,
    ))
    return v


async def _handle_play_button(interaction: discord.Interaction, mode: str) -> None:
    await interaction.response.defer(ephemeral=True)
    if interaction.guild is None:
        await interaction.followup.send("This only works inside a server.", ephemeral=True)
        return
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.followup.send(
            "❌ Games can only be created from a text channel.", ephemeral=True
        )
        return

    thread, flag = await _start_game(
        interaction.guild,
        interaction.user,  # type: ignore[arg-type]
        interaction.channel,
        mode,
    )

    if flag == "resumed":
        await interaction.followup.send(
            f"You already have an active game! → {thread.mention}",
            view=_jump_view(interaction.guild.id, thread.id),
            ephemeral=True,
        )
    elif flag == "no_thread":
        await interaction.followup.send(
            "You have an active game but I can't find the thread. "
            "Use `/wordle board` to locate it.",
            ephemeral=True,
        )
    elif flag == "played":
        await interaction.followup.send(
            "✅ You've already played today's daily word!\n"
            "Click **🎲 Free Play** for another round anytime.",
            ephemeral=True,
        )
    elif thread is None:
        await interaction.followup.send(flag, ephemeral=True)
    else:
        mode_tag = "📅 Daily" if mode == "daily" else "🎲 Free Play"
        await interaction.followup.send(
            f"🎮 **{mode_tag}** game ready! → {thread.mention}",
            view=_jump_view(interaction.guild.id, thread.id),
            ephemeral=True,
        )


class ReminderView(discord.ui.View):
    """Persistent view attached to daily reminder messages — never times out."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🎮 Play Daily",
        style=discord.ButtonStyle.success,
        custom_id="sigmordle:play_daily",
    )
    async def play_daily_btn(self, button: discord.ui.Button, interaction: discord.Interaction):
        await _handle_play_button(interaction, "daily")

    @discord.ui.button(
        label="🎲 Free Play",
        style=discord.ButtonStyle.primary,
        custom_id="sigmordle:play_free",
    )
    async def play_free_btn(self, button: discord.ui.Button, interaction: discord.Interaction):
        await _handle_play_button(interaction, "freeplay")


# ── Cog ───────────────────────────────────────────────────────────────────────

class WordleCog(commands.Cog):
    wordle = SlashCommandGroup("wordle", "Sigmordle — daily word guessing game")
    remind = SlashCommandGroup("remind", "Configure Sigmordle daily reminders")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(ReminderView())   # re-attach persistent view across restarts
        self.daily_reminder_task.start()

    def cog_unload(self):
        self.daily_reminder_task.cancel()

    # ── Shared reminder sender ────────────────────────────────────────────────

    async def _send_reminder(
        self,
        channel: discord.TextChannel,
        guild: discord.Guild,
        today_str: str,
        month: int,
        day: int,
    ) -> None:
        """Build and send the reminder embeds to `channel`. Raises on Discord errors."""
        players     = await db.get_all_players_for_reminder(str(guild.id))
        leaderboard = await db.get_leaderboard(str(guild.id), limit=10)
        word_fact   = get_word_fact(month, day)
        embeds      = reminder_embed(players, leaderboard, today_str, guild.name, word_fact)
        content     = (
            _build_mention_content(players, "🌅 **Sigmordle is LIVE!** ")
            if players else "🌅 **Sigmordle is LIVE!**"
        )
        first = True
        for embed in embeds:
            if first:
                # Attach the play buttons only to the first message
                await channel.send(content=content, embed=embed, view=ReminderView())
                first = False
            else:
                await channel.send(embed=embed)

    # ── Daily reminder background task ────────────────────────────────────────

    @tasks.loop(minutes=1)
    async def daily_reminder_task(self):
        now_utc = datetime.now(timezone.utc)

        for guild in self.bot.guilds:
            gid = str(guild.id)
            try:
                config = await db.get_guild_config(gid)

                # Skip entirely if not configured or disabled
                if not config.get("reminder_enabled") or not config.get("reminder_channel_id"):
                    continue

                try:
                    tz = ZoneInfo(config.get("timezone", "UTC"))
                except (ZoneInfoNotFoundError, KeyError):
                    tz = ZoneInfo("UTC")

                local_now = now_utc.astimezone(tz)
                if not (local_now.hour == 0 and local_now.minute < 5):
                    continue

                today_local = local_now.date().isoformat()
                if config.get("last_reminder_date") == today_local:
                    continue

                # Mark sent before any async I/O to prevent duplicate sends
                await db.mark_reminder_sent(gid, today_local)

                channel = guild.get_channel(int(config["reminder_channel_id"]))
                if not isinstance(channel, discord.TextChannel):
                    log.warning("Reminder channel %s not found for guild %s", config["reminder_channel_id"], gid)
                    continue

                await self._send_reminder(channel, guild, today_local, local_now.month, local_now.day)
            except Exception as exc:
                log.error("daily_reminder failed for guild %s: %s", gid, exc)

    @daily_reminder_task.before_loop
    async def before_daily_reminder(self):
        await self.bot.wait_until_ready()

    # ── /remind channel ───────────────────────────────────────────────────────

    @remind.command(name="channel", description="Set the channel where daily reminders are posted")
    @discord.default_permissions(manage_guild=True)
    async def remind_channel(
        self,
        ctx: discord.ApplicationContext,
        channel: discord.Option(discord.TextChannel, "Channel to post reminders in", required=True),  # type: ignore[valid-type]
    ):
        await ctx.defer(ephemeral=True)
        if ctx.guild is None:
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        await db.set_reminder_channel(str(ctx.guild.id), str(channel.id))
        await ctx.followup.send(
            f"✅ Daily reminders will be posted in {channel.mention}.\n"
            "They fire at **midnight local time** — set your timezone with `/remind timezone`.",
            ephemeral=True,
        )

    # ── /remind timezone ──────────────────────────────────────────────────────

    @remind.command(name="timezone", description="Set the server timezone for reminder timing")
    @discord.default_permissions(manage_guild=True)
    async def remind_timezone(
        self,
        ctx: discord.ApplicationContext,
        tz: discord.Option(  # type: ignore[valid-type]
            str,
            "IANA timezone name — e.g. America/New_York, Europe/London, Asia/Kolkata",
            required=True,
        ),
    ):
        await ctx.defer(ephemeral=True)
        if ctx.guild is None:
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        try:
            zone = ZoneInfo(tz)
        except (ZoneInfoNotFoundError, KeyError):
            await ctx.followup.send(
                f"❌ `{tz}` is not a valid IANA timezone name.\n"
                "Examples: `UTC`  `America/New_York`  `Europe/London`  `Asia/Kolkata`  `Australia/Sydney`",
                ephemeral=True,
            )
            return

        await db.set_guild_timezone(str(ctx.guild.id), tz)
        local_now = datetime.now(zone)
        await ctx.followup.send(
            f"✅ Timezone set to **{tz}**.\n"
            f"Current local time: **{local_now.strftime('%I:%M %p')}** — "
            "reminders fire at **12:00 AM (midnight)**.",
            ephemeral=True,
        )

    # ── /remind status ────────────────────────────────────────────────────────

    @remind.command(name="status", description="Check the current reminder configuration")
    @discord.default_permissions(manage_guild=True)
    async def remind_status(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        if ctx.guild is None:
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        gid    = str(ctx.guild.id)
        config = await db.get_guild_config(gid)

        channel_mention: str | None = None
        chan_id = config.get("reminder_channel_id")
        if chan_id:
            ch = ctx.guild.get_channel(int(chan_id))
            channel_mention = ch.mention if ch else f"`#{chan_id} (deleted)`"

        local_time_str: str | None = None
        tz_name = config.get("timezone") or "UTC"
        try:
            local_time_str = datetime.now(ZoneInfo(tz_name)).strftime("%I:%M %p")
        except Exception:
            pass

        player_count = len(await db.get_all_players_for_reminder(gid))
        embed = remind_status_embed(config, ctx.guild.name, channel_mention, local_time_str, player_count)
        await ctx.followup.send(embed=embed, ephemeral=True)

    # ── /remind test ──────────────────────────────────────────────────────────

    @remind.command(name="test", description="Fire a test reminder to the configured channel right now")
    @discord.default_permissions(manage_guild=True)
    async def remind_test(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        if ctx.guild is None:
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        gid    = str(ctx.guild.id)
        config = await db.get_guild_config(gid)

        if not config.get("reminder_channel_id"):
            await ctx.followup.send(
                "❌ No channel configured. Run `/remind channel #channel` first.",
                ephemeral=True,
            )
            return

        channel = ctx.guild.get_channel(int(config["reminder_channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            await ctx.followup.send(
                "❌ Configured channel not found — run `/remind channel #channel` to update it.",
                ephemeral=True,
            )
            return

        today = date.today()
        try:
            await self._send_reminder(channel, ctx.guild, today.isoformat(), today.month, today.day)
            await ctx.followup.send(
                f"✅ Test reminder sent to {channel.mention}.", ephemeral=True
            )
        except discord.Forbidden:
            await ctx.followup.send(
                f"❌ Missing permissions to post in {channel.mention}.", ephemeral=True
            )
        except Exception as exc:
            await ctx.followup.send(f"❌ Failed: {exc}", ephemeral=True)

    # ── /remind off ───────────────────────────────────────────────────────────

    @remind.command(name="off", description="Disable automatic daily reminders")
    @discord.default_permissions(manage_guild=True)
    async def remind_off(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        if ctx.guild is None:
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        await db.set_reminder_enabled(str(ctx.guild.id), False)
        await ctx.followup.send(
            "🔕 Automatic reminders disabled.\n"
            "Run `/remind channel #channel` to re-enable.",
            ephemeral=True,
        )

    # ── Message-based guess input ─────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.guild is None:
            return
        if not isinstance(message.channel, discord.Thread):
            return

        word = message.content.strip().upper()
        if not (len(word) == 5 and word.isalpha()):
            return

        uid = str(message.author.id)
        gid = str(message.guild.id)

        active = await db.get_active_game(uid, gid)
        if not active:
            return
        if str(active.get("thread_id", "")) != str(message.channel.id):
            return

        # Delete the guess to keep the thread clean
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass

        game = WordleGame.from_db(active)
        err  = game.validate(word)

        if err:
            await message.channel.send(f"❌ **{word}** — {err}", delete_after=5)
            return

        points, elapsed = await _apply_guess(
            game, word, uid, gid, message.author.display_name,
            active.get("created_at", ""),
        )

        # Build updated embed + view
        if game.is_active:
            embed = game_embed(game, message.author.display_name)
            view  = WordleView(uid, gid)
        else:
            embed = _end_embed(game, message.author.display_name, points, elapsed)
            view  = _done_view()

        # Edit the pinned board message
        thread      = message.channel
        board_msg_id = active.get("board_message_id")
        board_msg   = None

        if board_msg_id:
            try:
                board_msg = await thread.fetch_message(int(board_msg_id))
            except (discord.NotFound, ValueError):
                board_msg = None

        if board_msg:
            try:
                await board_msg.edit(embed=embed, view=view, file=board_file(game))
            except (discord.NotFound, discord.Forbidden):
                board_msg = None

        if board_msg is None:
            new_msg = await thread.send(embed=embed, view=view, file=board_file(game))
            await db.update_thread_info(active["game_id"], gid, str(thread.id), str(new_msg.id))

        if not game.is_active:
            await _archive_thread(
                thread, game.is_won, message.author.display_name, game.num_guesses
            )

    # ── /wordle play ──────────────────────────────────────────────────────────

    @wordle.command(name="play", description="Start a Sigmordle game")
    async def play(
        self,
        ctx: discord.ApplicationContext,
        max_guesses: discord.Option(
            int, "Number of guesses allowed (default 6)",
            required=False, default=6,
        ),  # type: ignore[valid-type]
        mode: discord.Option(
            str, "freeplay = random word  ·  daily = shared word of the day",
            required=False, default="freeplay", choices=["freeplay", "daily"],
        ),  # type: ignore[valid-type]
    ):
        await ctx.defer(ephemeral=True)

        if ctx.guild is None:
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        if not isinstance(ctx.channel, discord.TextChannel):
            await ctx.followup.send(
                "❌ Games can only be started in a regular text channel.", ephemeral=True
            )
            return

        if max_guesses < 1 or max_guesses > 10:
            await ctx.followup.send("❌ Guesses must be between 1 and 10.", ephemeral=True)
            return

        uid = str(ctx.author.id)
        gid = str(ctx.guild.id)

        thread, flag = await _start_game(ctx.guild, ctx.author, ctx.channel, mode, max_guesses)

        if flag == "resumed":
            await ctx.followup.send(
                f"You already have an active game! → {thread.mention}",
                ephemeral=True,
            )
        elif flag == "no_thread":
            active = await db.get_active_game(uid, gid)
            game   = WordleGame.from_db(active)
            embed  = game_embed(game, ctx.author.display_name)
            embed.set_footer(text="Couldn't find your game thread — use /wordle board")
            await ctx.followup.send(
                embed=embed, view=WordleView(uid, gid), file=board_file(game), ephemeral=True
            )
        elif flag == "played":
            await ctx.followup.send(
                "✅ You already played today's daily word!\n"
                "Use `/wordle daily` to see results, or `/wordle play mode:freeplay` for another round.",
                ephemeral=True,
            )
        elif thread is None:
            await ctx.followup.send(flag, ephemeral=True)
        else:
            await ctx.followup.send(
                f"🎮 Your game is ready! → {thread.mention}", ephemeral=True
            )

    # ── /wordle guess (slash fallback) ────────────────────────────────────────

    @wordle.command(name="guess", description="Submit a guess (alternative to typing in the thread)")
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

        # Update board message in the thread if possible
        thread_id    = active.get("thread_id")
        board_msg_id = active.get("board_message_id")

        if game.is_active:
            embed = game_embed(game, ctx.author.display_name)
            view  = WordleView(uid, gid)
        else:
            embed = _end_embed(game, ctx.author.display_name, points, elapsed)
            view  = _done_view()

        board_updated = False
        if thread_id and board_msg_id:
            try:
                thread    = ctx.guild.get_channel_or_thread(int(thread_id))
                board_msg = await thread.fetch_message(int(board_msg_id))
                await board_msg.edit(embed=embed, view=view, file=board_file(game))
                board_updated = True
                if not game.is_active:
                    await _archive_thread(
                        thread, game.is_won, ctx.author.display_name, game.num_guesses
                    )
            except Exception:
                pass

        if board_updated:
            thread_obj = ctx.guild.get_channel_or_thread(int(thread_id))
            mention    = thread_obj.mention if thread_obj else "your game thread"
            await ctx.followup.send(
                f"✅ Board updated in {mention}", ephemeral=True
            )
        else:
            await ctx.followup.send(embed=embed, view=view, file=board_file(game), ephemeral=True)

    # ── /wordle board ─────────────────────────────────────────────────────────

    @wordle.command(name="board", description="Find your active game thread")
    async def board(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        if ctx.guild is None:
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        uid    = str(ctx.author.id)
        gid    = str(ctx.guild.id)
        active = await db.get_active_game(uid, gid)

        if not active:
            await ctx.followup.send("No active game. Start one with `/wordle play`.", ephemeral=True)
            return

        thread_id = active.get("thread_id")
        if thread_id:
            thread = ctx.guild.get_channel_or_thread(int(thread_id))
            if thread:
                await ctx.followup.send(
                    f"Your active game is in {thread.mention}", ephemeral=True
                )
                return

        # Thread not found — show board inline
        game  = WordleGame.from_db(active)
        embed = game_embed(game, ctx.author.display_name)
        await ctx.followup.send(embed=embed, view=WordleView(uid, gid), file=board_file(game), ephemeral=True)

    # ── /wordle giveup ────────────────────────────────────────────────────────

    @wordle.command(name="giveup", description="Reveal the word and forfeit your current game")
    async def giveup(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        if ctx.guild is None:
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        uid    = str(ctx.author.id)
        gid    = str(ctx.guild.id)
        active = await db.get_active_game(uid, gid)
        if not active:
            await ctx.followup.send("No active game to forfeit.", ephemeral=True)
            return

        game, elapsed = await _do_giveup(uid, gid, ctx.author.display_name)
        if not game:
            await ctx.followup.send("No active game to forfeit.", ephemeral=True)
            return

        embed = game_embed(game, ctx.author.display_name)
        word_reveal = "The word will be revealed tomorrow." if game.mode == "daily" else f"The word was **`{game.target}`**"
        embed.description = f"🏳️ You gave up. {word_reveal}  ·  ⏱ {_fmt_time(elapsed)}"

        # Update thread if it exists
        thread_id    = active.get("thread_id")
        board_msg_id = active.get("board_message_id")
        if thread_id and board_msg_id:
            try:
                thread    = ctx.guild.get_channel_or_thread(int(thread_id))
                board_msg = await thread.fetch_message(int(board_msg_id))
                await board_msg.edit(embed=embed, view=_done_view(), file=board_file(game))
                await _archive_thread(thread, won=False, username=ctx.author.display_name)
                await ctx.followup.send(
                    f"🏳️ Forfeited. Thread archived.", ephemeral=True
                )
                return
            except Exception:
                pass

        await ctx.followup.send(embed=embed, file=board_file(game), ephemeral=True)

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
                f"**{target_user.display_name}** hasn't played any Sigmordle games yet.",
                ephemeral=True,
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
        # Never reveal the daily word on the same day — show it only from tomorrow onward
        embed = daily_results_embed(rows, word, ctx.guild.name, today, show_word=False)

        if already_played and len(rows) >= 2:
            all_g1bits = []
            for r in rows:
                try:
                    elog = json.loads(r.get("entropy_log") or "[]")
                    if elog and isinstance(elog[0], dict):
                        bits = elog[0].get("actual_bits")
                        if bits is not None:
                            all_g1bits.append(float(bits))
                except (json.JSONDecodeError, IndexError, TypeError, ValueError):
                    pass
            if all_g1bits:
                avg = sum(all_g1bits) / len(all_g1bits)
                embed.add_field(
                    name="📐 Entropy Comparison (Guess 1)",
                    value=(
                        f"Server avg: **{avg:.2f}b**  ·  "
                        f"Best: **{max(all_g1bits):.2f}b**  ·  "
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
    bot.add_cog(WordleCog(bot))
