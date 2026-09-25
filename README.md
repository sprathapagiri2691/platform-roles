# platform-roles

This repo helps with applying for platform engineering roles.

## Daily job postings

A GitHub Actions workflow ([.github/workflows/daily-jobs.yml](.github/workflows/daily-jobs.yml)) runs every evening around 7 PM Pacific and searches for full-time jobs posted that day within 100 miles of San Francisco. Results come from the [JSearch API](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch), which returns Google for Jobs listings from LinkedIn, Indeed, Glassdoor, ZipRecruiter, company career sites and other boards.

[searches.yml](searches.yml) sets the searches and the filters. Jobs are left out when they are:
- posted by staffing, recruiting or consulting firms (by name keyword or a list of known firms)
- contract, part-time or temporary, or mention C2C, W2 or 1099
- off-topic, meaning the title has none of the target keywords (platform, cloud, DevOps, SRE, infrastructure, ...)
- ruling out visa sponsorship ("unable to sponsor", "US citizens only", security clearance, ...). Jobs that mention H-1B or visa sponsorship get a ✅ in the H-1B column; a blank means the posting doesn't say.

The report is committed to [jobs/latest.md](jobs/latest.md) (plus a dated copy in `jobs/`), shown on each run's summary page, and printed with links in the run log. Each job title links to the posting, and the Apply column says where it opens: the company's own careers site when JSearch has that link, otherwise the job board it was found on, such as LinkedIn. Company names link to the company's website.

The free JSearch plan allows about 200 requests a month. Each search costs one request per run (6 searches, about 180 a month), so adding searches or pages may need a paid plan.

### Setup

1. Sign up at https://rapidapi.com, subscribe to the JSearch API (free Basic plan), and copy your RapidAPI key.
2. In the GitHub repo, go to Settings → Secrets and variables → Actions and add a secret named `JSEARCH_API_KEY`.
3. Edit [searches.yml](searches.yml) to change roles, location or radius.
4. Run it once from the Actions tab (Daily job postings → Run workflow) to check it works.

## Application helper

[apply/apply_helper.py](apply/apply_helper.py) opens a job application in Chrome, fills in the fields it recognizes from your profile, attaches your resume, and lists the required questions it couldn't answer. **It never submits.** You review everything and click Submit yourself.

It works best on Greenhouse (`boards.greenhouse.io`, `job-boards.greenhouse.io`) and Lever (`jobs.lever.co`) forms; other company sites are best effort. LinkedIn is refused because its terms forbid automation, so follow LinkedIn's Apply button to the company's site and use that URL. Voluntary demographic (EEO) questions are always left for you.

### Setup (once)

```bash
cd ~/Desktop/platform-jobs
python3 -m venv .venv
source .venv/bin/activate
pip install playwright pyyaml
cp apply/profile.example.yml apply/profile.yml   # then fill in your details
```

`apply/profile.yml`, the browser data and resume files are in `.gitignore`, so they stay on your Mac. It uses your installed Google Chrome.

### Use

```bash
source .venv/bin/activate
python apply/apply_helper.py "https://job-boards.greenhouse.io/company/jobs/123"
```

Chrome opens with the form filled in. The terminal lists what was filled and what's still needed. Review, finish, submit, then press Enter in the terminal to close the browser.
