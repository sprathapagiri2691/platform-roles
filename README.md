# platform-roles

This repo helps with applying for platform engineering roles.

## Daily job postings

A GitHub Actions workflow ([.github/workflows/daily-jobs.yml](.github/workflows/daily-jobs.yml)) runs every evening around 7 PM Pacific and searches for full-time jobs posted that day within 100 miles of San Francisco. Results come from the [JSearch API](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch), which returns Google for Jobs listings from LinkedIn, Indeed, Glassdoor, ZipRecruiter, company career sites and other boards.

[searches.yml](searches.yml) sets the searches and the filters. Jobs are left out when they are:
- posted by staffing, recruiting or consulting firms (by name keyword or a list of known firms)
- contract, part-time or temporary, or mention C2C, W2 or 1099
- off-topic, meaning the title has none of the target keywords (platform, cloud, DevOps, SRE, infrastructure, ...)

The report is committed to [jobs/latest.md](jobs/latest.md) (plus a dated copy in `jobs/`), shown on each run's summary page, and printed with links in the run log. Each job title links to the posting, and the Apply column says where it opens: the company's own careers site when JSearch has that link, otherwise the job board it was found on, such as LinkedIn. Company names link to the company's website.

The free JSearch plan allows about 200 requests a month. Each search costs one request per run (6 searches, about 180 a month), so adding searches or pages may need a paid plan.

### Setup

1. Sign up at https://rapidapi.com, subscribe to the JSearch API (free Basic plan), and copy your RapidAPI key.
2. In the GitHub repo, go to Settings → Secrets and variables → Actions and add a secret named `JSEARCH_API_KEY`.
3. Edit [searches.yml](searches.yml) to change roles, location or radius.
4. Run it once from the Actions tab (Daily job postings → Run workflow) to check it works.
