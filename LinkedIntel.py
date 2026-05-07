#!/usr/bin/env python3
"""
LinkedIntel.py — LinkedIn Employee Enumeration & Email Generation
For authorized penetration testing and red team engagements only.

Usage:
  python LinkedIntel.py --company "Acme Corp" --domain acme.com --format firstname.lastname
  python LinkedIntel.py --company-id 1234567 --domain acme.com --format f.lastname
  python LinkedIntel.py --company acmecorp --domain acme.com --format f.lastname -o results.csv
"""

import argparse
import csv
import io
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import random
import unicodedata
from pathlib import Path
from typing import NamedTuple, Optional

# ── Dependency check ──────────────────────────────────────────────────────────

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import (
        TimeoutException,
        NoSuchElementException,
        StaleElementReferenceException,
        WebDriverException,
    )
except ImportError:
    print("[ERROR] selenium not installed. Run: pip install selenium")
    sys.exit(1)


# ── Constants ─────────────────────────────────────────────────────────────────

LINKEDIN_BASE        = "https://www.linkedin.com"
LINKEDIN_LOGIN       = "https://www.linkedin.com/login"
LINKEDIN_FEED        = "https://www.linkedin.com/feed"
COOKIES_FILE         = Path(".linkedin_cookies.json")
CONFIG_FILE          = Path(".linkedintel_config.json")

# LinkedIn search: people at a specific company (by numeric ID)
SEARCH_URL_ID        = (
    "https://www.linkedin.com/search/results/people/"
    "?keywords={keywords}&origin=FACETED_SEARCH&currentCompany=%5B%22{cid}%22%5D"
)

# CSS selectors for name elements — ordered most→least specific/current.
# LinkedIn now uses fully hashed/obfuscated class names, so we rely on
# stable semantic attributes (role, aria-*) and structural patterns.
NAME_SELECTORS = [
    # 2025+: search results use role="listitem" wrappers; the name is the
    # shortest /in/ link inside each item (the outer card link has more text)
    "[role='listitem'] a[href*='/in/']",
    # Older class-based selectors (kept as fallback)
    "span.entity-result__title-text a span[aria-hidden='true']",
    "span.entity-result__title-text span[aria-hidden='true']",
    ".entity-result__title-line span[aria-hidden='true']",
    ".artdeco-entity-lockup__title span[aria-hidden='true']",
    ".artdeco-entity-lockup__title span",
    ".org-people-profile-card__profile-title",
    ".org-people-profiles-module__profile-title",
    "a.app-aware-link span[aria-hidden='true']",
    ".search-results__result-item .actor-name",
]

# ── Email format generators ───────────────────────────────────────────────────

EMAIL_FORMATS: dict[str, callable] = {
    "firstname.lastname":    lambda f, l: f"{f}.{l}",
    "firstname_lastname":    lambda f, l: f"{f}_{l}",
    "firstname-lastname":    lambda f, l: f"{f}-{l}",
    "firstnamelastname":     lambda f, l: f"{f}{l}",
    "firstname":             lambda f, l: f"{f}",
    "lastname":              lambda f, l: f"{l}",
    "f.lastname":            lambda f, l: f"{f[0]}.{l}",
    "flastname":             lambda f, l: f"{f[0]}{l}",
    "f_lastname":            lambda f, l: f"{f[0]}_{l}",
    "f-lastname":            lambda f, l: f"{f[0]}-{l}",
    "firstname.l":           lambda f, l: f"{f}.{l[0]}",
    "firstname_l":           lambda f, l: f"{f}_{l[0]}",
    "firstname-l":           lambda f, l: f"{f}-{l[0]}",
    "firstnamel":            lambda f, l: f"{f}{l[0]}",
    "lastname.firstname":    lambda f, l: f"{l}.{f}",
    "lastname_firstname":    lambda f, l: f"{l}_{f}",
    "lastname-firstname":    lambda f, l: f"{l}-{f}",
    "lastnamefirstname":     lambda f, l: f"{l}{f}",
    "l.firstname":           lambda f, l: f"{l[0]}.{f}",
    "l_firstname":           lambda f, l: f"{l[0]}_{f}",
    "l-firstname":           lambda f, l: f"{l[0]}-{f}",
    "lfirstname":            lambda f, l: f"{l[0]}{f}",
    "lastname.f":            lambda f, l: f"{l}.{f[0]}",
    "lastname_f":            lambda f, l: f"{l}_{f[0]}",
    "lastname-f":            lambda f, l: f"{l}-{f[0]}",
    "lastnamef":             lambda f, l: f"{l}{f[0]}",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """Lowercase, strip accents, remove non-alpha chars."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z]", "", ascii_str.lower())


def split_name(full_name: str) -> tuple[str, str]:
    """
    Return (first, last) from a full name string.
    Handles 'First Last', 'First Middle Last', 'First M. Last', etc.
    Middle names / initials are dropped.
    """
    parts = full_name.strip().split()
    if not parts:
        return ("", "")
    if len(parts) == 1:
        return (normalize(parts[0]), "")
    first = normalize(parts[0])
    last  = normalize(parts[-1])
    return (first, last)


def generate_emails(first: str, last: str, domain: str, formats: list[str]) -> list[str]:
    """Generate all requested email variants for a name."""
    if not first or not last:
        return []
    emails = []
    for fmt in formats:
        gen = EMAIL_FORMATS.get(fmt)
        if gen:
            try:
                local = gen(first, last)
                if local:
                    emails.append(f"{local}@{domain}")
            except IndexError:
                pass
    return emails


def human_delay(lo: float = 1.5, hi: float = 4.0) -> None:
    time.sleep(random.uniform(lo, hi))


# ── Browser detection & selection ─────────────────────────────────────────────

class _Browser(NamedTuple):
    name:   str
    path:   str
    driver: str = "chrome"   # "chrome" | "edge"


def _find_browsers() -> list[_Browser]:
    """Return every Chromium-based browser installed on this system."""
    system = platform.system()
    found: list[_Browser] = []

    def _add(name: str, candidates, driver: str = "chrome") -> None:
        for p in candidates:
            if p and os.path.isfile(str(p)) and os.access(str(p), os.X_OK):
                found.append(_Browser(name=name, path=str(p), driver=driver))
                return

    if system == "Darwin":
        _add("Google Chrome", ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"])
        _add("Brave Browser", ["/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"])
        _add("Microsoft Edge", ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"], "edge")
        _add("Chromium",       ["/Applications/Chromium.app/Contents/MacOS/Chromium"])

    elif system == "Linux":
        _add("Google Chrome", filter(None, [
            shutil.which("google-chrome-stable"), shutil.which("google-chrome"),
            "/usr/bin/google-chrome-stable", "/usr/bin/google-chrome",
        ]))
        _add("Brave Browser", filter(None, [
            shutil.which("brave-browser"), shutil.which("brave"), "/usr/bin/brave-browser",
        ]))
        _add("Chromium", filter(None, [
            shutil.which("chromium-browser"), shutil.which("chromium"),
            "/usr/bin/chromium-browser", "/usr/bin/chromium", "/snap/bin/chromium",
        ]))
        _add("Microsoft Edge", filter(None, [
            shutil.which("microsoft-edge"), shutil.which("microsoft-edge-stable"),
            "/usr/bin/microsoft-edge",
        ]), "edge")

    elif system == "Windows":
        lad  = os.environ.get("LOCALAPPDATA", "")
        pf   = os.environ.get("PROGRAMFILES",       r"C:\Program Files")
        pf86 = os.environ.get("PROGRAMFILES(X86)",  r"C:\Program Files (x86)")
        _add("Google Chrome", [
            rf"{pf}\Google\Chrome\Application\chrome.exe",
            rf"{pf86}\Google\Chrome\Application\chrome.exe",
            rf"{lad}\Google\Chrome\Application\chrome.exe",
        ])
        _add("Brave Browser", [
            rf"{pf}\BraveSoftware\Brave-Browser\Application\brave.exe",
            rf"{pf86}\BraveSoftware\Brave-Browser\Application\brave.exe",
            rf"{lad}\BraveSoftware\Brave-Browser\Application\brave.exe",
        ])
        _add("Microsoft Edge", [
            rf"{pf}\Microsoft\Edge\Application\msedge.exe",
            rf"{pf86}\Microsoft\Edge\Application\msedge.exe",
        ], "edge")
        _add("Chromium", [rf"{lad}\Chromium\Application\chrome.exe"])

    return found


def _install_options() -> list[tuple[str, str, bool]]:
    """
    Return (display_name, instruction, can_auto_run) tuples for this OS.
    can_auto_run=True means the command needs no sudo and is safe to subprocess.
    """
    system = platform.system()

    if system == "Darwin":
        if shutil.which("brew"):
            return [
                ("Google Chrome",  "brew install --cask google-chrome",  True),
                ("Brave Browser",  "brew install --cask brave-browser",  True),
                ("Chromium",       "brew install --cask chromium",        True),
                ("Microsoft Edge", "brew install --cask microsoft-edge",  True),
            ]
        return [
            ("Google Chrome",  "https://www.google.com/chrome",   False),
            ("Brave Browser",  "https://brave.com/download",      False),
            ("Chromium",       "https://www.chromium.org/getting-involved/download-chromium", False),
        ]

    if system == "Linux":
        for mgr, prefix, pkg in [
            ("apt-get", "sudo apt-get install -y", "chromium-browser"),
            ("dnf",     "sudo dnf install -y",     "chromium"),
            ("pacman",  "sudo pacman -S",           "chromium"),
            ("snap",    "sudo snap install",        "chromium"),
        ]:
            if shutil.which(mgr):
                return [
                    ("Chromium",       f"{prefix} {pkg}",                  False),
                    ("Google Chrome",  "https://www.google.com/chrome",    False),
                    ("Brave Browser",  "https://brave.com/linux/",         False),
                ]
        return [
            ("Google Chrome", "https://www.google.com/chrome",   False),
            ("Chromium",      "https://www.chromium.org/getting-involved/download-chromium", False),
        ]

    if system == "Windows":
        if shutil.which("winget"):
            return [
                ("Google Chrome",  "winget install -e --id Google.Chrome", True),
                ("Brave Browser",  "winget install -e --id Brave.Brave",   True),
                ("Microsoft Edge", "Already included with Windows — check Start menu", False),
            ]
        if shutil.which("choco"):
            return [
                ("Google Chrome", "choco install -y googlechrome", True),
                ("Brave Browser", "choco install -y brave",        True),
            ]
        return [
            ("Google Chrome",  "https://www.google.com/chrome",  False),
            ("Microsoft Edge", "Already included with Windows — check Start menu", False),
        ]

    return [("Google Chrome", "https://www.google.com/chrome", False)]


def _prompt_install_and_exit() -> None:
    """Show platform-appropriate install instructions, optionally auto-run one, then exit."""
    system   = platform.system()
    os_label = {"Darwin": "macOS", "Linux": "Linux", "Windows": "Windows"}.get(system, system)

    print(f"\n[ERROR] No supported browser found on {os_label}.")
    print("[INFO] LinkedIntel requires a Chromium-based browser (Chrome, Brave, Chromium, or Edge).\n")

    options = _install_options()
    auto_nums: list[str] = []

    print("[INFO] Install options:")
    for i, (name, cmd, can_run) in enumerate(options, 1):
        note = "" if can_run else "  ← copy & run in your terminal"
        print(f"    [{i}] {name:<20} {cmd}{note}")
        if can_run:
            auto_nums.append(str(i))

    if not auto_nums:
        print("\n[INFO] Install one of the browsers above, then re-run LinkedIntel.")
        sys.exit(1)

    try:
        raw = input(
            f"\n[?] Press [{'/'.join(auto_nums)}] to auto-install, or Enter to quit: "
        ).strip()
    except EOFError:
        raw = ""

    if raw in auto_nums:
        _, cmd, _ = options[int(raw) - 1]
        print(f"[INFO] Running: {cmd}")
        result = subprocess.run(shlex.split(cmd), check=False)
        if result.returncode == 0:
            print("[w00t] Installed. Re-run LinkedIntel to continue.")
        else:
            print(f"[ERROR] Install failed (exit {result.returncode}). Install manually and re-run.")
    else:
        print("[INFO] Exiting — install a browser and re-run LinkedIntel.")

    sys.exit(1)


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    except Exception:
        return {}


def _save_config(update: dict) -> None:
    cfg = _load_config()
    cfg.update(update)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def select_browser(force: bool = False) -> _Browser:
    """
    Locate and return the browser to use, prompting interactively only when:
      - multiple browsers are found and no saved preference exists, or
      - force=True  (user passed --select-browser).
    Silently picks the first browser in non-TTY environments.
    Exits with install instructions if no browser is found at all.
    """
    browsers = _find_browsers()

    if not browsers:
        _prompt_install_and_exit()

    if len(browsers) == 1:
        print(f"[INFO] Browser: {browsers[0].name}")
        return browsers[0]

    if not force:
        saved = _load_config().get("browser")
        if saved:
            for b in browsers:
                if b.name == saved:
                    print(f"[INFO] Browser: {b.name}  (use --select-browser to change)")
                    return b

    # Non-interactive environments — don't block on input()
    if not sys.stdin.isatty():
        print(f"[INFO] Browser: {browsers[0].name}  (non-interactive, using first available)")
        return browsers[0]

    print("\n[INFO] Select a web browser:")
    for i, b in enumerate(browsers, 1):
        print(f"    [{i}] {b.name:<22} {b.path}")

    while True:
        try:
            raw = input(f"\n    Enter [1-{len(browsers)}] (default 1): ").strip()
            idx = int(raw) if raw else 1
            if 1 <= idx <= len(browsers):
                break
            print(f"    Please enter a number from 1 to {len(browsers)}.")
        except (ValueError, EOFError):
            idx = 1
            break

    selected = browsers[idx - 1]
    _save_config({"browser": selected.name})
    print(f"\n[w00t] {selected.name} selected and saved as default. (use --select-browser to change)")
    return selected


# ── Browser factories ─────────────────────────────────────────────────────────

def _build_opts(browser: _Browser, headless: bool, profile_dir: Optional[str]):
    """Construct ChromeOptions / EdgeOptions for the given browser."""
    if browser.driver == "edge":
        from selenium.webdriver.edge.options import Options as EdgeOptions
        opts = EdgeOptions()
    else:
        opts = ChromeOptions()

    opts.binary_location = browser.path
    if headless:
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1920,1080")
    if profile_dir:
        opts.add_argument(f"--user-data-dir={profile_dir}")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    return opts


def make_driver(
    browser: _Browser,
    headless: bool = False,
    profile_dir: Optional[str] = None,
) -> webdriver.Chrome:
    opts = _build_opts(browser, headless, profile_dir)
    # Selenium Manager (Selenium 4.6+) reads the binary to auto-download the
    # matching driver version — works for Chrome, Brave, Edge, and Chromium.
    driver = webdriver.Edge(options=opts) if browser.driver == "edge" else webdriver.Chrome(options=opts)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )
    return driver


# ── Cookie persistence ────────────────────────────────────────────────────────

def save_cookies(driver: webdriver.Chrome, path: Path) -> None:
    cookies = driver.get_cookies()
    with open(path, "w") as fh:
        json.dump(cookies, fh, indent=2)
    print(f"[w00t] Cookies saved to {path}")


def load_cookies(driver: webdriver.Chrome, path: Path) -> bool:
    if not path.exists():
        return False
    driver.get(LINKEDIN_BASE)
    time.sleep(2)
    with open(path) as fh:
        cookies = json.load(fh)
    for c in cookies:
        try:
            driver.add_cookie(c)
        except Exception:
            pass
    print(f"[w00t] Cookies loaded from {path}")
    return True


# ── Phase 1: headful login ────────────────────────────────────────────────────

def headful_login(browser: _Browser, reuse_cookies: bool = True) -> list[dict]:
    """
    Open a visible browser window so the user can log in and beat any
    anti-bot challenges. Polls the URL every 3 s until LinkedIn feed is
    detected — no keypress required.
    """
    if reuse_cookies and COOKIES_FILE.exists():
        print(f"[INFO] Found existing cookie file ({COOKIES_FILE}) — reusing saved session.")
        return []   # signal: use saved cookies

    print("[INFO] Opening browser for manual login...")
    print("[INFO] Log in to LinkedIn in the browser window. This script will continue automatically once you are logged in.")
    driver = make_driver(browser, headless=False)
    driver.get(LINKEDIN_LOGIN)

    try:
        # Poll until the feed URL appears (login complete)
        poll_interval = 3
        while True:
            try:
                url = driver.current_url
            except WebDriverException:
                break
            if "feed" in url or "mynetwork" in url or "/home" in url:
                break
            time.sleep(poll_interval)

        print("[w00t] Login detected!")
        save_cookies(driver, COOKIES_FILE)
        return driver.get_cookies()
    finally:
        driver.quit()


# ── Phase 2: resolve company → numeric ID ────────────────────────────────────

def resolve_company_id(driver: webdriver.Chrome, company: str) -> Optional[str]:
    """
    Resolve a company name/slug to a LinkedIn numeric company ID.

    Strategy:
    1. Hit LinkedIn's internal Voyager API (most reliable — returns only the
       target company's data as JSON).
    2. Fall back to parsing the company's own page, matching the ID only in
       the JSON object that also contains the company's universal name (avoids
       picking up the logged-in user's own company ID which appears earlier on
       the page).
    """
    if company.isdigit():
        return company

    slug = re.sub(r"[^a-z0-9-]", "", company.lower().replace(" ", "-"))
    slug = re.sub(r"-+", "-", slug).strip("-")
    slug_escaped = re.escape(slug)

    _CURN = r"urn:li:(?:fsd_company|fs_normalized_company|company)"

    print(f"[INFO] Resolving company ID for '{slug}'...")
    driver.get(f"https://www.linkedin.com/company/{slug}/")
    human_delay(3, 5)
    page_src = driver.page_source

    for pat in [
        # Best: the Follow button — first match is always the viewed company,
        # and this ID is what LinkedIn's people search uses in currentCompany.
        r'"?\*?followAction"\s*:\s*"urn:li:fsd_followingState:urn:li:fsd_company:(\d+)"',
        # Good: *organizationalPage field next to the company name
        rf'"name"\s*:\s*"[^"]*".{{0,200}}?fsd_organizationalPage:(\d+)',
        # Fallback: entityUrn within 500 chars of universalName
        rf'"entityUrn"\s*:\s*"({_CURN}:\d+)".{{0,500}}?"universalName"\s*:\s*"{slug_escaped}"',
        rf'"universalName"\s*:\s*"{slug_escaped}".{{0,500}}?"entityUrn"\s*:\s*"({_CURN}:\d+)"',
        rf'"universalName"\s*:\s*"{slug_escaped}".{{0,2000}}?{_CURN}:(\d+)',
    ]:
        m = re.search(pat, page_src, re.DOTALL)
        if m:
            raw = next(g for g in m.groups() if g is not None)
            cid = re.search(r'\d+$', raw).group()
            print(f"[w00t] Company ID resolved: {cid}")
            return cid

    print(f"[INFO] Could not resolve company ID for '{slug}'.")
    print("[INFO] Try passing --company-id <numeric_id> directly.")
    return None


# ── Phase 2: employee scraping ────────────────────────────────────────────────

_JUNK_NAMES = {"linkedin member", "linkedin", "", "see all", "show more", "connect", "follow"}
# Text fragments that indicate a line is metadata, not a name
_JUNK_FRAGMENTS = re.compile(
    r"\d+\s*(followers?|connections?|mutual|recommendation|result)",
    re.IGNORECASE,
)
# A valid name: 2-4 words, only letters/hyphens/apostrophes/spaces, no digits
_NAME_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ' -]{2,60}$")


def _is_valid_name(text: str) -> bool:
    if not text or text.lower() in _JUNK_NAMES:
        return False
    if _JUNK_FRAGMENTS.search(text):
        return False
    parts = text.strip().split()
    if not (2 <= len(parts) <= 5):
        return False
    return bool(_NAME_RE.match(text.strip()))


def _wait_for_results(driver: webdriver.Chrome, timeout: int = 12) -> None:
    """Block until at least one result card or the Next button appears."""
    indicators = [
        "[role='listitem']",
        "[data-testid='pagination-controls-next-button-visible']",
        "div.search-results-container",
        "ul.reusable-search__entity-result-list",
        "div.scaffold-layout__list",
    ]
    deadline = time.time() + timeout
    while time.time() < deadline:
        for sel in indicators:
            try:
                if driver.find_elements(By.CSS_SELECTOR, sel):
                    return
            except Exception:
                pass
        time.sleep(1)


def extract_names_from_page(driver: webdriver.Chrome, debug: bool = False) -> list[str]:
    """
    Try each known CSS selector, then fall back to harvesting names from
    /in/ profile anchor tags.  Saves a debug snapshot when nothing is found.
    """
    _wait_for_results(driver)
    names: list[str] = []

    def _name_from_element(el) -> str:
        """Extract a clean name string from a DOM element."""
        # aria-label is cleanest when present (e.g. "View John Doe's profile")
        label = (el.get_attribute("aria-label") or "").strip()
        label = re.sub(r"(?i)^view\s+", "", label)
        label = re.sub(r"(?i)'s profile.*$", "", label).strip()
        if not label:
            # Fall back to visible text; take only the first line because card
            # links include title/location/mutual-connections on later lines.
            label = (el.text or "").split("\n")[0].strip()
        return label

    # Strategy 1: known CSS selectors
    for sel in NAME_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in elements:
                text = _name_from_element(el)
                if _is_valid_name(text):
                    names.append(text)
            if names:
                break
        except Exception:
            continue

    # Strategy 2: any /in/ link not already caught above
    if not names:
        try:
            links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/in/']")
            for link in links:
                label = _name_from_element(link)
                if _is_valid_name(label):
                    names.append(label)
        except Exception:
            pass

    # Deduplicate preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            unique.append(n)

    # Save a debug snapshot whenever the page yields nothing
    if not unique or debug:
        try:
            snap_path = Path("debug_snapshot.png")
            src_path  = Path("debug_pagesource.html")
            driver.save_screenshot(str(snap_path))
            src_path.write_text(driver.page_source, encoding="utf-8")
            print(f"[INFO] 0 names found — debug snapshot saved: {snap_path}, {src_path}")
        except Exception:
            pass

    return unique


def scroll_to_bottom(driver: webdriver.Chrome, pause: float = 2.0) -> None:
    """Scroll the page incrementally to trigger lazy-loaded content."""
    last_h = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)
        new_h = driver.execute_script("return document.body.scrollHeight")
        if new_h == last_h:
            break
        last_h = new_h


def click_next_page(driver: webdriver.Chrome) -> bool:
    """Click the pagination Next button. Returns False if not found/disabled."""
    selectors = [
        # Stable data-testid (2025+ LinkedIn DOM)
        "[data-testid='pagination-controls-next-button-visible']",
        # Older class-based selectors kept as fallback
        "button[aria-label='Next']",
        "button.artdeco-pagination__button--next",
        "li.artdeco-pagination__indicator--number.active + li button",
    ]
    for sel in selectors:
        try:
            btn = WebDriverWait(driver, 6).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(0.5)
            btn.click()
            return True
        except (TimeoutException, NoSuchElementException, StaleElementReferenceException):
            continue
    return False


def scrape_employees_search(
    driver: webdriver.Chrome,
    company_id: str,
    company_keywords: str,
    max_pages: int = 0,
) -> list[str]:
    """
    Use LinkedIn people search filtered by company ID to enumerate employees.
    Handles pagination.  Returns list of full names.
    """
    import urllib.parse
    all_names: list[str] = []
    seen: set[str] = set()
    page = 1

    base_url = SEARCH_URL_ID.format(
        keywords=urllib.parse.quote(company_keywords),
        cid=company_id,
    )

    while True:
        url = base_url if page == 1 else f"{base_url}&page={page}"
        print(f"[INFO] Fetching search page {page}... ({url})")
        driver.get(url)
        human_delay(4, 7)
        scroll_to_bottom(driver, pause=2.0)

        names = extract_names_from_page(driver)
        new_this_page = 0
        for name in names:
            if name not in seen:
                seen.add(name)
                all_names.append(name)
                new_this_page += 1

        print(f"[w00t] Found {len(names)} employees on page {page} ({new_this_page} new employees). Total: {len(all_names)} employees...")

        if new_this_page == 0:
            print("[INFO] No new names on this page — stopping pagination.")
            break

        if max_pages and page >= max_pages:
            print(f"[INFO] Reached --max-pages {max_pages} limit.")
            break

        # Try to go to next page
        if not click_next_page(driver):
            print("[INFO] No 'Next' button found — end of results.")
            break

        human_delay(2, 5)
        page += 1

    return all_names


# ── Output ────────────────────────────────────────────────────────────────────

def write_output(
    names: list[str],
    company: str,
    domain: str,
    formats: list[str],
    output_file: Optional[str],
) -> None:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["Company", "First Name", "Last Name", "Email Address"])

    seen_emails: set[str] = set()
    row_count = 0

    for name in names:
        first, last = split_name(name)
        if not first:
            continue
        emails = generate_emails(first, last, domain, formats)
        for email in emails:
            if email not in seen_emails:
                seen_emails.add(email)
                # Recover display-cased first/last from the original name string
                parts = name.strip().split()
                display_first = parts[0] if parts else first
                display_last  = parts[-1] if len(parts) > 1 else last
                writer.writerow([company, display_first, display_last, email])
                row_count += 1

    csv_text = buf.getvalue()

    if output_file:
        Path(output_file).write_text(csv_text)
        print(f"\n[w00t] Results written to: {output_file}")
    else:
        print(csv_text, end="")

    print(f"[w00t] {len(names)} names → {row_count} rows ({len(seen_emails)} unique emails).")


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="LinkedIntel — LinkedIn employee enumeration & email generation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Email format keys (use one with --format):
  firstname.lastname   → john.doe@company.com
  firstname_lastname   → john_doe@company.com
  firstname-lastname   → john-doe@company.com
  firstnamelastname    → johndoe@company.com
  firstname            → john@company.com
  lastname             → doe@company.com
  f.lastname           → j.doe@company.com
  f_lastname           → j_doe@company.com
  f-lastname           → j-doe@company.com
  flastname            → jdoe@company.com
  lastname.f           → doe.j@company.com
  lastname_f           → doe_j@company.com
  lastname-f           → doe-j@company.com
  lastnamef            → doej@company.com
  firstname.l          → john.d@company.com
  firstname_l          → john_d@company.com
  firstname-l          → john-d@company.com
  firstnamel           → johnd@company.com
  l.firstname          → d.john@company.com
  l_firstname          → d_john@company.com
  l-firstname          → d-john@company.com
  lfirstname           → djohn@company.com
  lastname.firstname   → doe.john@company.com
  lastname_firstname   → doe_john@company.com
  lastname-firstname   → doe-john@company.com
  lastnamefirstname    → doejohn@company.com

Examples:
  python LinkedIntel.py --company "Acme Corp" --domain acme.com --format firstname.lastname
  python LinkedIntel.py --company-id 1234567 --domain acme.com --format f.lastname -o out.csv
  python LinkedIntel.py --company acmecorp --domain acme.com --format flastname
""",
    )

    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--company", "-c",
        metavar="NAME_OR_SLUG",
        help="Company name or LinkedIn URL slug (e.g. 'Acme Corp' or 'acme-corp')",
    )
    target.add_argument(
        "--company-id", "-C",
        metavar="ID",
        help="LinkedIn numeric company ID (bypasses slug resolution)",
    )

    p.add_argument(
        "--domain", "-d",
        required=True,
        metavar="DOMAIN",
        help="Email domain to append (e.g. acme.com)",
    )

    p.add_argument(
        "--format", "-f",
        required=True,
        metavar="FMT",
        choices=list(EMAIL_FORMATS.keys()),
        help="Email format key (see below)",
    )

    p.add_argument(
        "--output", "-o",
        metavar="FILE.csv",
        help="Write results to this CSV file (default: stdout)",
    )
    p.add_argument(
        "--max-pages",
        type=int,
        default=0,
        metavar="N",
        help="Max search pages to scrape (0 = unlimited)",
    )
    p.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        help="Run enumeration phase in a visible Chrome window",
    )
    p.set_defaults(headless=True)
    p.add_argument(
        "--select-browser",
        action="store_true",
        help="Interactively choose which browser to use (clears saved preference)",
    )

    return p.parse_args()


def main() -> None:
    args = parse_args()

    cookie_path = COOKIES_FILE
    formats     = [args.format]

    # ── Select browser ────────────────────────────────────────────────────────
    browser = select_browser(force=args.select_browser)

    # ── Phase 1: headful login ────────────────────────────────────────────────
    headful_login(browser, reuse_cookies=True)

    # ── Phase 2: headless enumeration ─────────────────────────────────────────
    print(f"[INFO] Starting {'headless' if args.headless else 'headful'} enumeration phase...")
    driver = make_driver(browser, headless=args.headless)

    try:
        # Load cookies into the new driver
        if not load_cookies(driver, cookie_path):
            print(f"[ERROR] Cookie file not found: {cookie_path}. Re-run to trigger a fresh login.")
            sys.exit(1)

        # Verify session is alive
        print("[INFO] Verifying LinkedIn session...")
        driver.get(LINKEDIN_FEED)
        time.sleep(4)

        def _session_invalid(drv: webdriver.Chrome) -> bool:
            url   = drv.current_url.lower()
            title = drv.title.lower().strip()
            # Bare-domain title means Chrome returned its own error page
            # (connection refused / blocked) rather than LinkedIn's feed.
            bare_domain = title in ("www.linkedin.com", "linkedin.com", "linkedin", "")
            return (
                "login"      in url
                or "authwall"   in url
                or "checkpoint" in url
                or "log in"     in title
                or "sign in"    in title
                or "join"       in title
                or bare_domain
            )

        if _session_invalid(driver):
            print("[ERROR] Cookies are invalid or session has expired.")
            driver.quit()
            driver = None
            if cookie_path.exists():
                cookie_path.unlink()
                print(f"[INFO] Removed stale cookie file: {cookie_path}")
            print("[INFO] Launching fresh login …")
            headful_login(browser, reuse_cookies=False)
            driver = make_driver(browser, headless=args.headless)
            if not load_cookies(driver, cookie_path):
                print("[ERROR] Login failed — no cookie file produced. Aborting.")
                sys.exit(1)
            driver.get(LINKEDIN_FEED)
            time.sleep(4)
            if _session_invalid(driver):
                print("[ERROR] Still unable to authenticate after fresh login. Aborting.")
                sys.exit(1)
        print("[w00t] Session active!")

        # ── Resolve company ID / slug ─────────────────────────────────────────
        company_label = args.company if args.company else f"ID:{args.company_id}"
        company_id: Optional[str] = None

        if args.company_id:
            company_id = args.company_id.strip()
        else:
            raw = args.company.strip()
            company_id = resolve_company_id(driver, raw)

        # ── Scrape ────────────────────────────────────────────────────────────
        names: list[str] = []

        if company_id:
            names = scrape_employees_search(driver, company_id, company_label, max_pages=args.max_pages)
        else:
            print("[ERROR] Could not resolve company ID. Aborting.")
            sys.exit(1)

    finally:
        if driver is not None:
            driver.quit()

    # ── Phase 3: generate output ───────────────────────────────────────────────
    if not names:
        print("[ERROR] No employee names discovered.")
        sys.exit(0)

    print(f"\n[w00t] Total unique names discovered: {len(names)}")
    write_output(names, company_label, args.domain, formats, args.output)


if __name__ == "__main__":
    main()
