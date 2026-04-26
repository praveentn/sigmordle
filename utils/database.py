"""
Sigmordle — PostgreSQL async database layer (asyncpg).

Connection pooling via a module-level pool created on first use.
All SQL uses $1,$2,... positional placeholders (asyncpg style).
"""

import json
import os
from datetime import date as _date

import asyncpg

DATABASE_URL: str = os.environ.get("DATABASE_URL", "")

_pool: asyncpg.Pool | None = None

# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA_STMTS = [
    """CREATE TABLE IF NOT EXISTS wordle_games (
        game_id          BIGSERIAL PRIMARY KEY,
        user_id          TEXT    NOT NULL,
        guild_id         TEXT    NOT NULL,
        channel_id       TEXT    NOT NULL,
        target           TEXT    NOT NULL,
        guesses          TEXT    NOT NULL DEFAULT '[]',
        patterns         TEXT    NOT NULL DEFAULT '[]',
        entropy_log      TEXT    NOT NULL DEFAULT '[]',
        status           TEXT    NOT NULL DEFAULT 'active',
        max_guesses      INTEGER NOT NULL DEFAULT 6,
        mode             TEXT    NOT NULL DEFAULT 'daily',
        game_date        TEXT,
        created_at       TEXT    DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'),
        thread_id        TEXT,
        board_message_id TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS user_stats (
        user_id             TEXT NOT NULL,
        guild_id            TEXT NOT NULL,
        username            TEXT,
        games_played        INTEGER DEFAULT 0,
        games_won           INTEGER DEFAULT 0,
        current_streak      INTEGER DEFAULT 0,
        max_streak          INTEGER DEFAULT 0,
        total_points        INTEGER DEFAULT 0,
        total_time_seconds  INTEGER DEFAULT 0,
        guess_dist          TEXT    DEFAULT '{}',
        starting_words      TEXT    DEFAULT '[]',
        last_game_date      TEXT,
        PRIMARY KEY (user_id, guild_id)
    )""",
    """CREATE TABLE IF NOT EXISTS server_stats (
        guild_id          TEXT PRIMARY KEY,
        total_games       INTEGER DEFAULT 0,
        total_wins        INTEGER DEFAULT 0,
        server_streak     INTEGER DEFAULT 0,
        max_server_streak INTEGER DEFAULT 0,
        last_win_date     TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS game_history (
        id              BIGSERIAL PRIMARY KEY,
        game_id         BIGINT,
        user_id         TEXT,
        guild_id        TEXT,
        username        TEXT,
        target          TEXT,
        guesses         TEXT,
        entropy_log     TEXT    DEFAULT '[]',
        num_guesses     INTEGER,
        won             INTEGER,
        points          INTEGER,
        elapsed_seconds INTEGER DEFAULT 0,
        mode            TEXT,
        game_date       TEXT,
        played_at       TEXT    DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS')
    )""",
    """CREATE TABLE IF NOT EXISTS guild_config (
        guild_id           TEXT PRIMARY KEY,
        timezone           TEXT NOT NULL DEFAULT 'UTC',
        last_reminder_date TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_gh_guild_date ON game_history(guild_id, game_date)",
    "CREATE INDEX IF NOT EXISTS idx_gh_user_guild ON game_history(user_id, guild_id)",
    "CREATE INDEX IF NOT EXISTS idx_wg_user_guild ON wordle_games(user_id, guild_id, status)",
]

# Future column additions go here — (table, column, col_def)
_MIGRATIONS: list[tuple[str, str, str]] = [
    ("guild_config", "reminder_channel_id", "TEXT"),
    ("guild_config", "reminder_enabled",    "INTEGER NOT NULL DEFAULT 0"),
]


# ── Pool + init ───────────────────────────────────────────────────────────────

async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10)
    return _pool


async def _migrate(conn: asyncpg.Connection) -> None:
    for table, column, col_def in _MIGRATIONS:
        row = await conn.fetchrow(
            "SELECT 1 FROM information_schema.columns WHERE table_name=$1 AND column_name=$2",
            table, column,
        )
        if not row:
            await conn.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_def}"
            )


async def init_db() -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for stmt in _SCHEMA_STMTS:
                await conn.execute(stmt)
            await _migrate(conn)


# ── Active game ───────────────────────────────────────────────────────────────

async def get_active_game(user_id: str, guild_id: str) -> dict | None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM wordle_games "
            "WHERE user_id=$1 AND guild_id=$2 AND status='active' "
            "ORDER BY game_id DESC LIMIT 1",
            user_id, guild_id,
        )
        return dict(row) if row else None


async def create_game(
    user_id: str, guild_id: str, channel_id: str, target: str,
    max_guesses: int, mode: str, game_date: str | None = None,
) -> int:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        game_id = await conn.fetchval(
            "INSERT INTO wordle_games "
            "(user_id, guild_id, channel_id, target, max_guesses, mode, game_date) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING game_id",
            user_id, guild_id, channel_id, target, max_guesses, mode, game_date,
        )
        return game_id  # type: ignore[return-value]


async def update_thread_info(
    game_id: int, guild_id: str, thread_id: str, board_message_id: str
) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE wordle_games SET thread_id=$1, board_message_id=$2 "
            "WHERE game_id=$3 AND guild_id=$4",
            thread_id, board_message_id, game_id, guild_id,
        )


async def update_game(
    game_id: int, guild_id: str, guesses: str, patterns: str,
    entropy_log: str, status: str,
) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE wordle_games SET guesses=$1, patterns=$2, entropy_log=$3, status=$4 "
            "WHERE game_id=$5 AND guild_id=$6",
            guesses, patterns, entropy_log, status, game_id, guild_id,
        )


# ── User stats ────────────────────────────────────────────────────────────────

async def get_user_stats(user_id: str, guild_id: str) -> dict | None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM user_stats WHERE user_id=$1 AND guild_id=$2",
            user_id, guild_id,
        )
        return dict(row) if row else None


async def upsert_user_stats(
    user_id: str, guild_id: str, username: str,
    won: bool, num_guesses: int, points: int,
    game_date: str, starting_word: str, elapsed_seconds: int = 0,
) -> int:
    """Update stats, return new current_streak."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM user_stats WHERE user_id=$1 AND guild_id=$2",
                user_id, guild_id,
            )

            if row:
                row            = dict(row)
                guess_dist     = json.loads(row["guess_dist"])
                starting_words = json.loads(row["starting_words"])
                games_played   = row["games_played"] + 1
                games_won      = row["games_won"] + (1 if won else 0)
                total_points   = row["total_points"] + points
                total_time     = (row.get("total_time_seconds") or 0) + (elapsed_seconds if won else 0)

                last_date = row["last_game_date"]
                streak    = row["current_streak"]
                if won:
                    if last_date:
                        delta  = (_date.fromisoformat(game_date) - _date.fromisoformat(last_date)).days
                        streak = (streak + 1) if delta == 1 else 1
                    else:
                        streak = 1
                else:
                    streak = 0

                max_streak = max(row["max_streak"], streak)
                if won:
                    guess_dist[str(num_guesses)] = guess_dist.get(str(num_guesses), 0) + 1
                starting_words = (starting_words + [starting_word])[-100:]

                await conn.execute(
                    """UPDATE user_stats
                       SET username=$1, games_played=$2, games_won=$3, current_streak=$4,
                           max_streak=$5, total_points=$6, total_time_seconds=$7,
                           guess_dist=$8, starting_words=$9, last_game_date=$10
                       WHERE user_id=$11 AND guild_id=$12""",
                    username, games_played, games_won, streak, max_streak,
                    total_points, total_time, json.dumps(guess_dist),
                    json.dumps(starting_words), game_date, user_id, guild_id,
                )
            else:
                guess_dist = {str(num_guesses): 1} if won else {}
                streak     = 1 if won else 0
                total_time = elapsed_seconds if won else 0
                await conn.execute(
                    """INSERT INTO user_stats
                       (user_id, guild_id, username, games_played, games_won, current_streak,
                        max_streak, total_points, total_time_seconds, guess_dist,
                        starting_words, last_game_date)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)""",
                    user_id, guild_id, username, 1, 1 if won else 0, streak, streak,
                    points, total_time, json.dumps(guess_dist),
                    json.dumps([starting_word]), game_date,
                )

            return streak


# ── Server stats ──────────────────────────────────────────────────────────────

async def update_server_stats(guild_id: str, won: bool, game_date: str) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM server_stats WHERE guild_id=$1", guild_id
            )

            if row:
                row      = dict(row)
                total_g  = row["total_games"] + 1
                total_w  = row["total_wins"] + (1 if won else 0)
                streak   = row["server_streak"]
                last_win = row["last_win_date"]
                if won:
                    if last_win:
                        delta  = (_date.fromisoformat(game_date) - _date.fromisoformat(last_win)).days
                        streak = (streak + 1) if delta <= 1 else 1
                    else:
                        streak = 1
                    last_win = game_date
                else:
                    streak = 0
                max_s = max(row["max_server_streak"], streak)
                await conn.execute(
                    "UPDATE server_stats "
                    "SET total_games=$1, total_wins=$2, server_streak=$3, "
                    "    max_server_streak=$4, last_win_date=$5 "
                    "WHERE guild_id=$6",
                    total_g, total_w, streak, max_s, last_win, guild_id,
                )
            else:
                await conn.execute(
                    "INSERT INTO server_stats "
                    "(guild_id, total_games, total_wins, server_streak, max_server_streak, last_win_date) "
                    "VALUES ($1,$2,$3,$4,$5,$6)",
                    guild_id, 1, 1 if won else 0, 1 if won else 0,
                    1 if won else 0, game_date if won else None,
                )


async def get_server_stats(guild_id: str) -> dict | None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM server_stats WHERE guild_id=$1", guild_id
        )
        return dict(row) if row else None


# ── Game history ──────────────────────────────────────────────────────────────

async def add_history(
    game_id: int, user_id: str, guild_id: str, username: str,
    target: str, guesses: str, entropy_log: str,
    num_guesses: int, won: bool, points: int,
    elapsed_seconds: int, mode: str, game_date: str | None,
) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO game_history
               (game_id, user_id, guild_id, username, target, guesses, entropy_log,
                num_guesses, won, points, elapsed_seconds, mode, game_date)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
            game_id, user_id, guild_id, username, target, guesses, entropy_log,
            num_guesses, int(won), points, elapsed_seconds, mode, game_date,
        )


async def check_daily_played(user_id: str, guild_id: str, game_date: str) -> bool:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM game_history "
            "WHERE user_id=$1 AND guild_id=$2 AND game_date=$3 AND mode='daily'",
            user_id, guild_id, game_date,
        )
        return row is not None


async def get_daily_results(guild_id: str, game_date: str) -> list[dict]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT username, num_guesses, won, points, guesses, entropy_log, elapsed_seconds
               FROM game_history
               WHERE guild_id=$1 AND game_date=$2 AND mode='daily'
               ORDER BY won DESC, num_guesses ASC, elapsed_seconds ASC""",
            guild_id, game_date,
        )
        return [dict(r) for r in rows]


async def get_leaderboard(guild_id: str, limit: int = 10) -> list[dict]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT username, total_points, games_played, games_won,
                      max_streak, current_streak, total_time_seconds
               FROM user_stats WHERE guild_id=$1
               ORDER BY total_points DESC, total_time_seconds ASC LIMIT $2""",
            guild_id, limit,
        )
        return [dict(r) for r in rows]


async def get_user_history(user_id: str, guild_id: str, limit: int = 10) -> list[dict]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT target, guesses, num_guesses, won, points, elapsed_seconds,
                      mode, game_date, played_at, entropy_log
               FROM game_history WHERE user_id=$1 AND guild_id=$2
               ORDER BY played_at DESC LIMIT $3""",
            user_id, guild_id, limit,
        )
        return [dict(r) for r in rows]


async def get_server_word_stats(guild_id: str) -> list[dict]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT target, COUNT(*) AS plays, SUM(won) AS wins, AVG(num_guesses) AS avg_guesses
               FROM game_history WHERE guild_id=$1
               GROUP BY target ORDER BY plays DESC LIMIT 15""",
            guild_id,
        )
        return [dict(r) for r in rows]


async def get_daily_played_with_stats(guild_id: str, game_date: str) -> list[dict]:
    """Daily game rows joined with current_streak from user_stats — used for reminders."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT gh.user_id, gh.username, gh.num_guesses, gh.won, gh.points, gh.elapsed_seconds,
                      COALESCE(us.current_streak, 0) AS current_streak
               FROM game_history gh
               LEFT JOIN user_stats us ON gh.user_id = us.user_id AND gh.guild_id = us.guild_id
               WHERE gh.guild_id=$1 AND gh.game_date=$2 AND gh.mode='daily'
               ORDER BY gh.won DESC, gh.num_guesses ASC, gh.elapsed_seconds ASC""",
            guild_id, game_date,
        )
        return [dict(r) for r in rows]


async def get_recent_daily_channel(guild_id: str) -> str | None:
    """Return the channel_id where the most recent daily game in this guild was started."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT channel_id FROM wordle_games "
            "WHERE guild_id=$1 AND mode='daily' ORDER BY game_id DESC LIMIT 1",
            guild_id,
        )


async def get_top_starting_words(guild_id: str, limit: int = 10) -> list[tuple[str, int]]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT guesses FROM game_history WHERE guild_id=$1 ORDER BY id DESC LIMIT 2000",
            guild_id,
        )

    from collections import Counter
    ctr: Counter = Counter()
    for row in rows:
        try:
            guesses = json.loads(row["guesses"])
            if guesses:
                ctr[guesses[0]] += 1
        except (json.JSONDecodeError, TypeError):
            pass
    return ctr.most_common(limit)


# ── Guild config ──────────────────────────────────────────────────────────────

async def get_guild_config(guild_id: str) -> dict:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM guild_config WHERE guild_id=$1", guild_id
        )
        if row:
            return dict(row)
        return {
            "guild_id": guild_id, "timezone": "UTC",
            "last_reminder_date": None,
            "reminder_channel_id": None, "reminder_enabled": 0,
        }


async def set_guild_timezone(guild_id: str, tz_name: str) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO guild_config (guild_id, timezone) VALUES ($1,$2) "
            "ON CONFLICT (guild_id) DO UPDATE SET timezone=EXCLUDED.timezone",
            guild_id, tz_name,
        )


async def set_reminder_channel(guild_id: str, channel_id: str) -> None:
    """Set the target channel and enable reminders (first-time setup or re-configure)."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO guild_config (guild_id, reminder_channel_id, reminder_enabled) VALUES ($1,$2,1) "
            "ON CONFLICT (guild_id) DO UPDATE SET "
            "reminder_channel_id=EXCLUDED.reminder_channel_id, reminder_enabled=1",
            guild_id, channel_id,
        )


async def set_reminder_enabled(guild_id: str, enabled: bool) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO guild_config (guild_id, reminder_enabled) VALUES ($1,$2) "
            "ON CONFLICT (guild_id) DO UPDATE SET reminder_enabled=EXCLUDED.reminder_enabled",
            guild_id, 1 if enabled else 0,
        )


async def mark_reminder_sent(guild_id: str, date_str: str) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO guild_config (guild_id, last_reminder_date) VALUES ($1,$2) "
            "ON CONFLICT (guild_id) DO UPDATE SET last_reminder_date=EXCLUDED.last_reminder_date",
            guild_id, date_str,
        )


async def get_daily_players_for_reminder(guild_id: str) -> list[dict]:
    """All users who have ever played daily mode, ordered by current streak desc."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT us.user_id, us.username, us.current_streak, us.total_points,
                      us.games_played, us.games_won
               FROM user_stats us
               WHERE us.guild_id=$1
               AND EXISTS (
                   SELECT 1 FROM game_history gh
                   WHERE gh.user_id=us.user_id AND gh.guild_id=us.guild_id AND gh.mode='daily'
               )
               ORDER BY us.current_streak DESC, us.total_points DESC""",
            guild_id,
        )
        return [dict(r) for r in rows]
