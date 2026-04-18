from __future__ import annotations
import json
from datetime import date, timezone, datetime

import discord
from discord.ext import commands
from discord import SlashCommandGroup

from utils import database as db
from utils.words import (
    get_daily_word, get_random_word, is_valid,
    compute_expected_entropy, information_gained,
    filter_words, get_all_words, get_remaining,
)
from utils.display import (
    game_embed, stats_embed, leaderboard_embed,
    daily_results_embed, server_stats_embed, history_embed, help_embed,
)
from game.wordle import WordleGame, EntropyEntry


def _today() -> str:
    return date.today().isoformat()


def _require_guild(ctx: discord.ApplicationContext) -> bool:
    return ctx.guild is not None


# ── Cog ───────────────────────────────────────────────────────────────────────

class WordleCog(commands.Cog):
    wordle = SlashCommandGroup("wordle", "Sigmordle — daily word guessing game")

    # ── /wordle play ──────────────────────────────────────────────────────────
    @wordle.command(name="play", description="Start a Sigmordle game")
    async def play(
        self,
        ctx: discord.ApplicationContext,
        max_guesses: discord.Option(
            int,
            "Number of word guesses allowed (default 6, max 10)",
            required=False,
            default=6,
        ),  # type: ignore[valid-type]
        mode: discord.Option(
            str,
            "freeplay = random word (default) · daily = shared word of the day",
            required=False,
            default="freeplay",
            choices=["freeplay", "daily"],
        ),  # type: ignore[valid-type]
    ):
        await ctx.defer(ephemeral=True)
        if not _require_guild(ctx):
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        if max_guesses < 1 or max_guesses > 10:
            await ctx.followup.send("❌ Guesses must be between 1 and 10.", ephemeral=True)
            return

        uid  = str(ctx.author.id)
        gid  = str(ctx.guild.id)  # type: ignore[union-attr]
        cid  = str(ctx.channel.id)
        today = _today()

        # Check for already-active game
        active = await db.get_active_game(uid, gid)
        if active:
            game  = WordleGame.from_db(active)
            embed = game_embed(game, ctx.author.display_name)
            embed.set_footer(text="You already have an active game! Use /wordle guess <word> to continue.")
            await ctx.followup.send(embed=embed, ephemeral=True)
            return

        # Daily — check if already played today
        if mode == "daily":
            already = await db.check_daily_played(uid, gid, today)
            if already:
                await ctx.followup.send(
                    "✅ You already played today's daily word! Come back tomorrow.\n"
                    "Use `/wordle daily` to see today's server results, or `/wordle play mode:freeplay` for another round.",
                    ephemeral=True,
                )
                return
            target = get_daily_word(today)
        else:
            target = get_random_word()

        game_id = await db.create_game(uid, gid, cid, target, max_guesses, mode, today if mode == "daily" else None)
        game    = WordleGame(
            game_id=game_id, target=target, guesses=[], patterns=[],
            status="active", max_guesses=max_guesses, mode=mode, entropy_log=[],
        )

        embed = game_embed(game, ctx.author.display_name)
        embed.description = (
            "🎮 **Game started!** Use `/wordle guess <word>` to make your first guess.\n"
            f"You have **{max_guesses}** guess{'es' if max_guesses != 1 else ''}."
        )
        await ctx.followup.send(embed=embed, ephemeral=True)

    # ── /wordle guess ─────────────────────────────────────────────────────────
    @wordle.command(name="guess", description="Submit a guess in your active game")
    async def guess(
        self,
        ctx: discord.ApplicationContext,
        word: discord.Option(str, "Your 5-letter guess", required=True),  # type: ignore[valid-type]
    ):
        await ctx.defer(ephemeral=True)
        if not _require_guild(ctx):
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        uid   = str(ctx.author.id)
        gid   = str(ctx.guild.id)  # type: ignore[union-attr]
        word  = word.strip().upper()
        today = _today()

        active = await db.get_active_game(uid, gid)
        if not active:
            await ctx.followup.send(
                "You don't have an active game. Use `/wordle play` to start one.",
                ephemeral=True,
            )
            return

        game = WordleGame.from_db(active)

        err = game.validate(word)
        if err:
            await ctx.followup.send(f"❌ {err}", ephemeral=True)
            return

        # ── Entropy calculations ───────────────────────────────────────────
        all_words   = get_all_words()
        remaining   = get_remaining(game.guesses, game.patterns, all_words)
        n_before    = len(remaining)
        exp_entropy = compute_expected_entropy(word, remaining)

        # Apply guess
        pattern   = game.apply_guess(word, EntropyEntry(word, n_before, 0, exp_entropy, 0.0))

        remaining_after = filter_words(word, pattern, remaining)
        n_after         = len(remaining_after)
        actual_info     = information_gained(n_before, n_after)

        # Patch in real n_after / actual_bits on the last entropy entry
        game.entropy_log[-1].n_after    = n_after
        game.entropy_log[-1].actual_bits = actual_info

        # ── Persist ───────────────────────────────────────────────────────
        if game.is_active:
            await db.update_game(
                game.game_id, game.guesses_json(), game.patterns_json(),
                game.entropy_log_json(), "active",
            )
        else:
            # Game over — finalise
            today_date = today
            points     = 0
            streak     = 0

            if game.is_won or game.is_lost:
                # Fetch current streak before upsert to compute bonus
                stats_row = await db.get_user_stats(uid, gid)
                cur_streak = (stats_row["current_streak"] if stats_row else 0) + (1 if game.is_won else 0)
                points = game.compute_points(streak=cur_streak) if game.is_won else 0

                await db.update_game(
                    game.game_id, game.guesses_json(), game.patterns_json(),
                    game.entropy_log_json(), game.status,
                )
                starting = game.guesses[0] if game.guesses else word
                streak = await db.upsert_user_stats(
                    uid, gid, ctx.author.display_name,
                    won=game.is_won,
                    num_guesses=game.num_guesses,
                    points=points,
                    game_date=today_date,
                    starting_word=starting,
                )
                await db.add_history(
                    game_id=game.game_id,
                    user_id=uid,
                    guild_id=gid,
                    username=ctx.author.display_name,
                    target=game.target,
                    guesses=game.guesses_json(),
                    entropy_log=game.entropy_log_json(),
                    num_guesses=game.num_guesses,
                    won=game.is_won,
                    points=points,
                    mode=game.mode,
                    game_date=today_date if game.mode == "daily" else None,
                )
                if game.mode == "daily":
                    await db.update_server_stats(gid, game.is_won, today_date)

        embed = game_embed(game, ctx.author.display_name)

        if game.is_won:
            stats_row = await db.get_user_stats(uid, gid)
            streak_now = stats_row["current_streak"] if stats_row else 1
            bonus_msg  = f"\n🔥 Streak: **{streak_now}** days!" if streak_now >= 2 else ""
            embed.description = (
                f"🎉 **Brilliant!** You got it in {game.num_guesses} guess{'es' if game.num_guesses != 1 else ''}!"
                f"\n🏆 **+{points} points**{bonus_msg}"
            )
        elif game.is_lost:
            embed.description = (
                f"💀 Out of guesses! The word was **`{game.target}`**.\n"
                "Better luck next time — come back tomorrow for the daily word!"
            )

        await ctx.followup.send(embed=embed, ephemeral=True)

    # ── /wordle board ─────────────────────────────────────────────────────────
    @wordle.command(name="board", description="Show your current game board")
    async def board(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        if not _require_guild(ctx):
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        active = await db.get_active_game(str(ctx.author.id), str(ctx.guild.id))  # type: ignore[union-attr]
        if not active:
            await ctx.followup.send(
                "No active game. Start one with `/wordle play`.", ephemeral=True
            )
            return

        game  = WordleGame.from_db(active)
        embed = game_embed(game, ctx.author.display_name)
        await ctx.followup.send(embed=embed, ephemeral=True)

    # ── /wordle giveup ────────────────────────────────────────────────────────
    @wordle.command(name="giveup", description="Reveal the word and forfeit your current game")
    async def giveup(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        if not _require_guild(ctx):
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        uid = str(ctx.author.id)
        gid = str(ctx.guild.id)  # type: ignore[union-attr]

        active = await db.get_active_game(uid, gid)
        if not active:
            await ctx.followup.send("No active game to forfeit.", ephemeral=True)
            return

        game        = WordleGame.from_db(active)
        game.status = "lost"

        await db.update_game(
            game.game_id, game.guesses_json(), game.patterns_json(),
            game.entropy_log_json(), "lost",
        )
        today = _today()
        await db.upsert_user_stats(
            uid, gid, ctx.author.display_name,
            won=False, num_guesses=game.num_guesses, points=0,
            game_date=today, starting_word=game.guesses[0] if game.guesses else "—",
        )
        await db.add_history(
            game_id=game.game_id, user_id=uid, guild_id=gid,
            username=ctx.author.display_name, target=game.target,
            guesses=game.guesses_json(), entropy_log=game.entropy_log_json(),
            num_guesses=game.num_guesses, won=False, points=0,
            mode=game.mode, game_date=today if game.mode == "daily" else None,
        )

        embed = game_embed(game, ctx.author.display_name)
        embed.description = f"🏳️ You gave up. The word was **`{game.target}`**."
        await ctx.followup.send(embed=embed, ephemeral=True)

    # ── /wordle stats ─────────────────────────────────────────────────────────
    @wordle.command(name="stats", description="Show Wordle stats for yourself or another user")
    async def stats(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(discord.Member, "User to look up (default: yourself)", required=False),  # type: ignore[valid-type]
    ):
        await ctx.defer(ephemeral=True)
        if not _require_guild(ctx):
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        target_user = user or ctx.author
        row = await db.get_user_stats(str(target_user.id), str(ctx.guild.id))  # type: ignore[union-attr]
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
        if not _require_guild(ctx):
            await ctx.followup.send("Use this inside a server.")
            return

        limit = max(3, min(limit, 25))
        rows  = await db.get_leaderboard(str(ctx.guild.id), limit)  # type: ignore[union-attr]
        embed = leaderboard_embed(rows, ctx.guild.name)  # type: ignore[union-attr]
        await ctx.followup.send(embed=embed)

    # ── /wordle daily ─────────────────────────────────────────────────────────
    @wordle.command(name="daily", description="Show today's daily word results for this server")
    async def daily(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        if not _require_guild(ctx):
            await ctx.followup.send("Use this inside a server.")
            return

        gid   = str(ctx.guild.id)  # type: ignore[union-attr]
        uid   = str(ctx.author.id)
        today = _today()
        word  = get_daily_word(today)

        already_played = await db.check_daily_played(uid, gid, today)
        rows  = await db.get_daily_results(gid, today)
        embed = daily_results_embed(rows, word, ctx.guild.name, today, show_word=already_played)  # type: ignore[union-attr]

        # Show entropy comparison if caller has played
        if already_played and len(rows) >= 2:
            my_row     = next((r for r in rows if True), None)  # first match (rows ordered by score)
            all_g1bits = [
                json.loads(r.get("entropy_log") or "[]")[0]["actual_bits"]
                for r in rows
                if json.loads(r.get("entropy_log") or "[]")
            ]
            if all_g1bits:
                avg = sum(all_g1bits) / len(all_g1bits)
                embed.add_field(
                    name="📐 Entropy Comparison (Guess 1)",
                    value=(
                        f"Server avg: **{avg:.2f} bits** | "
                        f"Best: **{max(all_g1bits):.2f} bits** | "
                        f"Players: **{len(all_g1bits)}**"
                    ),
                    inline=False,
                )

        await ctx.followup.send(embed=embed)

    # ── /wordle server ────────────────────────────────────────────────────────
    @wordle.command(name="server", description="Show server-wide Sigmordle statistics")
    async def server(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        if not _require_guild(ctx):
            await ctx.followup.send("Use this inside a server.")
            return

        gid          = str(ctx.guild.id)  # type: ignore[union-attr]
        stats        = await db.get_server_stats(gid)
        word_stats   = await db.get_server_word_stats(gid)
        top_starters = await db.get_top_starting_words(gid)
        embed        = server_stats_embed(stats, ctx.guild.name, word_stats, top_starters)  # type: ignore[union-attr]
        await ctx.followup.send(embed=embed)

    # ── /wordle history ───────────────────────────────────────────────────────
    @wordle.command(name="history", description="Show your recent Sigmordle games")
    async def history(
        self,
        ctx: discord.ApplicationContext,
        limit: discord.Option(int, "Number of games (default 5)", required=False, default=5),  # type: ignore[valid-type]
    ):
        await ctx.defer(ephemeral=True)
        if not _require_guild(ctx):
            await ctx.followup.send("Use this inside a server.", ephemeral=True)
            return

        limit = max(1, min(limit, 15))
        rows  = await db.get_user_history(str(ctx.author.id), str(ctx.guild.id), limit)  # type: ignore[union-attr]
        embed = history_embed(rows, ctx.author.display_name)
        await ctx.followup.send(embed=embed, ephemeral=True)

    # ── /wordle help ──────────────────────────────────────────────────────────
    @wordle.command(name="help", description="How to play Sigmordle")
    async def help(self, ctx: discord.ApplicationContext):
        await ctx.respond(embed=help_embed(), ephemeral=True)


def setup(bot: commands.Bot) -> None:
    bot.add_cog(WordleCog())
