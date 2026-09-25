"""Startup job posts from Hacker News "Ask HN: Who is hiring?" threads.

Uses the official, free HN Algolia API. Each top-level comment in a thread is
one company's post, usually starting with a header line like
"Company | Role | Location | ONSITE | VISA | https://...".
Posts are returned in the same shape as JSearch results.
"""

import datetime
import html
import json
import re
import urllib.request

SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=20"
ITEM_URL = "https://hn.algolia.com/api/v1/items/{}"
POST_URL = "https://news.ycombinator.com/item?id={}"
TIMEOUT_SECONDS = 60

# A line naming one of the target roles: a target area plus an engineering role.
TARGET = re.compile(r"(?<!\w)(platform|cloud|devops|sre|site reliability|infrastructure|infra|kubernetes)",
                    re.IGNORECASE)
ROLE = re.compile(r"(?<!\w)(engineer|sre|architect|developer|devops)", re.IGNORECASE)
NOT_COMPANY_NAMES = {"remote", "onsite", "on-site", "hybrid", "nyc", "sf", "new york",
                     "san francisco", "bay area", "full-time", "full time", "us", "usa"}
CAREERS_LINK = re.compile(r"greenhouse\.io|lever\.co|ashbyhq\.com|workable|careers|jobs", re.IGNORECASE)


def get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "platform-jobs-daily"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
        return json.load(resp)


def post_text(markup):
    text = re.sub(r"<p>", "\n", markup or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(text)


def post_links(markup):
    return [html.unescape(u) for u in re.findall(r'href="([^"]+)"', markup or "")]


def company_name(header):
    """The company from a post's header: text before the first |, dash or colon."""
    first = re.split(r"\||\s[-–—]\s|:|,|\s\(", header)[0]
    first = re.sub(r"^(we['’]?re|we are) hiring (at|for)\s+", "", first.strip(), flags=re.IGNORECASE)
    # Some posts start with a location or work style instead of the company.
    if first.lower() in NOT_COMPANY_NAMES:
        return ""
    return first[:80]


def role_line(parts, lines):
    """The post's role for our purposes: a header part or line naming a target role."""
    for candidate in parts[1:] + lines[1:]:
        candidate = re.sub(r"https?://\S+", "", candidate).strip(" *-•:")
        if len(candidate) <= 120 and TARGET.search(candidate) and ROLE.search(candidate):
            return candidate
    return None


def parse_post(comment, place_pattern):
    """A job dict for a Bay Area post naming a target role, or None."""
    text = post_text(comment.get("text"))
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return None
    header = lines[0]
    parts = [p.strip() for p in header.split("|") if p.strip()]
    title = role_line(parts, lines)
    # Location from the header, or failing that the first few lines.
    place = next((p for p in parts if place_pattern.search(p)), None)
    if not place:
        match = place_pattern.search(" ".join(lines[:3]))
        place = match.group(0) if match else None
    if not title or not place:
        return None
    links = post_links(comment.get("text"))
    careers = [u for u in links if CAREERS_LINK.search(u)]
    url = (careers or links or [POST_URL.format(comment["id"])])[0]
    company = company_name(header)
    return {
        "job_title": title,
        "employer_name": company,
        "job_city": place,
        "job_apply_link": url,
        "job_apply_is_direct": url != POST_URL.format(comment["id"]),
        "job_publisher": "Hacker News",
        "job_description": "\n".join(lines),
        "job_posted_at_datetime_utc": comment.get("created_at") or "",
        "hn_post": POST_URL.format(comment["id"]),
    }


def fetch_posts(config, place_pattern):
    """Return (Bay Area posts for target roles, (link, company) for every link in the threads)."""
    threads = int(config.get("threads") or 1)
    max_age = int(config.get("max_age_days") or 0)
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=max_age)
              if max_age else None)
    stories = [h for h in get_json(SEARCH_URL).get("hits", [])
               if (h.get("title") or "").lower().startswith("ask hn: who is hiring")][:threads]
    jobs, links = [], []
    for story in stories:
        thread = get_json(ITEM_URL.format(story["objectID"]))
        for comment in thread.get("children") or []:
            if not comment.get("text"):
                continue  # deleted or flagged
            lines = post_text(comment["text"]).strip().split("\n")
            company = company_name(lines[0]) if lines else ""
            links.extend((link, company) for link in post_links(comment["text"]))
            posted = datetime.datetime.fromisoformat(comment["created_at"].replace("Z", "+00:00"))
            if cutoff and posted < cutoff:
                continue
            job = parse_post(comment, place_pattern)
            if job:
                jobs.append(job)
    jobs.sort(key=lambda j: j["job_posted_at_datetime_utc"], reverse=True)
    return jobs, links
