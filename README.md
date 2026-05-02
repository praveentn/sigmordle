# Sigmordle

A Discord bot that brings Wordle-style word guessing to your server — with entropy analytics, streaks, server leaderboards, daily reminders, and PNG board rendering.

![Sigmordle banner](sigmordle-banner.png)

---

## Features

### Gameplay
- **Private thread per game** — each game lives in its own Discord thread; type guesses as plain messages
- **Two modes** — Daily (same word for everyone, synced to UTC) and Free Play (random word, unlimited)
- **Configurable difficulty** — 1 to 10 max guesses per game
- **Visual board** — every guess renders a live PNG board with coloured tiles and an on-screen QWERTY keyboard
- **Give Up button** — persistent button on the board to forfeit and reveal the answer

### Analytics
- **Entropy tracking** — every guess logs expected bits vs. actual bits gained, showing how lucky or efficient each move was
- **Guess distribution** — bar chart of your solve-turn breakdown across all games
- **Favourite openers** — tracks your most-used starting words and their average effectiveness

### Stats & Leaderboards
- **Personal stats** — games played, win rate, points, current/max streak, average solve time
- **Server leaderboard** — top players ranked by total points, with win rate and average solve time
- **Daily results** — see every player's attempt on today's word (word hidden until tomorrow)
- **Server stats** — aggregate wins, server streak, most-played words, top opening words

### Reminders
- **Daily midnight reminders** — fire at midnight in the server's configured timezone
- **Reminder embeds** — streaks, word-of-the-day fact, leaderboard snapshot
- **Action buttons** on every reminder: Play Daily · Free Play · Leaderboard · My Stats · Daily Results
- **All players tagged** so no one misses the daily

### External Leaderboards
- Fire-and-forget integration to one or more external leaderboard APIs whenever a player earns points
- Fully configuration-driven — add or remove services with env vars, no code changes needed

---

## Tech Stack

| Layer | Technology |
|---|---|
| Discord framework | [py-cord](https://github.com/Pycord-Development/pycord) ≥ 2.6 |
| Database | PostgreSQL via [asyncpg](https://github.com/MagicStack/asyncpg) |
| HTTP / external APIs | [aiohttp](https://docs.aiohttp.org/) |
| Image rendering | [Pillow](https://python-pillow.org/) |
| Environment config | [python-dotenv](https://github.com/theskumar/python-dotenv) |
| Runtime | Python 3.10+ |

---

## Documentation

| Guide | Contents |
|---|---|
| [README_SETUP.md](README_SETUP.md) | Discord app creation, permissions, environment variables, database setup, hosting |
| [README_PLAY.md](README_PLAY.md) | Gameplay, commands, scoring, entropy, reminders, tips |

---

## Quick Start

```bash
# macOS / Linux
./start.sh

# Windows
start.bat
```

Both scripts create a virtual environment, install dependencies, and start the bot.
Open `http://localhost:8080/` for the status dashboard.

See [README_SETUP.md](README_SETUP.md) for full configuration instructions.
