# Sigmordle — Setup Guide

## Requirements

- Python 3.10+
- A PostgreSQL database (local or hosted)
- A Discord application with a bot token

---

## 1. Create a Discord Application

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application** → name it `Sigmordle` → **Create**

## 2. Add a Bot

1. Left sidebar → **Bot**
2. Click **Add Bot** → **Yes, do it!**
3. Under **Token** click **Reset Token** → copy it (needed in step 5)
4. Enable all **Privileged Gateway Intents**:
   - ✅ **Message Content Intent** — reads plain-text guesses typed in threads
   - ✅ **Server Members Intent** — needed for `/wordle stats @user` lookups

## 3. Set Bot Permissions

In the **Bot** tab under **Bot Permissions**, enable:

| Permission | Why needed |
|---|---|
| Read Messages / View Channels | See channels and threads |
| Send Messages | Reply to commands |
| Send Messages in Threads | Post board updates inside game threads |
| Create Public Threads | Open a thread per game |
| Manage Threads | Archive threads when a game ends |
| Manage Messages | Delete the player's guess message (keeps threads clean) |
| Embed Links | Render rich embeds |
| Attach Files | Upload the PNG board image |
| Use Slash Commands | Application commands |
| Read Message History | Context for commands |
| Mention Everyone | Tag all players in daily reminders |

## 4. Generate an OAuth2 Invite URL

1. Left sidebar → **OAuth2** → **URL Generator**
2. Under **Scopes** tick: ✅ `bot` · ✅ `applications.commands`
3. Under **Bot Permissions** tick all permissions from step 3
4. Copy the generated URL

**Manual invite URL:**
```
https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&scope=bot+applications.commands&permissions=397553427520
```
Replace `YOUR_CLIENT_ID` with the Application ID from the **General Information** page.

## 5. Set Up PostgreSQL

Sigmordle requires a PostgreSQL database. The schema is created automatically on first startup — you just need a connection URL.

**Hosted options (free tier available):** Railway · Supabase · Neon · Render

Connection string format:
```
postgresql://USER:PASSWORD@HOST:PORT/DATABASE
```

## 6. Configure `.env`

Create a `.env` file in the project root:

```env
# ── Required ──────────────────────────────────────────────────────────────────
DISCORD_TOKEN=paste_your_bot_token_here
DATABASE_URL=postgresql://user:password@host:5432/dbname

# ── Optional: dev / debug ──────────────────────────────────────────────────────
# Guild-scoped commands register instantly (single server only).
# Leave unset for global registration (up to 1 hour propagation).
DISCORD_GUILD_ID=your_server_id_here

# HTTP status dashboard port (default 8080)
PORT=8080

# ── Optional: external leaderboard integration ─────────────────────────────────
# Comma-separated list of service names. For each name FOO, set FOO_ENABLED,
# FOO_URL, and FOO_API_KEY. Disabled services are silently skipped.
EXTERNAL_LEADERBOARDS=SIGMAFEUD,NAVI

SIGMAFEUD_ENABLED=true
SIGMAFEUD_URL=https://sigmafeud-production.up.railway.app
SIGMAFEUD_API_KEY=your_sigmafeud_key_here

NAVI_ENABLED=false
NAVI_URL=https://navi.example.com
NAVI_API_KEY=your_navi_key_here
```

**How to find your Server ID:** Right-click your server icon in Discord → **Copy Server ID**
(requires Developer Mode: User Settings → Advanced → Developer Mode).

## 7. Install & Run

### macOS / Linux
```bash
./start.sh
```

### Windows
```bat
start.bat
```

Both scripts will:
1. Create a Python virtual environment in `venv/`
2. Install all dependencies from `requirements.txt`
3. Start the bot
4. Open a status dashboard at `http://localhost:8080/`

### Manual (any OS)
```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

On first startup the bot creates all database tables automatically. You should see `DB initialised.` in the logs.

## 8. Invite the Bot to Your Server

Paste the invite URL from step 4 into a browser and select your server.

## 9. Slash Command Registration

| Mode | How | Wait time |
|---|---|---|
| `DISCORD_GUILD_ID` set | Guild-scoped, instant | None |
| `DISCORD_GUILD_ID` unset | Global registration | Up to 1 hour |

If commands don't appear after an hour:
1. Check the bot logs for `Commands synced` — an empty list means a cog failed to load.
2. Fully quit and reopen Discord (the client caches slash commands).
3. Try [discord.com](https://discord.com) in a browser to bypass the client cache.

## 10. Configure Daily Reminders (Optional)

Once the bot is in your server, a server admin runs these slash commands:

```
/remind channel #your-channel     Set the channel and enable reminders
/remind timezone America/New_York  Set the local midnight timezone (IANA name)
/remind status                     Confirm current config
/remind test                       Fire a reminder immediately to verify
```

The bot will then post a daily reminder at midnight in the configured timezone, mentioning all players and including action buttons.

---

## Hosting on Railway / Render / Fly.io

Set environment variables in your hosting dashboard:

| Variable | Required | Notes |
|---|---|---|
| `DISCORD_TOKEN` | Yes | Bot token from step 2 |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `PORT` | No | Default 8080; set to whatever your host assigns |
| `EXTERNAL_LEADERBOARDS` | No | Omit if not using external services |

Do **not** set `DISCORD_GUILD_ID` in production — global commands reach all servers.

The `/health` endpoint returns a JSON status object useful for uptime monitors:
```json
{
  "status": "online",
  "discord": "Sigmordle#1234",
  "guilds": 3,
  "latency_ms": 42.1,
  "uptime_s": 86400,
  "port": 8080
}
```

---

## Environment Variable Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `DISCORD_TOKEN` | Yes | — | Discord bot token |
| `DATABASE_URL` | Yes | — | PostgreSQL DSN (`postgresql://...`) |
| `PORT` | No | `8080` | HTTP status dashboard port |
| `DISCORD_GUILD_ID` | No | — | Guild ID for dev mode (instant command registration) |
| `EXTERNAL_LEADERBOARDS` | No | — | Comma-separated service names, e.g. `SIGMAFEUD,NAVI` |
| `{NAME}_ENABLED` | No | `false` | Enable a named external service |
| `{NAME}_URL` | No | — | Base URL for the service |
| `{NAME}_API_KEY` | No | — | Bearer token for the service |
