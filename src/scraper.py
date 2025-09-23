import logging
from dataclasses import dataclass
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

FRESHERS_URL = "https://www.freshersnow.com/freshers-jobs/"
TNPOFFICER_URL = "https://tnpofficer.com/2025-batch/"


@dataclass
class Job:
    title: str  # Job Role
    company: Optional[str]
    qualification: Optional[str]
    experience: Optional[str]
    location: Optional[str]
    link: str  # Apply link


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
    resp = requests.get(FRESHERS_URL, headers=headers, timeout=20)
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

            jobs.append(
                Job(
                    title=title,
                    company=company,
                    qualification=None,
                    experience=None,
                    location=None,
                    link=href,
                )
            )
            if limit and len(jobs) >= limit:
                break
        if limit and len(jobs) >= limit:
            break

    logging.info("Fetched %d jobs from TNP Officer", len(jobs))
    return jobs


def fetch_combined_jobs(limit_per_source: int = 10) -> List[Job]:
    """Fetch jobs from both sources, using the same per-source limit.

    Returns FreshersNow + TNP Officer results concatenated.
    """
    a = fetch_jobs(limit=limit_per_source)
    b = fetch_tnpofficer_jobs(limit=limit_per_source)
    return a + b
