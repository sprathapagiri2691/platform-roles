"""Fetch open jobs from startups' own Greenhouse, Lever and Ashby job boards.

The boards are listed in startups.yml. Their public APIs need no key. Jobs
are returned in the same shape as JSearch results so fetch_jobs.py can
filter and render them the same way.
"""

import concurrent.futures
import datetime
import html
import json
import re
import sys
import urllib.request

TIMEOUT_SECONDS = 30


def get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "platform-jobs-daily"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
        return json.load(resp)


def html_to_text(markup):
    text = re.sub(r"<[^>]+>", " ", html.unescape(markup or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_time(value):
    """ISO string or epoch milliseconds -> aware datetime, or None."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.datetime.fromtimestamp(value / 1000, datetime.timezone.utc)
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def job(company, title, locations, url, posted, description="", employment_type="", salary=""):
    return {
        "job_title": title,
        "employer_name": company,
        "job_city": "; ".join(dict.fromkeys(
            part.strip() for l in locations for part in re.split(r"[;|]", l or "") if part.strip())),
        "job_apply_link": url,
        "job_apply_is_direct": True,
        "job_description": description,
        "job_employment_type": employment_type,
        "job_posted_at": posted,
        "job_posted_at_datetime_utc": posted.isoformat() if posted else "",
        "salary_text": salary,
    }


def greenhouse(company, board):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
    for j in data.get("jobs", []):
        locations = [(j.get("location") or {}).get("name", "")]
        locations += [o.get("location") or o.get("name") or "" for o in j.get("offices") or []]
        yield job(company, j.get("title", ""), locations, j.get("absolute_url"),
                  parse_time(j.get("first_published") or j.get("updated_at")),
                  html_to_text(j.get("content")))


def lever(company, board):
    for j in get_json(f"https://api.lever.co/v0/postings/{board}?mode=json"):
        categories = j.get("categories") or {}
        locations = [categories.get("location", "")] + list(categories.get("allLocations") or [])
        if j.get("workplaceType") == "remote":
            locations.append("Remote")
        lists = " ".join(f"{l.get('text', '')} {html_to_text(l.get('content'))}" for l in j.get("lists") or [])
        description = " ".join([j.get("descriptionPlain") or "", lists, j.get("additionalPlain") or ""])
        yield job(company, j.get("text", ""), locations, j.get("hostedUrl"),
                  parse_time(j.get("createdAt")), description, categories.get("commitment", ""))


def ashby(company, board):
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true")
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locations = [j.get("location", "")]
        locations += [s.get("location", "") for s in j.get("secondaryLocations") or []]
        if j.get("workplaceType") == "Remote":
            locations.append("Remote")
        salary = (j.get("compensation") or {}).get("compensationTierSummary", "")
        yield job(company, j.get("title", ""), locations, j.get("jobUrl"),
                  parse_time(j.get("publishedAt")), j.get("descriptionPlain") or "",
                  j.get("employmentType", ""), salary)


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}


def fetch_all(config):
    """Return (jobs in the Bay Area posted recently, number of boards that failed)."""
    places = [re.escape(p) for p in config.get("locations") or []]
    place_pattern = re.compile(rf"(?<!\w)(?:{'|'.join(places)})(?!\w)", re.IGNORECASE) if places else None
    include_remote = bool(config.get("include_remote"))
    max_age = int(config.get("max_age_days") or 0)
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=max_age)
              if max_age else None)

    def load(company):
        fetcher = FETCHERS.get(company.get("ats"))
        if not fetcher:
            raise ValueError(f"unknown ats '{company.get('ats')}'")
        return list(fetcher(company["name"], company["board"]))

    companies = config.get("companies") or []
    jobs, failures = [], 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for company, future in [(c, pool.submit(load, c)) for c in companies]:
            try:
                board_jobs = future.result()
            except (OSError, ValueError, KeyError) as err:
                print(f"Startup board {company.get('name')} ({company.get('ats')}/"
                      f"{company.get('board')}) failed: {err}", file=sys.stderr)
                failures += 1
                continue
            for j in board_jobs:
                where = j["job_city"]
                local = bool(place_pattern and place_pattern.search(where))
                remote = include_remote and re.search(r"remote", where, re.IGNORECASE)
                recent = cutoff is None or (j["job_posted_at"] and j["job_posted_at"] >= cutoff)
                if (local or remote) and recent:
                    jobs.append(j)
    for j in jobs:
        del j["job_posted_at"]  # datetime isn't needed past this point
    jobs.sort(key=lambda j: j["job_posted_at_datetime_utc"], reverse=True)
    return jobs, failures
