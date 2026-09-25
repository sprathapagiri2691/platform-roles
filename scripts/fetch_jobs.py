"""Fetch newly posted jobs from the JSearch API and write a daily Markdown report.

JSearch returns Google for Jobs results, which cover LinkedIn, Indeed, Glassdoor,
ZipRecruiter, company career sites and other boards. Reads searches from
searches.yml, needs JSEARCH_API_KEY (a RapidAPI key) in the environment, and
writes jobs/<date>.md plus jobs/latest.md.
"""

import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

import hn_jobs
import startup_jobs

API_URL = "https://jsearch.p.rapidapi.com/search-v2"
API_HOST = "jsearch.p.rapidapi.com"
KM_PER_MILE = 1.609344
TIMEOUT_SECONDS = 120
ATTEMPTS = 2
ROOT = Path(__file__).resolve().parent.parent
TIMEZONE = ZoneInfo("America/Los_Angeles")
STARTUP_SECTION = "Startups (company career sites)"
HN_SECTION = "Startups (Hacker News: Who is hiring?)"
DISCOVERED_BOARDS = ROOT / "data" / "discovered_boards.json"
URL_IN_TEXT = re.compile(r"https?://[^\s)\]>\"']+")


def fetch(role, config, api_key):
    params = {
        "query": f"{role} in {config['location']}",
        "country": config.get("country", "us"),
        "radius": round(config.get("radius_miles", 100) * KM_PER_MILE),
        "date_posted": config.get("date_posted", "today"),
        "page": 1,
        "num_pages": config.get("pages_per_role", 1),
    }
    if config.get("employment_types"):
        params["employment_types"] = config["employment_types"]
    request = urllib.request.Request(
        API_URL + "?" + urllib.parse.urlencode(params),
        headers={
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": API_HOST,
            "User-Agent": "platform-jobs-daily",
        },
    )
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
                data = json.load(resp).get("data") or []
            break
        except (TimeoutError, urllib.error.URLError) as err:
            # Retry slow responses and server errors once; client errors
            # such as a bad key or a missing endpoint won't fix themselves.
            client_error = isinstance(err, urllib.error.HTTPError) and err.code < 500
            if client_error or attempt == ATTEMPTS:
                raise
            print(f"Search '{role}' attempt {attempt} failed ({err}); retrying", file=sys.stderr)
            time.sleep(10)
    # search-v2 may wrap the job list in an object rather than return it directly.
    if isinstance(data, dict):
        data = data.get("jobs") or data.get("data") or []
    return data


def word_pattern(words, whole_word=False):
    """Case-insensitive regex matching any of `words` at a word start."""
    if not words:
        return None
    end = r"\b" if whole_word else ""
    alternatives = "|".join(re.escape(str(w)) for w in words)
    return re.compile(rf"(?<!\w)(?:{alternatives}){end}", re.IGNORECASE)


def regex_any(patterns):
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE) if patterns else None


def description(job):
    """The posting's full text: description plus any highlight bullet lists."""
    parts = [job.get("job_description") or ""]
    for items in (job.get("job_highlights") or {}).values():
        parts.extend(str(i) for i in items or [])
    return "\n".join(parts)


def mentions_sponsorship(job, pattern):
    """True when the posting mentions sponsorship (call only on jobs that passed the filter)."""
    return bool(pattern and pattern.search(description(job)))


class JobFilter:
    def __init__(self, filters):
        self.title = word_pattern(filters.get("title_keywords"))
        self.title_role = word_pattern(filters.get("title_role_keywords"))
        self.title_exclude = word_pattern(filters.get("title_exclude_keywords"), whole_word=True)
        self.contract_title = word_pattern(filters.get("contract_title_keywords"), whole_word=True)
        self.contract_description = word_pattern(
            filters.get("contract_description_phrases"), whole_word=True)
        self.staffing_name = word_pattern(filters.get("staffing_name_keywords"))
        self.staffing_employer = word_pattern(filters.get("staffing_employers"), whole_word=True)
        self.no_sponsorship = regex_any(filters.get("no_sponsorship_patterns"))
        self.sponsorship = regex_any(filters.get("sponsorship_patterns"))
        self.only_sponsoring = bool(filters.get("only_jobs_mentioning_sponsorship"))

    def skip_reason(self, job):
        """Why a job should be left out of the report, or None to keep it."""
        employer = job.get("employer_name") or ""
        company_type = str(job.get("employer_company_type") or "")
        if (self.staffing_name and self.staffing_name.search(employer)) or \
                (self.staffing_employer and self.staffing_employer.search(employer)) or \
                re.search(r"staffing|consult", company_type, re.IGNORECASE):
            return "staffing/consulting firm"

        types = job.get("job_employment_types") or [job.get("job_employment_type")]
        types = " ".join(re.sub(r"[^A-Z]", "", str(t).upper()) for t in types if t)
        if re.search("CONTRACT|PARTTIME|TEMPORARY|INTERN", types):
            return "contract/part-time"
        if types and "FULLTIME" not in types:
            return "not full-time"

        title = job.get("job_title") or ""
        if (self.contract_title and self.contract_title.search(title)) or \
                (self.contract_description and
                 self.contract_description.search(job.get("job_description") or "")):
            return "contract/part-time"
        text = description(job)
        if self.no_sponsorship and self.no_sponsorship.search(text):
            return "no visa sponsorship"
        if self.only_sponsoring and not (self.sponsorship and self.sponsorship.search(text)):
            return "sponsorship not mentioned"
        if (self.title and not self.title.search(title)) or \
                (self.title_role and not self.title_role.search(title)) or \
                (self.title_exclude and self.title_exclude.search(title)):
            return "off-topic title"
        return None


def escape(text):
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def link(label, url):
    return f"[{escape(label)}]({url})" if url else ""


def apply_option(job):
    """Best place to apply: the employer's own site if listed, else the job board."""
    options = [o for o in job.get("apply_options") or [] if o.get("apply_link")]
    direct = [o for o in options if o.get("is_direct")]
    if direct:
        return "company site", direct[0]["apply_link"]
    if job.get("job_apply_link"):
        where = "company site" if job.get("job_apply_is_direct") else job.get("job_publisher")
        return where or "job board", job["job_apply_link"]
    if options:
        return options[0].get("publisher") or "job board", options[0]["apply_link"]
    return None, None


def company(job):
    name = escape(job.get("employer_name"))
    website = job.get("employer_website")
    return f"[{name}]({website})" if website and name else name


def location(job):
    parts = [job.get("job_city"), job.get("job_state")]
    text = ", ".join(p for p in parts if p)
    if job.get("job_is_remote"):
        text = f"{text} (remote)" if text else "Remote"
    return escape(text)


def salary(job):
    if job.get("salary_text"):
        return escape(job["salary_text"])
    low, high = job.get("job_min_salary"), job.get("job_max_salary")
    if not low and not high:
        return ""
    period = (job.get("job_salary_period") or "").lower()
    suffix = f" /{period}" if period else ""
    if low and high and round(low) != round(high):
        return f"{low:,.0f} – {high:,.0f}{suffix}"
    return f"{(low or high):,.0f}{suffix}"


def render(date, config, sections, sponsorship):
    lines = [f"# Jobs posted — {date}", ""]
    total = sum(len(jobs) for _, jobs in sections if jobs is not None)
    lines += [
        f"**{total}** jobs within about {config.get('radius_miles', 100)} miles of "
        f"{config['location']}: full-time only, staffing/consulting firms and "
        f"contract roles excluded, and jobs that rule out visa sponsorship. "
        f"Each job is listed once, under the first search that found it.",
        "",
        "✅ in the H-1B column means the posting mentions visa sponsorship. A blank means it "
        "doesn't say, not that the company won't sponsor.",
        "",
    ]
    for role, jobs in sections:
        lines += [f"## {role}", ""]
        if jobs is None:
            lines += ["_Search failed; see the workflow log._", ""]
            continue
        if not jobs:
            lines += ["_No new matching postings._", ""]
            continue
        lines.append("| Job | Apply | H-1B | Company | Location | Posted | Salary |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for job in jobs:
            where, url = apply_option(job)
            title = job.get("job_title")
            lines.append(
                f"| {link(title, url) or escape(title)} | {link(f'Apply on {where}', url) or '—'} "
                f"| {'✅' if mentions_sponsorship(job, sponsorship) else ''} "
                f"| {company(job)} | {location(job)} "
                f"| {str(job.get('job_posted_at_datetime_utc') or '')[:10]} | {salary(job)} |"
            )
        lines.append("")
    return "\n".join(lines)


def dedupe_key(job):
    """Same posting across searches and sources: title plus the employer's first word
    (so "Chime" and "Chime Financial, Inc" match). search-v2 job_ids differ between queries."""
    title = re.sub(r"\W+", " ", str(job.get("job_title") or "").lower()).strip()
    employer = re.sub(r"\W+", " ", str(job.get("employer_name") or "").lower()).split()
    return title, employer[0] if employer else ""


def keep_jobs(source, results, seen, job_filter):
    """Drop duplicates and filtered jobs, log what's left with links, return it."""
    jobs = []
    skipped = Counter()
    for job in results:
        key = dedupe_key(job)
        if key in seen:
            skipped["duplicate"] += 1
            continue
        seen.add(key)
        reason = job_filter.skip_reason(job)
        if reason:
            skipped[reason] += 1
        else:
            jobs.append(job)
    details = ", ".join(f"{n} {reason}" for reason, n in skipped.most_common())
    print(f"{source}: {len(results)} found, {len(jobs)} kept" + (f" (skipped {details})" if details else ""))
    if results and "job_title" not in results[0]:
        print(f"Unexpected job fields: {sorted(results[0])}", file=sys.stderr)
    if results and not any(description(j).strip() for j in results):
        print("  Note: no job descriptions returned, so sponsorship and contract "
              "wording could not be checked.", file=sys.stderr)
    for job in jobs:
        url = apply_option(job)[1] or "(no link)"
        visa = " | ✅ mentions sponsorship" if mentions_sponsorship(job, job_filter.sponsorship) else ""
        print(f"  - {job.get('job_title')} | {job.get('employer_name')} | {location(job)}{visa}\n    {url}")
    return jobs


def load_yaml(name):
    path = ROOT / name
    return (yaml.safe_load(path.read_text()) or {}) if path.exists() else None


def load_discovered():
    try:
        return json.loads(DISCOVERED_BOARDS.read_text())
    except FileNotFoundError:
        return []


def save_discovered(entries):
    DISCOVERED_BOARDS.parent.mkdir(exist_ok=True)
    entries = sorted(entries, key=lambda e: (e["name"].lower(), e["ats"], e["board"]))
    DISCOVERED_BOARDS.write_text(json.dumps(entries, indent=2) + "\n")


def job_links(job):
    links = [job.get("job_apply_link")]
    links += [o.get("apply_link") for o in job.get("apply_options") or []]
    return [l for l in links if l] + URL_IN_TEXT.findall(job.get("job_description") or "")


def discover_boards(startup_config, companies, discovered, searched, hn_posts, hn_links, date):
    """Add job boards linked from search results and HN posts to `discovered`."""
    excluded = {str(k).lower() for k in startup_config.get("exclude_boards") or []}
    known = {startup_jobs.board_key(c["ats"], c["board"]) for c in companies + discovered}
    candidates = []
    for job in [j for _, results in searched for j in results or []] + (hn_posts or []):
        for ats, board in startup_jobs.boards_in(job_links(job)):
            candidates.append((ats, board, job.get("employer_name") or ""))
    for link, company in hn_links:
        candidates += [(ats, board, company) for ats, board in startup_jobs.boards_in([link])]
    new = startup_jobs.discover(candidates, known, excluded)
    for entry in new:
        entry["first_seen"] = date
    if new:
        print(f"Discovered {len(new)} new startup job boards: " + ", ".join(e["name"] for e in new))
    discovered.extend(new)


def fetch_startups(startup_config, companies, discovered, seen, job_filter):
    """The startup-board section, or None when every board failed."""
    excluded = {str(k).lower() for k in startup_config.get("exclude_boards") or []}
    boards = {}
    for company in companies + discovered:
        key = startup_jobs.board_key(company["ats"], company["board"])
        if key not in excluded:
            boards.setdefault(key, company)
    results, failed = startup_jobs.fetch_all(startup_config, list(boards.values()))
    # Forget discovered boards that no longer exist.
    gone = {key for key, err in failed.items()
            if isinstance(err, urllib.error.HTTPError) and err.code == 404}
    discovered[:] = [d for d in discovered
                     if startup_jobs.board_key(d["ats"], d["board"]) not in gone]
    print(f"Checked {len(boards)} startup job boards ({len(companies)} listed, "
          f"{len(discovered)} discovered, {len(failed)} failed)")
    if boards and len(failed) == len(boards):
        return None
    return keep_jobs(STARTUP_SECTION, results, seen, job_filter)


def main():
    api_key = os.environ.get("JSEARCH_API_KEY")
    if not api_key:
        sys.exit("JSEARCH_API_KEY must be set")

    config = yaml.safe_load((ROOT / "searches.yml").read_text())
    sections = []
    seen = set()
    failures = 0
    job_filter = JobFilter(config.get("filters") or {})
    date = datetime.datetime.now(TIMEZONE).date().isoformat()

    # 1. Collect raw results from every source.
    roles = config.get("roles", [])
    searched = []
    for role in roles:
        try:
            searched.append((role, fetch(role, config, api_key)))
        except (OSError, ValueError) as err:
            detail = ""
            if isinstance(err, urllib.error.HTTPError):
                detail = " — " + err.read().decode(errors="replace")[:500]
            print(f"Search '{role}' failed: {err}{detail}", file=sys.stderr)
            searched.append((role, None))
            failures += 1

    startup_config = load_yaml("startups.yml")
    hn_posts, hn_links = None, []
    hn_config = (startup_config or {}).get("hacker_news") or {}
    if startup_config is not None and hn_config.get("enabled"):
        try:
            hn_posts, hn_links = hn_jobs.fetch_posts(
                hn_config, startup_jobs.place_pattern(startup_config))
        except (OSError, ValueError, KeyError) as err:
            print(f"Hacker News failed: {err}", file=sys.stderr)

    # 2. Find more startups from the links in those results, then check
    #    every startup board. Startups go first so a job also found on
    #    LinkedIn keeps its direct company link.
    startups = None
    if startup_config is not None:
        companies = list(startup_config.get("companies") or [])
        discovered = load_discovered()
        if startup_config.get("discover_boards"):
            discover_boards(startup_config, companies, discovered, searched, hn_posts, hn_links, date)
        startups = fetch_startups(startup_config, companies, discovered, seen, job_filter)
        save_discovered(discovered)
        sections.append((STARTUP_SECTION, startups))
    if hn_config.get("enabled"):
        sections.append((HN_SECTION, None if hn_posts is None
                         else keep_jobs(HN_SECTION, hn_posts, seen, job_filter)))

    # 3. Then the JSearch results.
    for role, results in searched:
        sections.append((role, None if results is None else keep_jobs(role, results, seen, job_filter)))

    report = render(date, config, sections, job_filter.sponsorship)
    out_dir = ROOT / "jobs"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{date}.md").write_text(report)
    (out_dir / "latest.md").write_text(report)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(report + "\n")

    if roles and failures == len(roles) and startups is None and hn_posts is None:
        sys.exit("All searches failed")


if __name__ == "__main__":
    main()
