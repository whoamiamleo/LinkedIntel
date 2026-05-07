# LinkedIntel

![Python](https://img.shields.io/badge/Python-3.8%2B-blue?style=flat-square&logo=python&logoColor=white)
![Selenium](https://img.shields.io/badge/Selenium-4.6%2B-43B02A?style=flat-square&logo=selenium&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey?style=flat-square)
![Authorized Pentesting Only](https://img.shields.io/badge/⚠%EF%B8%8F%20Authorized%20Pentesting%20Only-critical?style=flat-square)

Automates LinkedIn employee enumeration and email address generation.

---

## Table of Contents

- [Features](#features)
- [How It Works](#how-it-works)
- [Installation](#installation)
- [Usage](#usage)
  - [Examples](#examples)
- [Support](#support)
- [Formatting](#formatting)
  - [Input](#input)
  - [Output](#output)
- [Contributing](#contributing)
- [Attribution](#attribution)
- [Legal & Ethics](#legal--ethics)
- [License](#license)

---

## Features

- **Cookie-based authentication**: Log in once via a real browser. The session is persisted locally and reused automatically on every subsequent run.
- **Automatic session recovery**: Detects expired or invalid cookies, deletes them, and relaunches the login flow without intervention.
- **Company resolution**: Resolves company names and URL slugs to LinkedIn numeric IDs for precise search filtering.
- **Full pagination**: Walks every search result page automatically, deduplicating names across pages.
- **19 email formats**: Generate one or more naming conventions simultaneously (e.g. `firstname.lastname`, `flastname`, `f.lastname`).
- **Cross-platform browser detection**: Auto-detects Chrome, Brave, Chromium, and Edge on macOS, Linux, and Windows. Offers guided install if none is found.
- **Browser preference**: Remembers your chosen browser between runs. Use `--select-browser` to change it at any time.
- **CSV output**: Clean, deduplicated results written to stdout or a file for easy pipeline integration.
- **Anti-detection**: Randomised delays, incremental scrolling, and WebDriver fingerprint masking.

## How It Works

```
┌──────────────────────────────────────────────────────────────────────┐
│  Phase 0: Browser Detection                                           │
│  Scans standard install paths on macOS / Linux / Windows.             │
│  If multiple browsers found, prompts the user to choose (saved).      │
│  If none found, displays platform-specific install instructions.      │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────┐
│  Phase 1: Authentication                                              │
│  Checks for a saved .linkedin_cookies.json file.                      │
│  If missing or invalid → opens a visible browser for manual login.    │
│  Automatically detects successful login (no keypress required).       │
│  Persists the new session cookies for all future runs.                │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────┐
│  Phase 2: Company Resolution                                          │
│  Navigates to the target company's LinkedIn page.                     │
│  Extracts the numeric company ID via page-source pattern matching.    │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────┐
│  Phase 3: Employee Scraping                                           │
│  Headless browser paginates through people search results.            │
│  Names are extracted via layered CSS selectors with fallbacks.        │
│  Human-like scrolling and randomised delays between pages.            │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────┐
│  Phase 4: Email Generation                                            │
│  Names are normalised (accent-stripped, lowercased).                  │
│  One or more of the 19 email format templates are applied.            │
│  Deduplicated results are written to stdout or a CSV file.            │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Installation

**Requires Python 3.8+ and a Chromium-based browser** (Chrome, Brave, Chromium, or Edge). No ChromeDriver setup needed. Selenium Manager (bundled with Selenium 4.6+) downloads the correct driver automatically.

```bash
git clone https://github.com/whoamiamleo/LinkedIntel.git
cd LinkedIntel
python3 -m venv .venv
source .venv/bin/activate       # macOS / Linux
# .venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

If no supported browser is detected on first run, LinkedIntel will display platform-specific install options and offer to auto-install via **Homebrew** (macOS) or **winget / Chocolatey** (Windows).

---

## Usage

On the first run (or after cookies expire), a visible browser window opens automatically. Log in to LinkedIn normally. The script detects a successful login and saves your session cookies. All subsequent runs are fully headless.

```
usage: LinkedIntel.py [-h] (--company NAME_OR_SLUG | --company-id ID)
                      --domain DOMAIN --format FMT [FMT ...] [--output FILE]
                      [--max-pages N] [--no-headless] [--select-browser]
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--company` | `-c` | | Company name or LinkedIn URL slug |
| `--company-id` | `-C` | | LinkedIn numeric company ID (bypasses name resolution) |
| `--domain` | `-d` | | Email domain to append (e.g. `acme.com`) |
| `--format` | `-f` | | One or more email format keys |
| `--output` | `-o` | stdout | Write CSV output to this file |
| `--max-pages` | | `0` (unlimited) | Maximum search result pages to scrape |
| `--no-headless` | | | Run enumeration in a visible browser window |
| `--select-browser` | | | Interactively choose browser (clears saved preference) |

### Examples

```bash
# Single format
python LinkedIntel.py --company "Acme Corp" --domain acme.com --format firstname.lastname

# Multiple formats
python LinkedIntel.py --company "Acme Corp" --domain acme.com \
  --format firstname.lastname flastname f.lastname

# Multiple formats, save to file
python LinkedIntel.py --company "Acme Corp" --domain acme.com \
  --format firstname.lastname flastname f.lastname --output results.csv

# Use a LinkedIn URL slug
python LinkedIntel.py --company acme-corp --domain acme.com --format firstname.lastname

# Bypass name resolution with a numeric company ID
python LinkedIntel.py --company-id 1234567 --domain acme.com --format f.lastname flastname

# Limit to 3 pages of results
python LinkedIntel.py --company "Acme Corp" --domain acme.com \
  --format firstname.lastname --max-pages 3

# Run enumeration in a visible browser window
python LinkedIntel.py --company "Acme Corp" --domain acme.com \
  --format firstname.lastname --no-headless

# Choose or change browser
python LinkedIntel.py --company "Acme Corp" --domain acme.com \
  --format firstname.lastname --select-browser
```

---

## Support

LinkedIntel auto-detects installed browsers at standard system paths. No manual configuration is required.

| Browser | macOS | Linux | Windows |
|---|---|---|---|
| Google Chrome | ✅ | ✅ | ✅ |
| Brave Browser | ✅ | ✅ | ✅ |
| Chromium | ✅ | ✅ | ✅ |
| Microsoft Edge | ✅ | ✅ | ✅ |

If no browser is found, LinkedIntel displays install options for your OS and offers to auto-install via **Homebrew** (macOS) or **winget / Chocolatey** (Windows). On Linux, the appropriate `apt-get`, `dnf`, `pacman`, or `snap` command is printed for manual execution.

## Formatting

### Input

All input is provided via command-line flags. No input file is required. See [Usage](#usage) for the full flag reference.

### Output

Results are written as CSV to stdout or a file (`--output`). Each row contains:

```
Company,First Name,Last Name,Email Address
Acme Corp,John,Doe,john.doe@acme.com
```

The email local part is generated from the format keys supplied via `--format`:

| Key | Example |
|---|---|
| `firstname.lastname` | `john.doe@acme.com` |
| `firstname_lastname` | `john_doe@acme.com` |
| `firstname-lastname` | `john-doe@acme.com` |
| `firstnamelastname` | `johndoe@acme.com` |
| `firstname` | `john@acme.com` |
| `lastname` | `doe@acme.com` |
| `f.lastname` | `j.doe@acme.com` |
| `flastname` | `jdoe@acme.com` |
| `f_lastname` | `j_doe@acme.com` |
| `f-lastname` | `j-doe@acme.com` |
| `firstname.l` | `john.d@acme.com` |
| `firstnamel` | `johnd@acme.com` |
| `lastname.firstname` | `doe.john@acme.com` |
| `lastname_firstname` | `doe_john@acme.com` |
| `lastname-firstname` | `doe-john@acme.com` |
| `lastnamefirstname` | `doejohn@acme.com` |
| `l.firstname` | `d.john@acme.com` |
| `lfirstname` | `djohn@acme.com` |
| `lastname.f` | `doe.j@acme.com` |

---

## Contributing

Contributions, issues, and feature requests are welcome. Feel free to check the [issues](https://github.com/whoamiamleo/LinkedIntel/issues) page or submit a pull request.

## Attribution

If you use LinkedIntel in a project or research, a mention or link back to this repository is appreciated.

- Author: Leopold von Niebelschuetz-Godlewski
- Repository: [https://github.com/whoamiamleo/LinkedIntel](https://github.com/whoamiamleo/LinkedIntel)
- License: MIT

---

## Legal & Ethics

LinkedIntel is intended solely for authorized security testing and research activities. Any unauthorized use is strictly prohibited. The author assumes no responsibility for misuse or damage resulting from improper or unlawful use.

---

## License

MIT License

Copyright (c) 2026 Leopold von Niebelschuetz-Godlewski

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
