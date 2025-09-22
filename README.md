# Fresher Jobs Discord Bot

A Discord bot that:

- Fetches latest fresher jobs from [FreshersNow – Freshers Jobs](https://www.freshersnow.com/freshers-jobs/) and posts them with apply links.
- Supports slash commands to fetch on demand and to schedule daily refreshes in a channel.

Project structure:

- `src/scraper.py` – Web scraper for FreshersNow.
- `src/bot.py` – Discord bot with slash commands and scheduling.
- `.env.example` – Copy to `.env` and fill credentials.
- `requirements.txt` – Python dependencies.

## Features

- `/jobs [limit]` – Fetch latest fresher jobs and post as rich embeds with links.
- `/refresh_now [limit]` – Manually refresh and post latest jobs to the channel.
- `/schedule_refresh time_hhmm [tz]` – Schedule a daily refresh to this channel at a time (24h format) and optional timezone (default from `.env`).
- Optional global scheduled refresh driven by `REFRESH_CRON` in `.env`.

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

- `/jobs` – returns the latest fresher jobs (default 10, between 1 and 20).
- `/refresh_now` – posts the latest jobs to the channel immediately.
- `/schedule_refresh 09:00 Asia/Kolkata` – schedules a daily refresh at 09:00 IST for the current channel. The job is stored per-channel.

Additionally, if you set `REFRESH_CRON` and `DEFAULT_CHANNEL_ID` in `.env`, the bot will post on that schedule globally after it starts.

## Notes

- The scraper uses heuristics as the site structure may change. It extracts job title, link, and attempts to parse company/location/posted when present.
- Respect the site. Do not set extremely aggressive schedules. The default limit is 10.
- Timezone handling uses `pytz`. See the TZ database list here: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones

## Troubleshooting

- If you see `Missing Access` when adding the bot, ensure the OAuth2 URL includes `applications.commands` and the right permissions.
- If slash commands do not appear, verify `GUILD_ID` in `.env` matches the server where the bot is installed. Re-run the bot to resync.
- For scheduled jobs not firing, confirm your `.env` `TIMEZONE` is valid and that the machine clock/timezone are correct.
- Run with more verbose logging by setting the environment variable before launching:

```
LOG_LEVEL=DEBUG python -m src.bot
```

