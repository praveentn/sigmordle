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
BLUE   = discord.Colour(0x5865F2)
GREY   = discord.Colour.greyple()
ORANGE = discord.Colour.orange()

_TILE   = {CORRECT: "🟩", PRESENT: "🟨", ABSENT: "⬛"}
_NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
_BAR_WIDTH = 12
_MAX_BITS  = math.log2(5917)   # ~12.5 — theoretical max for our word list


def _medal(rank: int) -> str:
    return ["🥇", "🥈", "🥉"][rank] if rank < 3 else f"**{rank + 1}.**"


def _pct(num: int, denom: int) -> str:
    return "0%" if denom == 0 else f"{round(100 * num / denom)}%"


def _fmt_time(seconds: int) -> str:
    if seconds <= 0:
        return "—"
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


def _bar(count: int, max_count: int, width: int = 12) -> str:
    filled = round(width * count / max_count) if max_count else 0
    return "█" * filled + "░" * (width - filled)


def _row_num(i: int) -> str:
    return _NUM_EMOJI[i] if i < len(_NUM_EMOJI) else f"`{i + 1}.`"


def _entropy_bar(bits: float) -> str:
    filled = round(_BAR_WIDTH * min(bits, _MAX_BITS) / _MAX_BITS)
    return "█" * filled + "░" * (_BAR_WIDTH - filled)


# ── Board renderer ────────────────────────────────────────────────────────────

def render_board(game: WordleGame) -> str:
    """Each guessed row shows colored square + letter per tile: 🟩C 🟨R ⬛A ⬛N ⬛E"""
    lines: list[str] = []
    for i, (guess, pat) in enumerate(zip(game.guesses, game.patterns)):
        tiles = " ".join(f"{_TILE[p]}{ch}" for p, ch in zip(pat, guess))
        lines.append(f"{_row_num(i)} {tiles}")
    for i in range(len(game.guesses), game.max_guesses):
        lines.append(f"{_row_num(i)} " + " ".join(["⬜"] * 5))
    return "\n".join(lines)


# ── Letter-state renderer ─────────────────────────────────────────────────────

def render_keyboard(game: WordleGame) -> str:
    """
    Row 1: 5-slot position view — known letters in their exact position, ⬜ for unknowns.
    Row 2: 🟨-prefixed letters found but wrong position, in word-discovery order.
    Row 3: ⬛-prefixed letters confirmed absent, alphabetical.
    Row 4: untried letters in italic, alphabetical.
    """
    correct_slots, present_ordered, absent_sorted, untried_sorted = build_keyboard_lines(
        game.guesses, game.patterns
    )

    lines: list[str] = []

    # Position row — always shown
    pos = " ".join(f"🟩{ch}" if ch else "⬜" for ch in correct_slots)
    lines.append(pos)

    if present_ordered:
        lines.append(" ".join(f"🟨{ch}" for ch in present_ordered))

    if absent_sorted:
        lines.append(" ".join(f"⬛{ch}" for ch in absent_sorted))

    if untried_sorted:
        lines.append(f"_{' '.join(untried_sorted)}_")

    return "\n".join(lines)


# ── Entropy renderer ──────────────────────────────────────────────────────────

def render_entropy(log: list[EntropyEntry]) -> str:
    """
    Per-guess row: word  quality-emoji  progress-bar  actual-bits  word-count-narrowing
    🟢 actual ≥ expected + 0.2  (above average)
    🟡 within ±0.5 of expected  (on par)
    🔴 actual ≤ expected − 0.5  (below average / unlucky)
    """
    if not log:
        return "*No guesses yet*"

    lines: list[str] = []
    for i, e in enumerate(log):
        bar   = _entropy_bar(e.actual_bits)
        delta = e.actual_bits - e.expected_bits
        quality = "🟢" if delta >= 0.2 else ("🔴" if delta <= -0.5 else "🟡")
        lines.append(
            f"{_row_num(i)} `{e.guess}` {quality} `{bar}`"
            f" **{e.actual_bits:.1f}b**  _{e.n_before:,}→{e.n_after:,}_"
        )

    total_actual   = sum(e.actual_bits for e in log)
    total_expected = sum(e.expected_bits for e in log)
    remaining      = log[-1].n_after
    sep = "`" + "━" * (_BAR_WIDTH + 6) + "`"
    lines.append(sep)
    lines.append(
        f"**{total_actual:.1f}b** _(exp {total_expected:.1f})_"
        f"  ·  **{remaining:,}** word{'s' if remaining != 1 else ''} left"
    )
    return "\n".join(lines)


# ── Main game embed ───────────────────────────────────────────────────────────

def game_embed(game: WordleGame, username: str) -> discord.Embed:
    if game.is_won:
        colour = GREEN
        title  = f"🎉 Solved in {game.num_guesses}/{game.max_guesses}!"
    elif game.is_lost:
        colour = RED
        title  = f"😔 The word was **{game.target}**"
    else:
        left  = game.remaining_guesses
        colour = BLUE
        title  = f"🟩 Sigmordle — {left} guess{'es' if left != 1 else ''} left"

    mode_tag = "📅 Daily" if game.mode == "daily" else "🎲 Free Play"
    embed = discord.Embed(title=title, colour=colour)
    embed.set_author(name=f"{username} · {mode_tag}")

    # Board is rendered as an attached PNG — reference it via attachment URL.
    embed.set_image(url="attachment://board.png")
    embed.add_field(name="Letters", value=render_keyboard(game), inline=False)

    if game.entropy_log:
        embed.add_field(
            name="📐 Entropy",
            value=render_entropy(game.entropy_log),
            inline=False,
        )

    if game.is_won:
        embed.set_footer(text=f"Game #{game.game_id} · {game.mode}")
    elif game.is_lost:
        embed.set_footer(text=f"Game #{game.game_id} — try /wordle play again!")
    else:
        embed.set_footer(text=f"Game #{game.game_id} · {game.mode} · Type your 5-letter guess in this thread")

    return embed


# ── Stats embed ───────────────────────────────────────────────────────────────

def stats_embed(row: dict, username: str) -> discord.Embed:
    played  = row["games_played"]
    won     = row["games_won"]
    pts     = row["total_points"]
    streak  = row["current_streak"]
    max_str = row["max_streak"]
    dist    = json.loads(row["guess_dist"])
    sw_raw  = json.loads(row["starting_words"])

    win_rate = _pct(won, played)
    avg_pts  = f"{pts / played:.1f}" if played else "0"

    total_time = row.get("total_time_seconds", 0) or 0
    avg_time   = _fmt_time(total_time // won) if won else "—"

    embed = discord.Embed(title=f"📊 Stats — {username}", colour=BLUE)
    overview = (
        f"🎮 Games Played: **{played}**\n"
        f"✅ Won: **{won}** ({win_rate})\n"
        f"🏆 Total Points: **{pts}** (avg {avg_pts}/game)\n"
        f"🔥 Current Streak: **{streak}**  |  Best: **{max_str}**\n"
        f"⏱ Avg Solve Time: **{avg_time}**"
    )
    embed.add_field(name="Overview", value=overview, inline=False)

    if dist:
        max_count = max(dist.values(), default=1)
        bars: list[str] = []
        for g in sorted(dist.keys(), key=int):
            c   = dist[g]
            bar = _bar(c, max_count)
            bars.append(f"`{g}` {bar} {c}")
        embed.add_field(name="Guess Distribution", value="\n".join(bars), inline=False)

    if sw_raw:
        ctr   = Counter(sw_raw)
        top5  = ctr.most_common(5)
        sw_text = "  ".join(f"`{w}` ×{n}" for w, n in top5)
        embed.add_field(name="Favourite Openers", value=sw_text, inline=False)

    embed.set_footer(text="Use /wordle leaderboard to compare with the server")
    return embed


# ── Leaderboard embed ─────────────────────────────────────────────────────────

def leaderboard_embed(rows: list[dict], guild_name: str) -> discord.Embed:
    embed = discord.Embed(title=f"🏆 Leaderboard — {guild_name}", colour=ORANGE)

    if not rows:
        embed.description = "*No games played yet. Use `/wordle play` to start!*"
        return embed

    lines: list[str] = []
    for i, r in enumerate(rows):
        name       = r["username"] or "Unknown"
        pts        = r["total_points"]
        played     = r["games_played"]
        won        = r["games_won"]
        wr         = _pct(won, played)
        streak     = r["current_streak"]
        total_time = r.get("total_time_seconds") or 0
        avg_time   = _fmt_time(total_time // won) if won else "—"
        streak_tag = f" 🔥{streak}" if streak >= 3 else ""
        lines.append(
            f"{_medal(i)} **{name}** — **{pts} pts**  ·  {wr} WR  ·  ⏱ {avg_time}{streak_tag}"
        )

    embed.description = "\n".join(lines)
    embed.set_footer(text="Points: 1 guess=10 · 2=7 · 3=5 · 4=3 · 5=2 · 6=1 + streak bonus  ·  tiebreak: fastest avg")
    return embed


# ── Daily results embed ───────────────────────────────────────────────────────

def daily_results_embed(
    rows: list[dict],
    word: str,
    guild_name: str,
    game_date: str,
    show_word: bool = True,
) -> discord.Embed:
    embed = discord.Embed(title=f"📅 Daily Results — {game_date}", colour=BLUE)

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
            t   = _fmt_time(r.get("elapsed_seconds") or 0)
            lines.append(
                f"{_medal(i)} **{r['username']}** — {g} guess{'es' if g != 1 else ''}"
                f"  (+{pts} pts)  ⏱ {t}"
            )
        embed.add_field(name=f"✅ Solved ({len(solved)})", value="\n".join(lines[:15]), inline=False)

    if failed:
        fail_names = ", ".join(r["username"] for r in failed[:10])
        embed.add_field(name=f"❌ Did Not Solve ({len(failed)})", value=fail_names or "—", inline=False)

    all_elog = []
    for r in rows:
        try:
            elog = json.loads(r.get("entropy_log") or "[]")
            all_elog.append(elog)
        except Exception:
            pass

    if len(all_elog) >= 2:
        g1_bits = [e[0]["actual_bits"] for e in all_elog if e]
        if g1_bits:
            avg_g1 = sum(g1_bits) / len(g1_bits)
            best   = max(g1_bits)
            embed.add_field(
                name="📐 Opening Entropy (Guess 1)",
                value=(
                    f"Server avg: **{avg_g1:.2f}b**  ·  "
                    f"Best: **{best:.2f}b**  ·  "
                    f"Players: **{len(g1_bits)}**"
                ),
                inline=False,
            )

    total_played = len(rows)
    avg_guesses  = sum(r["num_guesses"] for r in solved) / len(solved) if solved else 0
    embed.set_footer(
        text=(
            f"{guild_name} · {total_played} played · {len(solved)} solved · "
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

    if not stats:
        embed.description = "*No games yet! Use `/wordle play` to start.*"
        return embed

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
        status  = "✅" if r["won"] else "❌"
        mode    = "📅" if r["mode"] == "daily" else "🎲"
        target  = r["target"] if r["won"] else "?????"
        elapsed = _fmt_time(r.get("elapsed_seconds") or 0)
        lines.append(
            f"{status} {mode} **{target}** — {r['num_guesses']} guess{'es' if r['num_guesses'] != 1 else ''}"
            f" | +{r['points']} pts | ⏱ {elapsed} | {r['game_date'] or r['played_at'][:10]}"
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
            "🟩**C** — correct letter, correct position\n"
            "🟨**R** — correct letter, wrong position\n"
            "⬛**N** — letter not in the word\n\n"
            "**Daily mode** — one shared word per day (builds streaks & server stats)\n"
            "**Free play** — fresh random word anytime\n\n"
            "**Scoring** (base points per game):\n"
            "`1 guess` → **10 pts** · `2` → **7** · `3` → **5** · `4` → **3** · `5` → **2** · `6+` → **1**\n"
            "🔥 Daily win streaks award up to **+5 bonus points**\n\n"
            "**Entropy** 📐 — bits of information each guess reveals. "
            "🟢 above expected · 🟡 on par · 🔴 below expected."
        ),
    )
    embed.add_field(
        name="Commands",
        value=(
            "`/wordle play [max_guesses] [mode]` — start a game\n"
            "`/wordle guess <word>` — submit a guess (slash fallback)\n"
            "`/wordle board` — find your active game thread\n"
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
    embed.add_field(
        name="How to play",
        value=(
            "Run `/wordle play` — a **private thread** opens just for you.\n"
            "Type your 5-letter word directly in the thread.\n"
            "The board updates automatically after each guess.\n"
            "Click **🏳️ Give Up** to reveal the word and forfeit."
        ),
        inline=False,
    )
    embed.set_footer(text="Sigmordle · Powered by py-cord")
    return embed
