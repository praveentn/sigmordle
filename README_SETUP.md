# Sigmordle — Discord App Setup Guide

## 1. Create a Discord Application

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application**
3. Name it `Sigmordle` (or whatever you like) → **Create**

## 2. Add a Bot

1. In the left sidebar click **Bot**
2. Click **Add Bot** → **Yes, do it!**
3. Under **Token** click **Reset Token** → copy it (you'll need it in step 5)
4. **Privileged Gateway Intents** — enable:
   - ✅ **Message Content Intent** (required to read plain-text messages)
   - ✅ **Server Members Intent** (needed for member lookups in `/wordle stats @user`)

## 3. Set Bot Permissions

In the **Bot** tab, under **Bot Permissions**, enable:

| Permission | Why |
|---|---|
| Read Messages / View Channels | See channels |
| Send Messages | Reply to commands |
| Embed Links | Rich embeds |
| Use Slash Commands | Application commands |
| Read Message History | Context for slash commands |

## 4. Generate an OAuth2 Invite URL

1. Left sidebar → **OAuth2** → **URL Generator**
2. Under **Scopes** tick:
   - ✅ `bot`
   - ✅ `applications.commands`
3. Under **Bot Permissions** tick the same permissions from step 3
4. Copy the generated URL at the bottom

**Invite URL format (manual):**
```
https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&scope=bot+applications.commands&permissions=274877975552
```
Replace `YOUR_CLIENT_ID` with your Application ID (found on the **General Information** page).

## 5. Configure `.env`

In the project root, edit `.env`:

```env
DISCORD_TOKEN=paste_your_bot_token_here

# Optional — for fast dev/test (instant command registration in one server):
# DISCORD_GUILD_ID=your_server_id_here
```

**How to find your Server ID:** Right-click your server icon in Discord → **Copy Server ID** (requires Developer Mode: Settings → Advanced → Developer Mode).

## 6. Install & Run

### macOS / Linux
```bash
./start.sh
```

### Windows
```bat
start.bat
```

Both scripts will:
- Create a Python virtual environment (`venv/`)
- Install all dependencies from `requirements.txt`
- Start the bot and open a status page at `http://localhost:8080/`

### Manual (any OS)
```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

## 7. Invite the Bot to Your Server

Paste the invite URL from step 4 into a browser and select your server.

## 8. Slash Command Registration

- **With `DISCORD_GUILD_ID` set:** Commands appear instantly in that server only (development mode).
- **Without `DISCORD_GUILD_ID`:** Commands register globally — propagation takes **up to 1 hour**.

If commands don't appear after an hour:
1. Check bot logs for `Commands synced` — if the list is empty, a cog failed to load.
2. Fully quit and reopen Discord (the desktop client caches slash commands).
3. Try [discord.com](https://discord.com) in a browser to bypass the client cache.

## Hosting on Railway / Render / Fly.io

Set the following environment variables in your hosting dashboard:

| Variable | Value |
|---|---|
| `DISCORD_TOKEN` | Your bot token |
| `PORT` | `8080` (or whatever your host assigns) |

Do **not** set `DISCORD_GUILD_ID` in production — global commands reach all servers.
