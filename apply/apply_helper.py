"""Open a job application in Chrome and fill it in from apply/profile.yml.

The script never submits anything: it fills the fields it recognises,
attaches your resume, lists what is left for you, and waits while you
review and click Submit yourself.

Works best on Greenhouse and Lever forms; other sites are best effort.
LinkedIn is refused, because its terms forbid automation.

Usage:
    python apply/apply_helper.py <job URL>
"""

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
PROFILE = HERE / "profile.yml"
BROWSER_DATA = HERE / ".browser-profile"

# Question text pattern -> profile key. Checked in order, so more specific
# patterns come first (e.g. sponsorship before work authorization).
TEXT_FIELDS = [
    (r"first\s*name|given name|preferred name", "first_name"),
    (r"last\s*name|surname|family name", "last_name"),
    (r"^(full\s*)?name$|^your name$", "full_name"),
    (r"e-?mail", "email"),
    (r"phone|mobile", "phone"),
    (r"linkedin", "linkedin_url"),
    (r"github", "github_url"),
    (r"website|portfolio|personal site|other url", "website_url"),
    (r"current (company|employer)|^company$|^org$|organi[sz]ation", "current_company"),
    (r"current (job )?(title|role|position)", "current_title"),
    (r"^location|\bcity\b|where are you (located|based)", "location"),
]
YES_NO_FIELDS = [
    (r"sponsor", "needs_sponsorship"),
    (r"authori[sz]ed to work|legally (authori[sz]ed|eligible)|eligible to work", "work_authorized"),
]
# Voluntary self-identification questions stay for the applicant to answer.
SKIP = re.compile(
    r"gender|race|ethnic|veteran|disabilit|hispanic|latino|sexual orientation|pronoun",
    re.IGNORECASE,
)

# Tags every form control in a frame and describes it, so Python can match
# its question text and fill it through a stable selector.
DESCRIBE_FIELDS_JS = """
() => {
  const text = (el) => (el ? el.textContent : "").replace(/\\s+/g, " ").trim();
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const fields = [];
  document.querySelectorAll("input, textarea, select").forEach((el, i) => {
    const type = (el.getAttribute("type") || el.tagName).toLowerCase();
    if (["hidden", "submit", "button", "image", "reset", "search"].includes(type)) return;
    if (type !== "file" && !visible(el)) return;
    const id = `f${i}`;
    el.setAttribute("data-apply-helper", id);
    let label = [...(el.labels || [])].map(text).join(" ");
    const labelledBy = el.getAttribute("aria-labelledby");
    if (!label && labelledBy) {
      label = labelledBy.split(/\\s+/).map((ref) => text(document.getElementById(ref))).join(" ");
    }
    if (!label) label = el.getAttribute("aria-label") || "";
    if (!label) {
      const container = el.closest("fieldset, .field, .application-question, [class*='question']");
      if (container) label = text(container.querySelector("legend, label, .application-label") || container);
    }
    fields.push({
      id,
      tag: el.tagName.toLowerCase(),
      type,
      label: label.slice(0, 300),
      name: el.getAttribute("name") || el.id || "",
      placeholder: el.getAttribute("placeholder") || "",
      // A dropdown still on its first (placeholder) option counts as empty.
      value: el.type === "checkbox" || el.type === "radio" ? ""
        : el.tagName === "SELECT" ? (el.selectedIndex > 0 ? el.value : "")
        : (el.value || ""),
      required: el.required || el.getAttribute("aria-required") === "true" || /[*✱]/.test(label),
      options: el.tagName === "SELECT" ? [...el.options].map((o) => o.text.trim()) : [],
    });
  });
  return fields;
}
"""


def load_profile():
    if not PROFILE.exists():
        sys.exit(
            f"{PROFILE} not found. Create it with:\n"
            "  cp apply/profile.example.yml apply/profile.yml\n"
            "then fill in your details."
        )
    profile = yaml.safe_load(PROFILE.read_text()) or {}
    first, last = profile.get("first_name") or "", profile.get("last_name") or ""
    profile["full_name"] = f"{first} {last}".strip()
    for key in ("resume_path", "cover_letter_path"):
        if profile.get(key):
            path = Path(profile[key]).expanduser()
            if not path.exists():
                sys.exit(f"{key} points to a missing file: {path}")
            profile[key] = str(path)
    return profile


def application_url(url):
    host = urlparse(url).netloc.lower()
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        sys.exit(
            "LinkedIn is not supported: its terms forbid automated applications.\n"
            "Open the job on LinkedIn, follow 'Apply' to the company's site, and run this\n"
            "script with that URL instead."
        )
    # Lever shows the job description first; the form lives at .../apply.
    if host == "jobs.lever.co" and not url.rstrip("/").endswith("/apply"):
        return url.split("?")[0].rstrip("/") + "/apply"
    return url


def question_text(field):
    return " ".join(p for p in (field["label"], field["placeholder"], field["name"]) if p)


def normalise(text):
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def answer_for(field, profile):
    """Return (profile value, description) for a field, or (None, None)."""
    label = normalise(field["label"] or field["placeholder"])
    text = question_text(field)
    if SKIP.search(text):
        return None, None
    for pattern, key in YES_NO_FIELDS:
        if re.search(pattern, text, re.IGNORECASE):
            return profile.get(key) or None, key
    for rule in profile.get("answers") or []:
        if rule.get("answer") and re.search(rule["match"], text, re.IGNORECASE):
            return rule["answer"], f"answer '{rule['match']}'"
    for pattern, key in TEXT_FIELDS:
        # Match whole-label patterns (^...$) against the cleaned label or the
        # field's name attribute; others against the whole question text.
        haystacks = [label, normalise(field["name"])] if pattern.startswith("^") else [text]
        if any(re.search(pattern, h, re.IGNORECASE) for h in haystacks):
            return profile.get(key) or None, key
    return None, None


def pick_option(options, wanted):
    wanted = str(wanted).strip().lower()
    for option in options:
        if option.strip().lower() == wanted:
            return option
    for option in options:
        if option.strip().lower().startswith(wanted):
            return option
    return None


def fill_frame(frame, profile, report):
    try:
        fields = frame.evaluate(DESCRIBE_FIELDS_JS)
    except PlaywrightError:
        return  # Cross-origin or detached frame.
    resume_done = False
    for field in fields:
        locator = frame.locator(f'[data-apply-helper="{field["id"]}"]')
        text = question_text(field)
        try:
            if field["type"] == "file":
                is_cover = re.search(r"cover", text, re.IGNORECASE)
                path = profile.get("cover_letter_path") if is_cover else profile.get("resume_path")
                if path and (is_cover or not resume_done):
                    locator.set_input_files(path)
                    report["filled"].append(f"{'cover letter' if is_cover else 'resume'} upload")
                    resume_done = resume_done or not is_cover
                continue
            if field["type"] in ("checkbox", "radio"):
                if field["required"]:
                    report["todo"].append(text or field["name"])
                continue
            if field["value"]:
                continue  # Never overwrite what's already there.
            value, source = answer_for(field, profile)
            if value is None:
                if field["required"]:
                    report["todo"].append(text or field["name"])
                continue
            if field["tag"] == "select":
                option = pick_option(field["options"], value)
                if not option:
                    report["todo"].append(text)
                    continue
                locator.select_option(label=option)
            else:
                locator.fill(str(value))
            report["filled"].append(f"{source}: {field['label'][:60] or field['name']}")
        except PlaywrightError as err:
            report["todo"].append(f"{text[:80]} (could not fill: {str(err).splitlines()[0]})")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", help="job application URL (Greenhouse, Lever or company site)")
    parser.add_argument("--browser", default="chrome",
                        help="Playwright browser channel: chrome (default), msedge, or chromium")
    args = parser.parse_args()

    profile = load_profile()
    url = application_url(args.url)

    with sync_playwright() as p:
        channel = None if args.browser == "chromium" else args.browser
        # A persistent profile keeps logins (e.g. Workday accounts) between runs.
        context = p.chromium.launch_persistent_context(
            str(BROWSER_DATA), channel=channel, headless=False, no_viewport=True)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightError:
            pass  # Some sites never go idle; fill what has loaded.

        report = {"filled": [], "todo": []}
        for frame in page.frames:
            fill_frame(frame, profile, report)

        print(f"\nFilled {len(report['filled'])} fields on {page.url}")
        for item in report["filled"]:
            print(f"  ✓ {item}")
        if report["todo"]:
            print("\nStill needed (required fields I couldn't fill):")
            for item in dict.fromkeys(report["todo"]):
                print(f"  • {item[:120]}")
        if not report["filled"]:
            print("\nNo form fields recognised. The page may need an 'Apply' click or a login first;")
            print("do that in the browser, then run this script again with the form's URL.")
        print("\nReview every field in the browser and click Submit yourself.")
        input("Press Enter here when you're done to close the browser... ")
        context.close()


if __name__ == "__main__":
    main()
