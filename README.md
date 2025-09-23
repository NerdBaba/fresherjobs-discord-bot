# Fresher Jobs Discord Bot

A Discord bot that:

- Fetches latest fresher jobs from two sources and posts with apply links:
  - [FreshersNow – Freshers Jobs](https://www.freshersnow.com/freshers-jobs/)
  - [TNP Officer – 2025 Batch](https://tnpofficer.com/2025-batch/)
- Supports slash commands to fetch on demand, filter only-new since last post, select source, and schedule daily refreshes in a channel.
- Provides utility commands for job search operators, cold email templates, and LaTeX resume templates (links to Overleaf).

Project structure:

- `src/scraper.py` – Web scrapers for FreshersNow and TNP Officer; combined fetch helper.
- `src/bot.py` – Discord bot with slash commands, scheduler, keepalive HTTP server for hosting.
- `.env.example` – Copy to `.env` and fill credentials.
- `requirements.txt` – Python dependencies.
- `.gitignore` – Excludes `.env`, `.venv/`, and runtime data like `data/seen.json`.

## Features

- `/jobs limit:[1-50] only_new:[true|false] source:[both|freshersnow|tnpofficer]`
  - Fetch latest fresher jobs and post as rich embeds with links.
  - When `source=both` (default), the `limit` applies per-source. Example: `limit=50` posts up to 100 total (50+50).
- `/refresh_now limit:[1-50] only_new:[true|false] source:[both|freshersnow|tnpofficer]`
  - Manually refresh and post latest jobs to the channel. Default `limit=30` (per-source).
- `/schedule_refresh time_hhmm [tz]`
  - Schedule a daily refresh to this channel at a time (24h format) and optional timezone (default from `.env`). Uses both sources and only-new filtering by default.
- `/search_operators`
  - Shows advanced job search operators and examples as embeds.
- `/cold_email_templates template_type:<autocomplete>`
  - Shows a cold email template with best practices.
- `/resume template:<autocomplete>`
  - Shows repo and Overleaf links for popular LaTeX resume templates (Jake’s Resume, Deedy Resume, Awesome-CV).

## Prerequisites

- Python 3.10+
- A Discord account and a Discord server where you have permission to add a bot

## Setup: Discord Developer Portal

1. Create Application
   - Go to https://discord.com/developers/applications
   - Click "New Application", name it (e.g., "Fresher Jobs Bot").

2. Create a Bot User
   - In your application, open the "Bot" tab.
   - Click "Reset Token" or "Reveal Token" and copy the Bot Token. You will put this in `.env` as `DISCORD_TOKEN`.
   - Under "Privileged Gateway Intents", enable:
     - MESSAGE CONTENT INTENT: Not required for this bot (we only use slash commands), you can keep it disabled.
     - SERVER MEMBERS INTENT: Not required.
     - PRESENCE INTENT: Not required.
   - Under "Bot Permissions" you can optionally preselect:
     - Send Messages
     - Embed Links

3. Application ID
   - In the "General Information" tab, copy the `Application ID` and put it in `.env` as `APPLICATION_ID`.

4. Invite the Bot to Your Server (OAuth2 URL)
   - Go to the "OAuth2" → "URL Generator" tab.
   - Scopes: select `bot` and `applications.commands`.
   - Bot Permissions: select at least `Send Messages` and `Embed Links`.
   - Copy the generated URL and open it in your browser to add the bot to your server.

5. Guild and Channel IDs
   - Right-click your server icon → "Copy Server ID" (Developer Mode must be enabled in Discord user settings → Advanced). Put one server ID in `.env` as `GUILD_ID` to get slash commands immediately in that guild. If you leave it blank, commands will be global but may take up to one hour to appear.
   - Right-click a channel → "Copy Channel ID" and put it as `DEFAULT_CHANNEL_ID` if you want the global scheduled refresh to post there.

## Local configuration

1. Copy `.env.example` → `.env` and fill the values:

```
DISCORD_TOKEN=your-bot-token
APPLICATION_ID=your-application-id
GUILD_ID=your-guild-id-optional
DEFAULT_CHANNEL_ID=your-default-channel-id-optional
TIMEZONE=Asia/Kolkata
# Optional 5-field cron (minute hour day month dow) to enable a default scheduled refresh
#REFRESH_CRON=0 9 * * *
```

2. Install dependencies

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. Run the bot

```
python -m src.bot
```

If slash commands do not show immediately:
- If you set `GUILD_ID`, commands should appear right away in that server.
- If you did not set `GUILD_ID`, global command registration can take up to 1 hour. You can also temporarily set `GUILD_ID` for faster iteration during development.

## Using the bot

- `/jobs limit:50 source:both only_new:true` – up to 100 total items (50 per source).
- `/jobs limit:25 source:freshersnow` – only FreshersNow.
- `/refresh_now limit:30 source:tnpofficer only_new:true` – only TNP Officer.
- `/schedule_refresh 09:00 Asia/Kolkata` – schedules a daily refresh (both sources) for the current channel. The job is stored per-channel.

Additionally, if you set `REFRESH_CRON` and `DEFAULT_CHANNEL_ID` in `.env`, the bot will post on that schedule globally after it starts (both sources, only-new).

## Notes

- The scrapers use heuristics as site structures may change. We extract title, link, and attempt to parse company/location/qualification/experience when present.
- Respect the sites. Avoid aggressive schedules. Defaults are modest.
- “Only new since last post” is tracked per channel by link. We can switch to a content hash if needed.
- Timezone handling uses `pytz`. See the TZ database list here: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones

## Troubleshooting

- If you see `Missing Access` when adding the bot, ensure the OAuth2 URL includes `applications.commands` and the right permissions.
- If slash commands do not appear, verify `GUILD_ID` in `.env` matches the server where the bot is installed. Re-run the bot to resync.
- For scheduled jobs not firing, confirm your `.env` `TIMEZONE` is valid and that the machine clock/timezone are correct.
- Render/Web hosting: make sure the service binds to `PORT` (the bot does this) and set Health Check Path to `/health`.
- Run with more verbose logging by setting the environment variable before launching:

```
LOG_LEVEL=DEBUG python -m src.bot
```

## Hosting (Render / web service)

- This bot starts a tiny HTTP server bound to `0.0.0.0:$PORT` with endpoints `/` and `/health` so it can run on Render’s Web Service.
- On Render, set Environment variables in the dashboard:
  - `DISCORD_TOKEN`, `APPLICATION_ID`, `GUILD_ID`, `DEFAULT_CHANNEL_ID`, `TIMEZONE`, optional `REFRESH_CRON`.
- Start command: `python -m src.bot`
- Health Check Path: `/health`
- Free tier may sleep; keep-alive by pinging `/health` every few minutes if necessary.

## Security

- Never commit your Discord bot token. `.gitignore` excludes `.env` by default.
- If a token leaks, reset it immediately in the Developer Portal and update your deployment.

