import json
import math
from collections import Counter

import discord

from utils.words import (
    pattern_to_emoji, build_keyboard_lines,
    CORRECT, PRESENT, ABSENT,
)
from game.wordle import WordleGame, EntropyEntry

# ── Colour palette ────────────────────────────────────────────────────────────
GREEN  = discord.Colour.green()
YELLOW = discord.Colour.gold()
RED    = discord.Colour.red()
BLUE   = discord.Colour(0x5865F2)   # Discord blurple
GREY   = discord.Colour.greyple()
ORANGE = discord.Colour.orange()


def _medal(rank: int) -> str:
    return ["🥇", "🥈", "🥉"][rank] if rank < 3 else f"**{rank + 1}.**"


def _pct(num: int, denom: int) -> str:
    if denom == 0:
        return "0%"
    return f"{round(100 * num / denom)}%"


def _bar(count: int, max_count: int, width: int = 12) -> str:
    filled = round(width * count / max_count) if max_count else 0
    return "█" * filled + "░" * (width - filled)


# ── Row number helpers ────────────────────────────────────────────────────────

_NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
_EMPTY_TILE = "⬜⬜⬜⬜⬜"
_BAR_WIDTH  = 12
_MAX_BITS   = math.log2(5917)   # ~12.5 — theoretical max for our word list


def _row_num(i: int) -> str:
    return _NUM_EMOJI[i] if i < len(_NUM_EMOJI) else f"`{i + 1}.`"


def _entropy_bar(bits: float) -> str:
    filled = round(_BAR_WIDTH * min(bits, _MAX_BITS) / _MAX_BITS)
    return "█" * filled + "░" * (_BAR_WIDTH - filled)


# ── Board renderer ────────────────────────────────────────────────────────────

def render_board(game: WordleGame) -> str:
    lines: list[str] = []
    for i, (guess, pat) in enumerate(zip(game.guesses, game.patterns)):
        emoji_row = pattern_to_emoji(pat)
        lines.append(f"{_row_num(i)} {emoji_row}  `{guess}`")
    for i in range(len(game.guesses), game.max_guesses):
        lines.append(f"{_row_num(i)} {_EMPTY_TILE}")
    return "\n".join(lines)


def render_keyboard(game: WordleGame) -> str:
    correct, present, absent, untried = build_keyboard_lines(game.guesses, game.patterns)
    parts: list[str] = []
    if correct:
        parts.append(f"🟩 **{correct}**")
    if present:
        parts.append(f"🟨 **{present}**")
    if absent:
        parts.append(f"⬛ ~~{absent}~~")
    if untried:
        parts.append(f"⬜ {untried}")
    return "\n".join(parts) or "⬜ A B C D E F G H I J K L M N O P Q R S T U V W X Y Z"


def render_entropy(log: list[EntropyEntry]) -> str:
    if not log:
        return "*No guesses yet — make your first guess!*"

    lines: list[str] = []
    for i, e in enumerate(log):
        bar   = _entropy_bar(e.actual_bits)
        delta = e.actual_bits - e.expected_bits
        sign  = "+" if delta >= 0 else ""
        lines.append(
            f"{_row_num(i)} `{e.guess}` `{bar}` **{e.actual_bits:.2f}b**"
            f"  _{e.n_before:,}→{e.n_after:,} words_ ({sign}{delta:.2f} vs exp)"
        )

    total_actual   = sum(e.actual_bits for e in log)
    total_expected = sum(e.expected_bits for e in log)
    last_n         = log[-1].n_after
    lines.append(
        f"\n**Total: {total_actual:.2f} bits** _(expected {total_expected:.2f})_"
        f"  ·  **{last_n:,}** word{'s' if last_n != 1 else ''} remaining"
    )
    return "\n".join(lines)


# ── Main game embed ───────────────────────────────────────────────────────────

def game_embed(game: WordleGame, username: str) -> discord.Embed:
    if game.is_won:
        colour = GREEN
        title  = f"🎉 Solved in {game.num_guesses}/{game.max_guesses}!"
    elif game.is_lost:
        colour = RED
        title  = f"😔 Better luck next time! The word was **{game.target}**"
    else:
        colour = BLUE
        left   = game.remaining_guesses
        title  = f"🟩 Sigmordle — {left} guess{'es' if left != 1 else ''} left"

    mode_tag = "📅 Daily" if game.mode == "daily" else "🎲 Free Play"
    embed = discord.Embed(title=title, colour=colour)
    embed.set_author(name=f"{username} · {mode_tag}")

    embed.add_field(name="Board", value=render_board(game), inline=False)
    embed.add_field(name="Letters", value=render_keyboard(game), inline=False)

    if game.entropy_log:
        embed.add_field(
            name="📐 Entropy per Guess",
            value=render_entropy(game.entropy_log),
            inline=False,
        )

    if game.is_won:
        embed.set_footer(text=f"Game #{game.game_id} · {game.mode}")
    elif game.is_lost:
        embed.set_footer(text=f"Game #{game.game_id} — try /wordle play again tomorrow!")
    else:
        embed.set_footer(text=f"Game #{game.game_id} · Use /wordle guess <word>")

    return embed


# ── Stats embed ───────────────────────────────────────────────────────────────

def stats_embed(row: dict, username: str) -> discord.Embed:
    played   = row["games_played"]
    won      = row["games_won"]
    pts      = row["total_points"]
    streak   = row["current_streak"]
    max_str  = row["max_streak"]
    dist     = json.loads(row["guess_dist"])
    sw_raw   = json.loads(row["starting_words"])

    win_rate = _pct(won, played)
    avg_pts  = f"{pts / played:.1f}" if played else "0"

    embed = discord.Embed(title=f"📊 Stats — {username}", colour=BLUE)

    overview = (
        f"🎮 Games Played: **{played}**\n"
        f"✅ Won: **{won}** ({win_rate})\n"
        f"🏆 Total Points: **{pts}** (avg {avg_pts}/game)\n"
        f"🔥 Current Streak: **{streak}**  |  Best: **{max_str}**"
    )
    embed.add_field(name="Overview", value=overview, inline=False)

    # Guess distribution bar chart
    if dist:
        max_count = max(dist.values(), default=1)
        bars: list[str] = []
        for g in sorted(dist.keys(), key=int):
            c    = dist[g]
            bar  = _bar(c, max_count)
            bars.append(f"`{g}` {bar} {c}")
        embed.add_field(name="Guess Distribution", value="\n".join(bars), inline=False)

    # Starting words breakdown
    if sw_raw:
        ctr     = Counter(sw_raw)
        top5    = ctr.most_common(5)
        sw_text = "  ".join(f"`{w}` ×{n}" for w, n in top5)
        embed.add_field(name="Favourite Openers", value=sw_text, inline=False)

    embed.set_footer(text="Use /wordle leaderboard to compare with the server")
    return embed


# ── Leaderboard embed ─────────────────────────────────────────────────────────

def leaderboard_embed(rows: list[dict], guild_name: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"🏆 Leaderboard — {guild_name}",
        colour=ORANGE,
    )

    if not rows:
        embed.description = "*No games played yet. Use `/wordle play` to start!*"
        return embed

    lines: list[str] = []
    for i, r in enumerate(rows):
        name   = r["username"] or "Unknown"
        pts    = r["total_points"]
        played = r["games_played"]
        wr     = _pct(r["games_won"], played)
        streak = r["current_streak"]
        streak_tag = f" 🔥{streak}" if streak >= 3 else ""
        lines.append(
            f"{_medal(i)} **{name}** — {pts} pts  ({wr} win rate, {played} games){streak_tag}"
        )

    embed.description = "\n".join(lines)
    embed.set_footer(text="Points: Guess 1=10 · 2=7 · 3=5 · 4=3 · 5=2 · 6=1 + streak bonuses")
    return embed


# ── Daily results embed ───────────────────────────────────────────────────────

def daily_results_embed(
    rows: list[dict],
    word: str,
    guild_name: str,
    game_date: str,
    show_word: bool = True,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"📅 Daily Word Results — {game_date}",
        colour=BLUE,
    )

    if show_word:
        embed.description = f"Today's word: **`{word}`**"
    else:
        embed.description = "*Play the daily word to see today's answer!*\nUse `/wordle play`"

    if not rows:
        embed.add_field(name=guild_name, value="*Nobody has played yet today.*", inline=False)
        return embed

    solved = [r for r in rows if r["won"]]
    failed = [r for r in rows if not r["won"]]

    if solved:
        lines: list[str] = []
        for i, r in enumerate(solved):
            g   = r["num_guesses"]
            pts = r["points"]
            lines.append(
                f"{_medal(i)} **{r['username']}** — {g} guess{'es' if g != 1 else ''} (+{pts} pts)"
            )
        embed.add_field(name=f"✅ Solved ({len(solved)})", value="\n".join(lines[:15]), inline=False)

    if failed:
        fail_names = ", ".join(r["username"] for r in failed[:10])
        embed.add_field(
            name=f"❌ Did Not Solve ({len(failed)})",
            value=fail_names or "—",
            inline=False,
        )

    # Entropy comparison across all players
    all_elog = []
    for r in rows:
        try:
            elog = json.loads(r.get("entropy_log") or "[]")
            all_elog.append(elog)
        except Exception:
            pass

    if len(all_elog) >= 2:
        # Average bits gained on guess 1
        g1_bits = [e[0]["actual_bits"] for e in all_elog if e]
        if g1_bits:
            avg_g1 = sum(g1_bits) / len(g1_bits)
            embed.add_field(
                name="📐 Server Entropy (Guess 1 avg)",
                value=f"Average info gain: **{avg_g1:.2f} bits** across {len(g1_bits)} players",
                inline=False,
            )

    total_played = len(rows)
    avg_guesses  = sum(r["num_guesses"] for r in solved) / len(solved) if solved else 0
    embed.set_footer(
        text=(
            f"{guild_name} · {total_played} played · "
            f"{len(solved)} solved · "
            + (f"avg {avg_guesses:.1f} guesses" if solved else "nobody solved yet")
        )
    )
    return embed


# ── Server stats embed ────────────────────────────────────────────────────────

def server_stats_embed(
    stats: dict | None,
    guild_name: str,
    word_stats: list[dict],
    top_starters: list[tuple[str, int]],
) -> discord.Embed:
    embed = discord.Embed(title=f"🌐 Server Stats — {guild_name}", colour=ORANGE)

    if stats:
        total_g  = stats["total_games"]
        total_w  = stats["total_wins"]
        s_streak = stats["server_streak"]
        max_s    = stats["max_server_streak"]
        wr       = _pct(total_w, total_g)
        overview = (
            f"🎮 Total Games: **{total_g}**\n"
            f"✅ Total Wins: **{total_w}** ({wr})\n"
            f"🔥 Current Streak: **{s_streak}**  |  Best: **{max_s}**"
        )
        embed.add_field(name="Overview", value=overview, inline=False)
    else:
        embed.description = "*No games yet! Use `/wordle play` to start.*"
        return embed

    if word_stats:
        lines: list[str] = []
        for r in word_stats[:8]:
            sr = _pct(int(r["wins"]), int(r["plays"]))
            lines.append(
                f"`{r['target']}` — {r['plays']} plays · {sr} solved · avg {r['avg_guesses']:.1f} guesses"
            )
        embed.add_field(name="Most Played Words", value="\n".join(lines), inline=False)

    if top_starters:
        sw_text = "  ".join(f"`{w}` ×{n}" for w, n in top_starters[:8])
        embed.add_field(name="Most Common Openers", value=sw_text, inline=False)

    return embed


# ── History embed ─────────────────────────────────────────────────────────────

def history_embed(rows: list[dict], username: str) -> discord.Embed:
    embed = discord.Embed(title=f"📜 Recent Games — {username}", colour=GREY)

    if not rows:
        embed.description = "*No games played yet.*"
        return embed

    lines: list[str] = []
    for r in rows:
        status = "✅" if r["won"] else "❌"
        mode   = "📅" if r["mode"] == "daily" else "🎲"
        target = r["target"] if r["won"] else "?????"
        lines.append(
            f"{status} {mode} **{target}** — {r['num_guesses']} guess{'es' if r['num_guesses'] != 1 else ''} "
            f"| +{r['points']} pts | {r['game_date'] or r['played_at'][:10]}"
        )

    embed.description = "\n".join(lines)
    embed.set_footer(text="📅 = Daily  🎲 = Free Play")
    return embed


# ── Help embed ────────────────────────────────────────────────────────────────

def help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🟩 How to Play Sigmordle",
        colour=GREEN,
        description=(
            "Guess the hidden 5-letter word. After each guess you get colour feedback:\n\n"
            "🟩 **Green** — correct letter, correct position\n"
            "🟨 **Yellow** — correct letter, wrong position\n"
            "⬛ **Black** — letter not in the word\n\n"
            "**Daily mode** — one shared word per day (builds streaks & server stats)\n"
            "**Free play** — fresh random word anytime\n\n"
            "**Scoring** (base points per game):\n"
            "`1 guess` → **10 pts** · `2` → **7** · `3` → **5** · `4` → **3** · `5` → **2** · `6+` → **1**\n"
            "🔥 Daily win streaks award up to **+5 bonus points**\n\n"
            "**Entropy** shows how much information each guess reveals (in bits). "
            "Higher is better — a perfect guess could eliminate half the remaining words."
        ),
    )
    embed.add_field(
        name="Commands",
        value=(
            "`/wordle play [max_guesses] [mode]` — start a game\n"
            "`/wordle guess <word>` — submit a guess\n"
            "`/wordle board` — show your current board\n"
            "`/wordle giveup` — reveal the word and end\n"
            "`/wordle stats [user]` — your stats\n"
            "`/wordle leaderboard` — server leaderboard\n"
            "`/wordle daily` — today's server results\n"
            "`/wordle server` — server-wide stats\n"
            "`/wordle history` — your recent games\n"
            "`/wordle help` — this message"
        ),
        inline=False,
    )
    embed.set_footer(text="Sigmordle · Powered by py-cord")
    return embed
