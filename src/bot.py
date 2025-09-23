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
from aiohttp import web

from .scraper import (
    fetch_combined_jobs,
    fetch_jobs as fetch_freshersnow,
    fetch_tnpofficer_jobs,
)

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
PORT = int(os.getenv("PORT", "10000"))  # For Render/Heroku-like platforms

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
        # In-memory docs (set below as constants)

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
        # Start lightweight HTTP server for Render keepalive
        await self._start_http_server()
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

    async def post_jobs(self, destination: discord.abc.Messageable, limit: int = 10, header: Optional[str] = None, only_new: bool = False, source: str = "both"):
        # Fetch by source; if 'both', limit applies per-source and totals are combined
        if source == "freshersnow":
            jobs = fetch_freshersnow(limit=limit)
        elif source == "tnpofficer":
            jobs = fetch_tnpofficer_jobs(limit=limit)
        else:
            jobs = fetch_combined_jobs(limit_per_source=limit)
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
            if source == "freshersnow":
                embed.set_footer(text="Source: freshersnow.com")
            elif source == "tnpofficer":
                embed.set_footer(text="Source: tnpofficer.com")
            else:
                embed.set_footer(text="Sources: freshersnow.com, tnpofficer.com")
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

    # ---------- HTTP keepalive server ----------
    async def _start_http_server(self):
        app = web.Application()

        async def root(_request):
            return web.json_response({"status": "ok", "service": "fresher-jobs-discord-bot"})

        async def health(_request):
            return web.Response(text="OK")

        app.add_routes([
            web.get("/", root),
            web.get("/health", health),
        ])

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        logger.info("HTTP keepalive server listening on 0.0.0.0:%s", PORT)


client = FresherJobsBot()


# ---------- Slash Commands ----------
@client.tree.command(name="jobs", description="Fetch latest fresher jobs and apply links")
@app_commands.describe(
    limit="Number of jobs to fetch (1-50)",
    only_new="Show only new jobs since last post in this channel",
    source="Choose source: both, freshersnow, tnpofficer",
)
@app_commands.choices(source=[
    app_commands.Choice(name="Both", value="both"),
    app_commands.Choice(name="FreshersNow", value="freshersnow"),
    app_commands.Choice(name="TNP Officer", value="tnpofficer"),
])
async def jobs_command(
    interaction: discord.Interaction,
    limit: Optional[int] = 10,
    only_new: Optional[bool] = False,
    source: Optional[app_commands.Choice[str]] = None,
):
    limit = max(1, min(50, limit or 10))
    await interaction.response.defer(thinking=True)
    src_val = source.value if isinstance(source, app_commands.Choice) else "both"
    header = "Latest Fresher Jobs" if src_val == "both" else f"Latest Fresher Jobs ({src_val})"
    await client.post_jobs(
        interaction.channel,
        limit=limit,
        header=header,
        only_new=bool(only_new),
        source=src_val,
    )
    await interaction.followup.send("Done.")


@client.tree.command(name="refresh_now", description="Refresh and post latest jobs to this channel")
@app_commands.describe(
    limit="Number of jobs to fetch (1-50)",
    only_new="Show only new jobs since last post in this channel",
    source="Choose source: both, freshersnow, tnpofficer",
)
@app_commands.choices(source=[
    app_commands.Choice(name="Both", value="both"),
    app_commands.Choice(name="FreshersNow", value="freshersnow"),
    app_commands.Choice(name="TNP Officer", value="tnpofficer"),
])
async def refresh_now_command(
    interaction: discord.Interaction,
    limit: Optional[int] = 30,
    only_new: Optional[bool] = True,
    source: Optional[app_commands.Choice[str]] = None,
):
    limit = max(1, min(50, limit or 30))
    await interaction.response.defer(thinking=True)
    src_val = source.value if isinstance(source, app_commands.Choice) else "both"
    header = "Manual Refresh - Latest Fresher Jobs" if src_val == "both" else f"Manual Refresh - Latest Fresher Jobs ({src_val})"
    await client.post_jobs(
        interaction.channel,
        limit=limit,
        header=header,
        only_new=bool(only_new),
        source=src_val,
    )
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

# ---------- In-memory data: Search Operators ----------
SEARCH_OPERATORS = [
    {
        "Search_Type": "Job-specific company search",
        "Search_Operator_Example": 'site:linkedin.com/jobs "product analyst" AND "bangalore"',
        "Purpose": "Find specific roles at target companies",
        "Success_Tips": "Use company LinkedIn page URL variations",
    },
    {
        "Search_Type": "Recent job postings",
        "Search_Operator_Example": 'site:naukri.com "data scientist" after:2025-08-15',
        "Purpose": "Apply to fresh postings within 2 hours",
        "Success_Tips": "Set up Google Alerts for automated monitoring",
    },
    {
        "Search_Type": "Hiring manager identification",
        "Search_Operator_Example": 'site:linkedin.com "hiring manager" AND "techcorp" AND "product"',
        "Purpose": "Identify decision makers for cold outreach",
        "Success_Tips": "Cross-reference with company org charts",
    },
    {
        "Search_Type": "Company career pages",
        "Search_Operator_Example": 'site:company.com/careers "product analyst" OR "business analyst"',
        "Purpose": "Access direct application portals",
        "Success_Tips": "Bookmark and check daily",
    },
    {
        "Search_Type": "Startup job opportunities",
        "Search_Operator_Example": '"hiring" AND "startup" AND "mumbai" site:angel.co',
        "Purpose": "Discover high-growth opportunities",
        "Success_Tips": "Follow startup accelerator portfolios",
    },
    {
        "Search_Type": "Remote work positions",
        "Search_Operator_Example": '"remote" AND "product manager" site:linkedin.com/jobs',
        "Purpose": "Filter for remote-friendly positions",
        "Success_Tips": "Include location flexibility keywords",
    },
    {
        "Search_Type": "Salary information research",
        "Search_Operator_Example": '"product analyst salary" AND "india" site:glassdoor.com',
        "Purpose": "Research competitive compensation",
        "Success_Tips": "Compare across multiple salary sites",
    },
    {
        "Search_Type": "Company news and updates",
        "Search_Operator_Example": '"TechCorp" AND ("funding" OR "growth" OR "expansion")',
        "Purpose": "Stay updated on company trajectory",
        "Success_Tips": "Set up news alerts for target companies",
    },
    {
        "Search_Type": "Employee reviews",
        "Search_Operator_Example": '"TechCorp employee review" site:glassdoor.com OR site:ambitionbox.com',
        "Purpose": "Understand company culture fit",
        "Success_Tips": "Look for recent reviews for current insights",
    },
    {
        "Search_Type": "Technical requirements",
        "Search_Operator_Example": '"python" AND "sql" AND "product analyst" filetype:pdf',
        "Purpose": "Match technical skill requirements",
        "Success_Tips": "Save relevant job descriptions for keyword extraction",
    },
]

# ---------- Command: Search Operators ----------
@client.tree.command(name="search_operators", description="Show advanced job search operators and examples")
async def search_operators_command(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)
    rows = SEARCH_OPERATORS
    if not rows:
        await interaction.followup.send("No operators found.", ephemeral=True)
        return
    # Send as multiple embeds to keep it readable
    embeds = []
    for r in rows:
        st = r.get("Search_Type", "-")
        ex = r.get("Search_Operator_Example", "-")
        purpose = r.get("Purpose", "-")
        tips = r.get("Success_Tips", "-")
        embed = discord.Embed(title=st, color=discord.Color.green())
        embed.add_field(name="Example", value=f"```text\n{ex}\n```", inline=False)
        embed.add_field(name="Purpose", value=purpose or "-", inline=False)
        if tips:
            embed.add_field(name="Success Tips", value=tips, inline=False)
        embeds.append(embed)

    # Discord limits: send in batches of 10 embeds
    batch = []
    for e in embeds:
        batch.append(e)
        if len(batch) == 10:
            await interaction.followup.send(embeds=batch, ephemeral=True)
            batch = []
    if batch:
        await interaction.followup.send(embeds=batch, ephemeral=True)


# ---------- In-memory data: Cold Email Templates ----------
COLD_TEMPLATES = [
    {
        "Template_Type": "Hiring Manager Outreach",
        "Subject_Line": "Product Analyst Opportunity - [Your Name]",
        "Template_Body": (
            "Hi [Name],\n\n"
            "I came across [Company]'s incredible work in [specific area] and was impressed by [specific achievement]. "
            "As a [your role] with [relevant experience], I believe I could contribute significantly to [specific team/project].\n\n"
            "[1-2 lines about relevant experience with metrics]\n\n"
            "I'd love to discuss how my background in [relevant skills] aligns with your team's goals. Available for a brief call this week?\n\n"
            "Best regards,\n[Your name]"
        ),
        "Best_Practices": "Research recent company news; personalize opening line; keep under 100 words; include specific metrics; provide clear CTA",
    },
    {
        "Template_Type": "Referral Request",
        "Subject_Line": "Alumni Connection - [Mutual Contact Name] Suggested I Reach Out",
        "Template_Body": (
            "Hi [Name],\n\n"
            "[Mutual contact] mentioned you might be a great person to connect with regarding opportunities in [field/company]. "
            "I'm a [your background] currently exploring [type of roles] and would appreciate any insights you might share.\n\n"
            "[Brief relevant background with 1 achievement]\n\n"
            "Would you be open to a 15-minute call to discuss the industry landscape?\n\n"
            "Thank you for your time!\n[Your name]"
        ),
        "Best_Practices": "Leverage mutual connections; be specific about advice sought; offer value in return; respect their time; follow up appropriately",
    },
    {
        "Template_Type": "Follow-up After Application",
        "Subject_Line": "Following Up: Product Analyst Application - [Your Name]",
        "Template_Body": (
            "Hi [Name],\n\n"
            "I applied for the [Position] role on [Date] and wanted to express my strong interest in joining [Company]. "
            "My experience with [relevant experience] directly aligns with the requirements outlined in the job posting.\n\n"
            "[1 specific example of relevant work]\n\n"
            "I'd welcome the opportunity to discuss how I can contribute to [specific team/project]. Please let me know if you need any additional information.\n\n"
            "Best regards,\n[Your name]"
        ),
        "Best_Practices": "Reference application date and position; highlight 1-2 key qualifications; show continued interest; professional but brief",
    },
    {
        "Template_Type": "Networking Introduction",
        "Subject_Line": "Introduction from [Industry/Event] - [Your Name]",
        "Template_Body": (
            "Hi [Name],\n\n"
            "I enjoyed meeting you at [Event/Platform] and our discussion about [topic]. Your insights on [specific topic] were particularly valuable.\n\n"
            "I'm currently exploring opportunities in [field] and would appreciate staying connected as I navigate this transition. "
            "I'd be happy to share resources or insights from my background in [your field] as well.\n\n"
            "Looking forward to staying in touch!\n[Your name]"
        ),
        "Best_Practices": "Reference where you met; mention specific conversation topics; offer mutual value; keep networking focused; suggest low-commitment next steps",
    },
    {
        "Template_Type": "Thank You After Interview",
        "Subject_Line": "Thank you for the interview opportunity - [Your Name]",
        "Template_Body": (
            "Hi [Name],\n\n"
            "Thank you for taking the time to interview me for the [Position] role yesterday. I enjoyed our discussion about [specific topic discussed] "
            "and am even more excited about the opportunity to contribute to [specific project/team].\n\n"
            "Our conversation reinforced my belief that my experience with [relevant experience] would be valuable for [specific challenge discussed]. "
            "Please let me know if you need any additional information.\n\n"
            "I look forward to hearing about next steps.\n\n"
            "Best regards,\n[Your name]"
        ),
        "Best_Practices": "Send within 24 hours; reference specific interview moments; reinforce key qualifications; address any concerns raised; maintain enthusiasm",
    },
]

def _list_template_types(rows):
    types = []
    for r in rows:
        t = (r.get("Template_Type") or "").strip()
        if t and t not in types:
            types.append(t)
    return types


@client.tree.command(name="cold_email_templates", description="Show a cold email template by type")
@app_commands.describe(template_type="Pick a template type (autocomplete)")
async def cold_email_templates_command(interaction: discord.Interaction, template_type: str):
    await interaction.response.defer(thinking=True, ephemeral=True)
    rows = COLD_TEMPLATES
    if not rows:
        await interaction.followup.send("No templates found.", ephemeral=True)
        return
    # Find matching type (case-insensitive)
    match = None
    for r in rows:
        if (r.get("Template_Type") or "").strip().lower() == template_type.strip().lower():
            match = r
            break
    if not match:
        # Suggest available types
        types = _list_template_types(rows)
        await interaction.followup.send(
            "Template not found. Available types: " + ", ".join(types), ephemeral=True
        )
        return

    subject = match.get("Subject_Line", "-")
    body = match.get("Template_Body", "-")
    best = match.get("Best_Practices", "")

    embed = discord.Embed(title=f"{template_type}", color=discord.Color.orange())
    embed.add_field(name="Subject", value=f"`{subject}`", inline=False)
    # Wrap body in code block for formatting
    body_value = f"```text\n{body}\n```"
    if len(body_value) > 950:  # split if really long
        first = body_value[:950]
        rest = body_value[950:1900]
        embed.add_field(name="Body (part 1)", value=first, inline=False)
        if rest:
            embed.add_field(name="Body (part 2)", value=rest, inline=False)
    else:
        embed.add_field(name="Body", value=body_value, inline=False)
    if best:
        embed.add_field(name="Best Practices", value=best, inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)


# Autocomplete for template_type
@cold_email_templates_command.autocomplete("template_type")
async def template_type_autocomplete(
    interaction: discord.Interaction, current: str
):
    try:
        rows = COLD_TEMPLATES
        types = _list_template_types(rows)
        current_lower = (current or "").lower()
        choices = [t for t in types if current_lower in t.lower()][:25]
        return [app_commands.Choice(name=t, value=t) for t in choices]
    except Exception:
        return []


# ---------- In-memory data: LaTeX Resume Templates ----------
RESUME_TEMPLATES = {
    "Jake's Resume": {
        "repo": "https://github.com/jakegut/resume",
        "description": "Clean, one-page resume template using standard LaTeX classes; easy to customize.",
        "snippet": (
            r"""% Jake's Resume minimal starter
\documentclass[letterpaper,11pt]{article}
\usepackage[empty]{fullpage}
\usepackage{titlesec}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\begin{document}
\begin{center}
  {\LARGE Your Name}\\
  City, Country \;|\; \href{mailto:you@email.com}{you@email.com} \;|\;
  \href{https://linkedin.com/in/you}{linkedin.com/in/you}
\end{center}
\section*{Experience}
\begin{itemize}[leftmargin=*]
  \item Company — Role (20XX–20XX): one line impact statement.
\end{itemize}
\section*{Education}
Your University — Degree
\end{document}
"""
        ),
    },
    "Deedy Resume": {
        "repo": "https://github.com/deedy/Deedy-Resume",
        "description": "Popular, nicely formatted two-column resume template in LaTeX.",
        "snippet": (
            r"""% Deedy Resume minimal starter
\documentclass[]{deedy-resume-openfont}
\begin{document}
\namesection{Your}{Name}{\href{mailto:you@email.com}{you@email.com} | linkedin.com/in/you}
\begin{minipage}[t]{0.33\textwidth}
\section{Skills}
LaTeX, Python, SQL
\end{minipage}
\hfill
\begin{minipage}[t]{0.66\textwidth}
\section{Experience}
\runsubsection{Company}
\descript{| Role}
Impact bullet here.
\end{minipage}
\end{document}
"""
        ),
    },
    "Awesome-CV": {
        "repo": "https://github.com/posquit0/Awesome-CV",
        "description": "Feature-rich LaTeX CV template with modern design and sections.",
        "snippet": (
            r"""% Awesome-CV minimal starter
\documentclass[11pt, a4paper]{awesome-cv}
\geometry{left=1.4cm, top=1.4cm, right=1.4cm, bottom=1.4cm}
\name{Your}{Name}
\position{Product Analyst}
\address{City, Country}
\email{you@email.com}
\linkedin{you}
\begin{document}
\makecvheader
\cvsection{Experience}
\cventry{20XX--20XX}{Role}{Company}{City}{}{Impact-focused bullet}
\cvsection{Education}
\cventry{20XX--20XX}{Degree}{University}{City}{}{}
\end{document}
"""
        ),
    },
}


# Upstream raw .tex URLs to try per template (first that works will be used)
RESUME_URLS = {
    "Jake's Resume": [
        "https://raw.githubusercontent.com/jakegut/resume/master/resume.tex",
    ],
    "Deedy Resume": [
        # Try common filenames (case-sensitive on GitHub)
        "https://raw.githubusercontent.com/deedy/Deedy-Resume/master/Deedy-Resume.tex",
        "https://raw.githubusercontent.com/deedy/Deedy-Resume/master/Deedy-Resume-OpenFont.tex",
        "https://raw.githubusercontent.com/deedy/Deedy-Resume/master/Deedy-Resume-openfont.tex",
    ],
    "Awesome-CV": [
        "https://raw.githubusercontent.com/posquit0/Awesome-CV/master/examples/resume.tex",
    ],
}


async def _http_get_text(url: str, timeout: int = 15) -> str:
    import aiohttp
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=timeout) as resp:
                if resp.status == 200:
                    return await resp.text()
        except Exception:
            return ""
    return ""


@client.tree.command(name="resume", description="Show a LaTeX resume template (online or built-in snippet)")
@app_commands.describe(template="Choose a template (autocomplete)", mode="Fetch online or use built-in snippet")
@app_commands.choices(mode=[
    app_commands.Choice(name="online", value="online"),
    app_commands.Choice(name="builtin", value="builtin"),
])
async def resume_command(interaction: discord.Interaction, template: str, mode: Optional[app_commands.Choice[str]] = None):
    await interaction.response.defer(thinking=True, ephemeral=True)
    key = None
    for k in RESUME_TEMPLATES.keys():
        if k.lower() == (template or "").lower():
            key = k
            break
    if key is None:
        choices = ", ".join(RESUME_TEMPLATES.keys())
        await interaction.followup.send(f"Template not found. Available: {choices}", ephemeral=True)
        return

    data = RESUME_TEMPLATES[key]
    chosen_mode = (mode.value if isinstance(mode, app_commands.Choice) else "online")

    embed = discord.Embed(title=f"{key} (LaTeX)", color=discord.Color.purple())
    embed.add_field(name="Repository", value=data["repo"], inline=False)
    embed.add_field(name="About", value=data["description"], inline=False)

    snippet_text = ""
    source_note = ""
    if chosen_mode == "online":
        urls = RESUME_URLS.get(key, [])
        for u in urls:
            txt = await _http_get_text(u)
            if txt and len(txt) > 100:  # sanity check
                snippet_text = txt
                source_note = f"Fetched from: {u}"
                break
        if not snippet_text:
            # fallback to builtin
            snippet_text = data["snippet"]
            source_note = "Falling back to built-in snippet (online fetch failed)"
    else:
        snippet_text = data["snippet"]
        source_note = "Using built-in snippet"

    # Split long snippets to avoid field size limits
    chunks = [snippet_text[i:i+950] for i in range(0, len(snippet_text), 950)]
    for idx, chunk in enumerate(chunks, start=1):
        label = "Snippet" if len(chunks) == 1 else f"Snippet (part {idx})"
        embed.add_field(name=label, value=f"```latex\n{chunk}\n```", inline=False)

    if source_note:
        embed.set_footer(text=source_note)

    await interaction.followup.send(embed=embed, ephemeral=True)


@resume_command.autocomplete("template")
async def resume_template_autocomplete(interaction: discord.Interaction, current: str):
    q = (current or "").lower()
    names = [k for k in RESUME_TEMPLATES.keys() if q in k.lower()]
    return [app_commands.Choice(name=n, value=n) for n in names[:25]]

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not set. Create a .env with DISCORD_TOKEN=<token>.")
    client.run(TOKEN)
