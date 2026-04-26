import json
import aiosqlite
from pathlib import Path
from datetime import date as _date

DB_PATH = Path(__file__).parent.parent / "data" / "sigmordle.db"

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS wordle_games (
    game_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    guild_id    TEXT    NOT NULL,
    channel_id  TEXT    NOT NULL,
    target      TEXT    NOT NULL,
    guesses     TEXT    NOT NULL DEFAULT '[]',
    patterns    TEXT    NOT NULL DEFAULT '[]',
    entropy_log TEXT    NOT NULL DEFAULT '[]',
    status      TEXT    NOT NULL DEFAULT 'active',
    max_guesses INTEGER NOT NULL DEFAULT 6,
    mode        TEXT    NOT NULL DEFAULT 'daily',
    game_date   TEXT,
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_stats (
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
);

CREATE TABLE IF NOT EXISTS server_stats (
    guild_id          TEXT PRIMARY KEY,
    total_games       INTEGER DEFAULT 0,
    total_wins        INTEGER DEFAULT 0,
    server_streak     INTEGER DEFAULT 0,
    max_server_streak INTEGER DEFAULT 0,
    last_win_date     TEXT
);

CREATE TABLE IF NOT EXISTS game_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id         INTEGER,
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
    played_at       TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS guild_config (
    guild_id           TEXT PRIMARY KEY,
    timezone           TEXT NOT NULL DEFAULT 'UTC',
    last_reminder_date TEXT
);

CREATE INDEX IF NOT EXISTS idx_gh_guild_date ON game_history(guild_id, game_date);
CREATE INDEX IF NOT EXISTS idx_gh_user_guild ON game_history(user_id, guild_id);
CREATE INDEX IF NOT EXISTS idx_wg_user_guild ON wordle_games(user_id, guild_id, status);
"""

# Safe migrations for existing deployments
_MIGRATIONS = [
    ("user_stats",   "total_time_seconds", "INTEGER DEFAULT 0"),
    ("game_history", "elapsed_seconds",    "INTEGER DEFAULT 0"),
    ("game_history", "entropy_log",        "TEXT DEFAULT '[]'"),
    ("game_history", "mode",               "TEXT"),
    ("game_history", "game_date",          "TEXT"),
    ("wordle_games", "thread_id",          "TEXT"),
    ("wordle_games", "board_message_id",   "TEXT"),
]


async def _migrate(db) -> None:
    for table, column, col_def in _MIGRATIONS:
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        if column not in cols:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await _migrate(db)
        await db.commit()


# ── Active game ───────────────────────────────────────────────────────────────

async def get_active_game(user_id: str, guild_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM wordle_games WHERE user_id=? AND guild_id=? AND status='active' ORDER BY game_id DESC LIMIT 1",
            (user_id, guild_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def create_game(
    user_id: str, guild_id: str, channel_id: str, target: str,
    max_guesses: int, mode: str, game_date: str | None = None,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO wordle_games (user_id, guild_id, channel_id, target, max_guesses, mode, game_date) VALUES (?,?,?,?,?,?,?)",
            (user_id, guild_id, channel_id, target, max_guesses, mode, game_date),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]


async def update_thread_info(game_id: int, guild_id: str, thread_id: str, board_message_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE wordle_games SET thread_id=?, board_message_id=? WHERE game_id=? AND guild_id=?",
            (thread_id, board_message_id, game_id, guild_id),
        )
        await db.commit()


async def update_game(game_id: int, guild_id: str, guesses: str, patterns: str, entropy_log: str, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE wordle_games SET guesses=?, patterns=?, entropy_log=?, status=? WHERE game_id=? AND guild_id=?",
            (guesses, patterns, entropy_log, status, game_id, guild_id),
        )
        await db.commit()


# ── User stats ────────────────────────────────────────────────────────────────

async def get_user_stats(user_id: str, guild_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_stats WHERE user_id=? AND guild_id=?", (user_id, guild_id)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def upsert_user_stats(
    user_id: str, guild_id: str, username: str,
    won: bool, num_guesses: int, points: int,
    game_date: str, starting_word: str, elapsed_seconds: int = 0,
) -> int:
    """Update stats, return new current_streak."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_stats WHERE user_id=? AND guild_id=?", (user_id, guild_id)
        ) as cur:
            row = await cur.fetchone()

        if row:
            row           = dict(row)
            guess_dist    = json.loads(row["guess_dist"])
            starting_words = json.loads(row["starting_words"])
            games_played  = row["games_played"] + 1
            games_won     = row["games_won"] + (1 if won else 0)
            total_points  = row["total_points"] + points
            total_time    = row.get("total_time_seconds", 0) + (elapsed_seconds if won else 0)

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

            await db.execute(
                """UPDATE user_stats
                   SET username=?, games_played=?, games_won=?, current_streak=?, max_streak=?,
                       total_points=?, total_time_seconds=?, guess_dist=?, starting_words=?, last_game_date=?
                   WHERE user_id=? AND guild_id=?""",
                (username, games_played, games_won, streak, max_streak,
                 total_points, total_time, json.dumps(guess_dist), json.dumps(starting_words),
                 game_date, user_id, guild_id),
            )
        else:
            guess_dist = {str(num_guesses): 1} if won else {}
            streak     = 1 if won else 0
            total_time = elapsed_seconds if won else 0
            await db.execute(
                """INSERT INTO user_stats
                   (user_id, guild_id, username, games_played, games_won, current_streak,
                    max_streak, total_points, total_time_seconds, guess_dist, starting_words, last_game_date)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (user_id, guild_id, username, 1, 1 if won else 0, streak, streak,
                 points, total_time, json.dumps(guess_dist), json.dumps([starting_word]), game_date),
            )

        await db.commit()
        return streak


# ── Server stats ──────────────────────────────────────────────────────────────

async def update_server_stats(guild_id: str, won: bool, game_date: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM server_stats WHERE guild_id=?", (guild_id,)) as cur:
            row = await cur.fetchone()

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
            await db.execute(
                "UPDATE server_stats SET total_games=?, total_wins=?, server_streak=?, max_server_streak=?, last_win_date=? WHERE guild_id=?",
                (total_g, total_w, streak, max_s, last_win, guild_id),
            )
        else:
            await db.execute(
                "INSERT INTO server_stats (guild_id, total_games, total_wins, server_streak, max_server_streak, last_win_date) VALUES (?,?,?,?,?,?)",
                (guild_id, 1, 1 if won else 0, 1 if won else 0, 1 if won else 0, game_date if won else None),
            )
        await db.commit()


async def get_server_stats(guild_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM server_stats WHERE guild_id=?", (guild_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


# ── Game history ──────────────────────────────────────────────────────────────

async def add_history(
    game_id: int, user_id: str, guild_id: str, username: str,
    target: str, guesses: str, entropy_log: str,
    num_guesses: int, won: bool, points: int,
    elapsed_seconds: int, mode: str, game_date: str | None,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO game_history
               (game_id, user_id, guild_id, username, target, guesses, entropy_log,
                num_guesses, won, points, elapsed_seconds, mode, game_date)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (game_id, user_id, guild_id, username, target, guesses, entropy_log,
             num_guesses, int(won), points, elapsed_seconds, mode, game_date),
        )
        await db.commit()


async def check_daily_played(user_id: str, guild_id: str, game_date: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM game_history WHERE user_id=? AND guild_id=? AND game_date=? AND mode='daily'",
            (user_id, guild_id, game_date),
        ) as cur:
            return await cur.fetchone() is not None


async def get_daily_results(guild_id: str, game_date: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT username, num_guesses, won, points, guesses, entropy_log, elapsed_seconds
               FROM game_history
               WHERE guild_id=? AND game_date=? AND mode='daily'
               ORDER BY won DESC, num_guesses ASC, elapsed_seconds ASC""",
            (guild_id, game_date),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_leaderboard(guild_id: str, limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT username, total_points, games_played, games_won,
                      max_streak, current_streak, total_time_seconds
               FROM user_stats WHERE guild_id=?
               ORDER BY total_points DESC, total_time_seconds ASC LIMIT ?""",
            (guild_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_user_history(user_id: str, guild_id: str, limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT target, guesses, num_guesses, won, points, elapsed_seconds,
                      mode, game_date, played_at, entropy_log
               FROM game_history WHERE user_id=? AND guild_id=?
               ORDER BY played_at DESC LIMIT ?""",
            (user_id, guild_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_server_word_stats(guild_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT target, COUNT(*) AS plays, SUM(won) AS wins, AVG(num_guesses) AS avg_guesses
               FROM game_history WHERE guild_id=?
               GROUP BY target ORDER BY plays DESC LIMIT 15""",
            (guild_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_daily_played_with_stats(guild_id: str, game_date: str) -> list[dict]:
    """Daily game rows joined with current_streak from user_stats — used for reminders."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT gh.user_id, gh.username, gh.num_guesses, gh.won, gh.points, gh.elapsed_seconds,
                      COALESCE(us.current_streak, 0) AS current_streak
               FROM game_history gh
               LEFT JOIN user_stats us ON gh.user_id = us.user_id AND gh.guild_id = us.guild_id
               WHERE gh.guild_id=? AND gh.game_date=? AND gh.mode='daily'
               ORDER BY gh.won DESC, gh.num_guesses ASC, gh.elapsed_seconds ASC""",
            (guild_id, game_date),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_recent_daily_channel(guild_id: str) -> str | None:
    """Return the channel_id where the most recent daily game in this guild was started."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT channel_id FROM wordle_games WHERE guild_id=? AND mode='daily' ORDER BY game_id DESC LIMIT 1",
            (guild_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def get_top_starting_words(guild_id: str, limit: int = 10) -> list[tuple[str, int]]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT guesses FROM game_history WHERE guild_id=? ORDER BY id DESC LIMIT 2000",
            (guild_id,),
        ) as cur:
            rows = await cur.fetchall()

    from collections import Counter
    ctr: Counter = Counter()
    for (g_json,) in rows:
        try:
            guesses = json.loads(g_json)
            if guesses:
                ctr[guesses[0]] += 1
        except (json.JSONDecodeError, TypeError):
            pass
    return ctr.most_common(limit)


# ── Guild config ──────────────────────────────────────────────────────────────

async def get_guild_config(guild_id: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_config WHERE guild_id=?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return dict(row)
            return {"guild_id": guild_id, "timezone": "UTC", "last_reminder_date": None}


async def set_guild_timezone(guild_id: str, tz_name: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO guild_config (guild_id, timezone) VALUES (?,?) "
            "ON CONFLICT(guild_id) DO UPDATE SET timezone=excluded.timezone",
            (guild_id, tz_name),
        )
        await db.commit()


async def mark_reminder_sent(guild_id: str, date_str: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO guild_config (guild_id, last_reminder_date) VALUES (?,?) "
            "ON CONFLICT(guild_id) DO UPDATE SET last_reminder_date=excluded.last_reminder_date",
            (guild_id, date_str),
        )
        await db.commit()


async def get_daily_players_for_reminder(guild_id: str) -> list[dict]:
    """All users who have ever played daily mode, ordered by current streak desc."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT us.user_id, us.username, us.current_streak, us.total_points,
                      us.games_played, us.games_won
               FROM user_stats us
               WHERE us.guild_id=?
               AND EXISTS (
                   SELECT 1 FROM game_history gh
                   WHERE gh.user_id=us.user_id AND gh.guild_id=us.guild_id AND gh.mode='daily'
               )
               ORDER BY us.current_streak DESC, us.total_points DESC""",
            (guild_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]
