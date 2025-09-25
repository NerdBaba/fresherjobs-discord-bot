import logging
from dataclasses import dataclass
from typing import List, Optional

import requests
import re
from bs4 import BeautifulSoup

FRESHERS_URL = "https://www.freshersnow.com/freshers-jobs/"
# Cloudflare-protected: fetch via provided proxy
FRESHERS_PROXY = (
    "https://simple-proxy.mda2233.workers.dev/?destination=" + FRESHERS_URL
)
TNPOFFICER_URL = "https://tnpofficer.com/2025-batch/"
OFFCAMPUS_URL = "https://offcampusjobs4u.com/off-campus-freshers-job/2025-batch-off-campus/"


@dataclass
class Job:
    title: str  # Job Role
    company: Optional[str]
    qualification: Optional[str]
    experience: Optional[str]
    location: Optional[str]
    link: str  # Apply link
    image_url: Optional[str] = None


def fetch_jobs(limit: Optional[int] = 20) -> List[Job]:
    """Scrape jobs from FreshersNow Freshers Jobs page.

    Args:
        limit: Optional cap on number of jobs to return.

    Returns:
        List of Job entries.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/116.0 Safari/537.36"
        )
    }
    # Use proxy to bypass Cloudflare JS challenge
    resp = requests.get(FRESHERS_PROXY, headers=headers, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    jobs: List[Job] = []

    # ---- Primary: Parse the main table with columns ----
    # We try to locate a table whose headers include the expected columns.
    def normalize(s: Optional[str]) -> str:
        return (s or "").strip()

    def find_target_table() -> Optional[BeautifulSoup]:
        for table in soup.find_all("table"):
            headers = [normalize(th.get_text(" ", strip=True)).lower() for th in table.find_all("th")]
            if not headers:
                # Sometimes first row might be header with <td>
                first_row = table.find("tr")
                if first_row:
                    headers = [normalize(td.get_text(" ", strip=True)).lower() for td in first_row.find_all(["th", "td"])]
            if not headers:
                continue
            wanted = ["company", "job role", "qualification", "experience", "location", "apply"]
            hits = sum(1 for w in wanted if any(w in h for h in headers))
            if hits >= 4:  # good enough match
                return table
        return None

    table = find_target_table()
    if table:
        # Determine column index mapping using header row
        header_row = table.find("tr")
        headers = [normalize(h.get_text(" ", strip=True)).lower() for h in header_row.find_all(["th", "td"])]
        idx = {name: None for name in ["company", "job role", "qualification", "experience", "location", "apply"]}
        for i, h in enumerate(headers):
            for key in list(idx.keys()):
                if key in h and idx[key] is None:
                    idx[key] = i

        # Iterate data rows
        rows = table.find_all("tr")[1:] if header_row else table.find_all("tr")
        for r in rows:
            cells = r.find_all(["td", "th"])  # some tables may use th for first column
            if not cells or len(cells) < 4:
                continue
            def get_cell_text(key: str) -> Optional[str]:
                j = idx.get(key)
                if j is not None and j < len(cells):
                    return normalize(cells[j].get_text(" ", strip=True))
                return None
            def get_cell_link(key: str) -> Optional[str]:
                j = idx.get(key)
                if j is not None and j < len(cells):
                    a = cells[j].find("a", href=True)
                    if a and a.get("href"):
                        return a["href"].strip()
                return None

            company = get_cell_text("company")
            title = get_cell_text("job role") or get_cell_text("role") or get_cell_text("job")
            qualification = get_cell_text("qualification")
            experience = get_cell_text("experience")
            location = get_cell_text("location")
            link = get_cell_link("apply") or get_cell_link("job role")

            if not (title and link):
                # Sometimes the apply is in last cell regardless of header
                if not link and cells:
                    a = cells[-1].find("a", href=True)
                    if a:
                        link = a.get("href", "").strip()
            if not (title and link):
                continue

            jobs.append(Job(
                title=title,
                company=company,
                qualification=qualification,
                experience=experience,
                location=location,
                link=link,
            ))
            if limit and len(jobs) >= limit:
                break

    # ---- Fallback: old heuristic parsing if table wasn't found or too few items ----
    if not jobs:
        # Try multiple patterns as site structure may change.
        possible_lists = [
            ("article", {"class": "type-post"}),
            ("div", {"class": "job-list"}),
            ("li", {"class": "job-item"}),
            ("div", {"class": "post"}),
        ]

        found_items = []
        for tag, attrs in possible_lists:
            found_items = soup.find_all(tag, attrs=attrs)
            if found_items:
                break

        if not found_items:
            found_items = soup.select("article, .post, .entry, .blog-post, li")[:100]

        for item in found_items:
            a = item.find("a", href=True)
            title = a.get_text(strip=True) if a else None
            link = a["href"].strip() if a else None
            if not title or not link:
                h2a = item.select_one("h2 a, h3 a")
                if h2a and h2a.get("href"):
                    title = title or h2a.get_text(strip=True)
                    link = link or h2a["href"].strip()
            if not title or not link:
                continue

            company = None
            location = None
            qualification = None
            experience = None

            jobs.append(Job(
                title=title,
                company=company,
                qualification=qualification,
                experience=experience,
                location=location,
                link=link,
            ))
            if limit and len(jobs) >= limit:
                break

    logging.info("Fetched %d jobs from FreshersNow", len(jobs))
    return jobs


def fetch_tnpofficer_jobs(limit: Optional[int] = 20) -> List[Job]:
    """Scrape jobs from TNP Officer 2025 batch page.

    This page often lists many off-campus drive links directly. If JS 'load more'
    is used, we still attempt to parse all present links in the HTML.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/116.0 Safari/537.36"
        )
    }
    resp = requests.get(TNPOFFICER_URL, headers=headers, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    jobs: List[Job] = []

    # Heuristic: find the main content area and collect relevant anchors
    # Often content is within article entry-content or similar.
    containers = soup.select(
        "article, .entry-content, .post-content, .site-content, #content"
    )
    if not containers:
        containers = [soup]

    seen = set()
    for c in containers:
        for a in c.find_all("a", href=True):
            href = a["href"].strip()
            text = a.get_text(" ", strip=True)
            if not href.startswith("https://tnpofficer.com/"):
                continue
            if not text or len(text) < 6:
                continue
            # Filter obvious non-job links
            lower = text.lower()
            if any(k in lower for k in ["mock", "course", "certification", "resources", "quick links"]):
                continue
            if href in seen:
                continue
            seen.add(href)

            title = text
            # Try to extract company from common pattern: "<Company> off campus drive ..."
            company = None
            for sep in [" off campus", " Off Campus", " | "]:
                if sep in title:
                    company = title.split(sep)[0].strip()
                    break

            # Try to find an image near the link
            img_url = None
            img = a.find("img") or a.parent.find("img") if a.parent else None
            if not img:
                # look for preceding image sibling
                prev = a.find_previous("img")
                if prev:
                    img = prev
            if img and img.get("src"):
                src = img.get("src").strip()
                if src.startswith("//"):
                    src = "https:" + src
                if src.startswith("/"):
                    src = "https://tnpofficer.com" + src
                img_url = src

            jobs.append(
                Job(
                    title=title,
                    company=company,
                    qualification=None,
                    experience=None,
                    location=None,
                    link=href,
                    image_url=img_url,
                )
            )
            if limit and len(jobs) >= limit:
                break
        if limit and len(jobs) >= limit:
            break

    logging.info("Fetched %d jobs from TNP Officer", len(jobs))
    return jobs


def fetch_combined_jobs(limit_per_source: int = 10) -> List[Job]:
    """Fetch jobs from all sources, using the same per-source limit.

    Returns FreshersNow + TNP Officer + OffCampusJobs4u results concatenated.
    """
    a = fetch_jobs(limit=limit_per_source)
    b = fetch_tnpofficer_jobs(limit=limit_per_source)
    c = fetch_offcampus_jobs(limit=limit_per_source)
    return a + b + c


def fetch_offcampus_jobs(limit: Optional[int] = 20) -> List[Job]:
    """Scrape jobs from OffCampusJobs4u 2025 batch listing.

    Attempts to capture title, link, and any nearby image.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/116.0 Safari/537.36"
        )
    }
    resp = requests.get(OFFCAMPUS_URL, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    jobs: List[Job] = []

    # ---- Primary: exact container targeting (#tdi_74 grid) ----
    grid = soup.select_one("#tdi_74.td_block_inner")

    def normalize_img(src: Optional[str]) -> Optional[str]:
        if not src:
            return None
        src = src.strip()
        if src.startswith("//"):
            src = "https:" + src
        if src.startswith("/"):
            src = "https://offcampusjobs4u.com" + src
        return src

    seen = set()
    if grid:
        for mod in grid.select(".td_module_wrap"):
            # Title and link
            title_a = mod.select_one("h3.entry-title.td-module-title a[href]")
            thumb_a = mod.select_one(".td-module-thumb a[href]")
            a = title_a or thumb_a
            if not a:
                continue
            href = a.get("href", "").strip()
            if not href.startswith("https://offcampusjobs4u.com/"):
                continue
            title = (title_a.get_text(" ", strip=True) if title_a else a.get_text(" ", strip=True))
            if not title or len(title) < 6:
                continue
            # Exclude non-job/site links just in case
            tl = title.lower()
            if any(k in tl for k in ["about", "advertise", "disclaimer", "privacy", "contact", "jobs by batch", "batch off campus"]):
                continue
            if href in seen:
                continue
            # Image: background-image in span.entry-thumb style
            span = mod.select_one("span.entry-thumb")
            img_url = None
            if span and span.has_attr("style"):
                m = re.search(r"background-image:\s*url\(['\"]?(.*?)['\"]?\)", span["style"]) 
                if m:
                    img_url = normalize_img(m.group(1))
            if not img_url:
                # Secondary: try any img in the module
                img = mod.find("img")
                if img and img.get("src"):
                    img_url = normalize_img(img.get("src"))
            if not img_url:
                continue
            seen.add(href)

            jobs.append(Job(
                title=title,
                company=None,
                qualification=None,
                experience=None,
                location=None,
                link=href,
                image_url=img_url,
            ))
            if limit and len(jobs) >= limit:
                break

    # ---- Fallback: generic crawl if the section was not identified ----
    if not jobs:
        containers = soup.select("article, .entry-content, .post-content, .site-content, #content")
        if not containers:
            containers = [soup]

        for c in containers:
            for a in c.find_all("a", href=True):
                href = a["href"].strip()
                text = a.get_text(" ", strip=True)
                if not href.startswith("https://offcampusjobs4u.com/"):
                    continue
                if not text or len(text) < 6:
                    continue
                if href in seen:
                    continue
                lower = text.lower()
                if any(k in lower for k in ["privacy", "terms", "contact", "category", "tag", "jobs by batch", "batch off campus", "2022", "2023", "2024", "2025 batch off campus"]):
                    continue

                img = a.find("img") or (a.parent.find("img") if a.parent else None)
                if not img:
                    prev = a.find_previous("img")
                    if prev:
                        img = prev
                img_url = normalize_img(img.get("src") if img else None)
                if not img_url:
                    continue

                seen.add(href)

                jobs.append(Job(
                    title=text,
                    company=None,
                    qualification=None,
                    experience=None,
                    location=None,
                    link=href,
                    image_url=img_url,
                ))
                if limit and len(jobs) >= limit:
                    break
            if limit and len(jobs) >= limit:
                break

    logging.info("Fetched %d jobs from OffCampusJobs4u", len(jobs))
    return jobs
