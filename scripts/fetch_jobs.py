"""Fetch newly posted jobs from the JSearch API and write a daily Markdown report.

JSearch returns Google for Jobs results, which cover LinkedIn, Indeed, Glassdoor,
ZipRecruiter, company career sites and other boards. Reads searches from
searches.yml, needs JSEARCH_API_KEY (a RapidAPI key) in the environment, and
writes jobs/<date>.md plus jobs/latest.md.
"""

import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

API_URL = "https://jsearch.p.rapidapi.com/search-v2"
API_HOST = "jsearch.p.rapidapi.com"
KM_PER_MILE = 1.609344
TIMEOUT_SECONDS = 120
ATTEMPTS = 2
ROOT = Path(__file__).resolve().parent.parent


def fetch(role, config, api_key):
    params = {
        "query": f"{role} in {config['location']}",
        "country": config.get("country", "us"),
        "radius": round(config.get("radius_miles", 100) * KM_PER_MILE),
        "date_posted": config.get("date_posted", "today"),
        "page": 1,
        "num_pages": config.get("pages_per_role", 1),
    }
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
    low, high = job.get("job_min_salary"), job.get("job_max_salary")
    if not low and not high:
        return ""
    period = (job.get("job_salary_period") or "").lower()
    suffix = f" /{period}" if period else ""
    if low and high and round(low) != round(high):
        return f"{low:,.0f} – {high:,.0f}{suffix}"
    return f"{(low or high):,.0f}{suffix}"


def render(date, config, sections):
    lines = [f"# Jobs posted — {date}", ""]
    total = sum(len(jobs) for _, jobs in sections if jobs is not None)
    lines += [
        f"**{total}** jobs within {config.get('radius_miles', 100)} miles of "
        f"{config['location']}. Each job is listed once, under the first role that found it.",
        "",
    ]
    for role, jobs in sections:
        lines += [f"## {role}", ""]
        if jobs is None:
            lines += ["_Search failed; see the workflow log._", ""]
            continue
        if not jobs:
            lines += ["_No postings other than those listed above._", ""]
            continue
        lines.append("| Job | Apply | Company | Location | Posted | Salary |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for job in jobs:
            where, url = apply_option(job)
            title = job.get("job_title")
            lines.append(
                f"| {link(title, url) or escape(title)} | {link(f'Apply on {where}', url) or '—'} "
                f"| {company(job)} | {location(job)} "
                f"| {str(job.get('job_posted_at_datetime_utc') or '')[:10]} | {salary(job)} |"
            )
        lines.append("")
    return "\n".join(lines)


def main():
    api_key = os.environ.get("JSEARCH_API_KEY")
    if not api_key:
        sys.exit("JSEARCH_API_KEY must be set")

    config = yaml.safe_load((ROOT / "searches.yml").read_text())
    sections = []
    seen = set()
    failures = 0
    for role in config.get("roles", []):
        try:
            results = fetch(role, config, api_key)
        except (OSError, ValueError) as err:
            detail = ""
            if isinstance(err, urllib.error.HTTPError):
                detail = " — " + err.read().decode(errors="replace")[:500]
            print(f"Search '{role}' failed: {err}{detail}", file=sys.stderr)
            sections.append((role, None))
            failures += 1
            continue
        # Overlapping roles (e.g. "Platform Engineer" and "Senior Platform
        # Engineer") return the same postings; list each job only once.
        jobs = []
        for job in results:
            # search-v2 job_ids differ between queries, so match on the posting itself.
            key = tuple(str(job.get(f) or "").strip().lower()
                        for f in ("job_title", "employer_name", "job_city"))
            if key not in seen:
                seen.add(key)
                jobs.append(job)
        print(f"{role}: {len(results)} found, {len(jobs)} new")
        if results and "job_title" not in results[0]:
            print(f"Unexpected job fields: {sorted(results[0])}", file=sys.stderr)
        sections.append((role, jobs))

    date = datetime.date.today().isoformat()
    report = render(date, config, sections)
    out_dir = ROOT / "jobs"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{date}.md").write_text(report)
    (out_dir / "latest.md").write_text(report)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(report + "\n")

    if sections and failures == len(sections):
        sys.exit("All searches failed")


if __name__ == "__main__":
    main()
