import asyncio
import logging
import os
import json
from pathlib import Path
from typing import Optional

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from discord import app_commands
from dotenv import load_dotenv
import pytz

from .scraper import fetch_jobs

# ---------- Setup Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger("fresher-bot")

# ---------- Load Env ----------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
APPLICATION_ID = os.getenv("APPLICATION_ID")
DEFAULT_GUILD_ID = os.getenv("GUILD_ID")
DEFAULT_CHANNEL_ID = os.getenv("DEFAULT_CHANNEL_ID")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")
REFRESH_CRON = os.getenv("REFRESH_CRON")  # e.g. "0 9 * * *" for 9:00 daily

# ---------- Discord Client ----------
intents = discord.Intents.default()
intents.message_content = False

class FresherJobsBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.scheduler = AsyncIOScheduler(timezone=pytz.timezone(TIMEZONE))
        # Seen storage path
        base_path = Path(__file__).resolve().parent.parent
        self.data_dir = base_path / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.seen_file = self.data_dir / "seen.json"
        self._seen_lock = asyncio.Lock()
        self._seen = {"channels": {}}  # channel_id -> {"links": [..]}

    async def setup_hook(self) -> None:
        # Sync commands
        if DEFAULT_GUILD_ID:
            guild = discord.Object(id=int(DEFAULT_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Slash commands synced to guild %s", DEFAULT_GUILD_ID)
        else:
            await self.tree.sync()
            logger.info("Slash commands synced globally (may take up to 1 hour)")

        # Start scheduler
        self.scheduler.start()
        # Load seen store
        await self._load_seen()
        if REFRESH_CRON and DEFAULT_CHANNEL_ID:
            # Parse 5-field cron: m h dom mon dow
            try:
                minute, hour, day, month, dow = REFRESH_CRON.split()
                trigger = CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow)
                self.scheduler.add_job(
                    self._scheduled_refresh,
                    trigger=trigger,
                    id="daily_refresh",
                    replace_existing=True,
                )
                logger.info("Scheduled refresh with CRON '%s' in TZ %s", REFRESH_CRON, TIMEZONE)
            except Exception as e:
                logger.exception("Invalid REFRESH_CRON format: %s", e)

    async def _scheduled_refresh(self):
        try:
            channel_id = int(DEFAULT_CHANNEL_ID)
            channel = self.get_channel(channel_id)
            if channel is None:
                logger.error("Default channel %s not found", DEFAULT_CHANNEL_ID)
                return
            await self.post_jobs(channel, limit=10, header="Scheduled Refresh - Latest Fresher Jobs", only_new=True)
        except Exception:
            logger.exception("Error in scheduled refresh")

    async def post_jobs(self, destination: discord.abc.Messageable, limit: int = 10, header: Optional[str] = None, only_new: bool = False):
        jobs = fetch_jobs(limit=limit)
        if not jobs:
            await destination.send("No jobs found right now. Please try again later.")
            return

        # Determine channel id for tracking
        channel_id = getattr(destination, "id", None)
        if only_new and channel_id:
            jobs, new_links = await self._filter_new(channel_id, jobs)
            if not jobs:
                await destination.send("No new jobs since last post.")
                return
        else:
            new_links = [j.link for j in jobs]

        if header:
            await destination.send(header)

        # Send as embeds in batches to avoid hitting message limits
        for job in jobs:
            embed = discord.Embed(title=job.title, url=job.link, color=discord.Color.blue())
            if job.company:
                embed.add_field(name="Company", value=job.company, inline=True)
            if job.location:
                embed.add_field(name="Location", value=job.location, inline=True)
            if getattr(job, "qualification", None):
                embed.add_field(name="Qualification", value=job.qualification, inline=True)
            if getattr(job, "experience", None):
                embed.add_field(name="Experience", value=job.experience, inline=True)
            embed.set_footer(text="Source: freshersnow.com")
            await destination.send(embed=embed)

        # Mark links as seen for this channel
        if channel_id:
            await self._add_seen(channel_id, new_links)

    # ---------- Seen store helpers ----------
    async def _load_seen(self):
        try:
            if self.seen_file.exists():
                content = self.seen_file.read_text(encoding="utf-8")
                if content.strip():
                    self._seen = json.loads(content)
                    if "channels" not in self._seen:
                        self._seen = {"channels": {}}
        except Exception:
            logger.exception("Failed to load seen store; starting fresh")
            self._seen = {"channels": {}}

    async def _save_seen(self):
        async with self._seen_lock:
            try:
                tmp_path = self.seen_file.with_suffix(".tmp")
                tmp_path.write_text(json.dumps(self._seen, indent=2), encoding="utf-8")
                tmp_path.replace(self.seen_file)
            except Exception:
                logger.exception("Failed to save seen store")

    async def _filter_new(self, channel_id: int, jobs):
        async with self._seen_lock:
            chan = self._seen["channels"].get(str(channel_id), {"links": []})
            seen_links = set(chan.get("links", []))
        new = [j for j in jobs if j.link not in seen_links]
        return new, [j.link for j in new]

    async def _add_seen(self, channel_id: int, links):
        async with self._seen_lock:
            chan = self._seen["channels"].setdefault(str(channel_id), {"links": []})
            current = set(chan.get("links", []))
            current.update(links)
            chan["links"] = list(current)
        await self._save_seen()


client = FresherJobsBot()


# ---------- Slash Commands ----------
@client.tree.command(name="jobs", description="Fetch latest fresher jobs and apply links")
@app_commands.describe(limit="Number of jobs to fetch (1-50)", only_new="Show only new jobs since last post in this channel")
async def jobs_command(interaction: discord.Interaction, limit: Optional[int] = 10, only_new: Optional[bool] = False):
    limit = max(1, min(50, limit or 10))
    await interaction.response.defer(thinking=True)
    await client.post_jobs(interaction.channel, limit=limit, header="Latest Fresher Jobs", only_new=bool(only_new))
    await interaction.followup.send("Done.")


@client.tree.command(name="refresh_now", description="Refresh and post latest jobs to this channel")
@app_commands.describe(limit="Number of jobs to fetch (1-50)", only_new="Show only new jobs since last post in this channel")
async def refresh_now_command(interaction: discord.Interaction, limit: Optional[int] = 30, only_new: Optional[bool] = True):
    limit = max(1, min(50, limit or 30))
    await interaction.response.defer(thinking=True)
    await client.post_jobs(interaction.channel, limit=limit, header="Manual Refresh - Latest Fresher Jobs", only_new=bool(only_new))
    await interaction.followup.send("Done.")


@client.tree.command(name="schedule_refresh", description="Schedule a daily refresh to this channel at a given time (HH:MM, 24h)")
@app_commands.describe(time_hhmm="Time in 24h format, e.g. 09:00", tz="Timezone, e.g. Asia/Kolkata")
async def schedule_refresh_command(interaction: discord.Interaction, time_hhmm: str, tz: Optional[str] = None):
    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        timezone = tz or TIMEZONE
        tzinfo = pytz.timezone(timezone)
        hour, minute = map(int, time_hhmm.split(":"))
        # Create/replace a job for this channel
        job_id = f"refresh_{interaction.channel_id}"
        trigger = CronTrigger(hour=hour, minute=minute, timezone=tzinfo)

        def job_exists(sched, jid):
            try:
                return sched.get_job(jid) is not None
            except Exception:
                return False

        if job_exists(client.scheduler, job_id):
            client.scheduler.remove_job(job_id)
        async def job():
            channel = client.get_channel(interaction.channel_id)
            if channel:
                await client.post_jobs(channel, limit=10, header=f"Scheduled Refresh - Latest Fresher Jobs ({timezone})", only_new=True)

        client.scheduler.add_job(job, trigger=trigger, id=job_id, replace_existing=True)
        await interaction.followup.send(
            f"Scheduled daily refresh at {time_hhmm} ({timezone}) for <#{interaction.channel_id}>.",
            ephemeral=True,
        )
    except Exception as e:
        logger.exception("Failed to schedule refresh")
        await interaction.followup.send(f"Failed to schedule refresh: {e}", ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not set. Create a .env with DISCORD_TOKEN=<token>.")
    client.run(TOKEN)
