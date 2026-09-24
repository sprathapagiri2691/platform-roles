# platform-roles

This repo helps with applying for platform engineering roles.

## Daily job postings

A GitHub Actions workflow ([.github/workflows/daily-jobs.yml](.github/workflows/daily-jobs.yml)) runs every day and searches for jobs posted in the last day for each role in [searches.yml](searches.yml), within 100 miles of San Francisco. Results come from the [JSearch API](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch), which returns Google for Jobs listings from LinkedIn, Indeed, Glassdoor, ZipRecruiter, company career sites and other boards.

The report is committed to [jobs/latest.md](jobs/latest.md) (plus a dated copy in `jobs/`). Each job title links to the posting, and the Apply column says where it opens: the company's own careers site when JSearch has that link, otherwise the job board it was found on, such as LinkedIn. Company names link to the company's website.

### Setup

1. Sign up at https://rapidapi.com, subscribe to the JSearch API (free Basic plan), and copy your RapidAPI key.
2. In the GitHub repo, go to Settings → Secrets and variables → Actions and add a secret named `JSEARCH_API_KEY`.
3. Edit [searches.yml](searches.yml) to change roles, location or radius.
4. Run it once from the Actions tab (Daily job postings → Run workflow) to check it works.
