# Fantacalcio Auto-Squad Bot

Automatically selects and saves the optimal lineup for your fantacalcio leagues.

## Architecture

```
scheduler.py          ← orchestrates everything; CLI entry point
  └─ scraper.py       ← logs in, scrapes your squad + player ratings
  └─ optimizer.py     ← picks the best XI given your formation + weights
  └─ submitter.py     ← applies the lineup on the site via Selenium
config.py             ← all user settings (credentials, leagues, weights)
weights.json          ← quick-edit scoring weights (no code change needed)
```

## Setup

### 1. Install dependencies

```bash
pip install selenium
# Chrome + chromedriver must be installed and on your PATH:
# https://googlechromelabs.github.io/chrome-for-testing/
```

### 2. Fill in config.py

```python
EMAIL    = "your@email.com"
PASSWORD = "your_password"

LEAGUES = [
    {
        "name": "rusticatorsapienza",
        "formation_url": "https://leghe.fantacalcio.it/rusticatorsapienza/...",
    },
]

FORMATION = {"P": 1, "D": 4, "C": 3, "A": 3}  # your preferred formation
```

Get the `formation_url` by opening "Inserisci formazione" in your league and copying the URL.

---

## Testing (start here)

### Step 1 — dry-run: no browser clicks, just logging
```bash
python scheduler.py --dry-run --verbose
```

### Step 2 — scrape only: check your squad is read correctly
```bash
python scheduler.py --scrape-only --verbose
# Inspect:  rusticatorsapienza_players.json
```

### Step 3 — optimise only: check the lineup makes sense
```bash
python scheduler.py --optimise-only --verbose
# Inspect:  rusticatorsapienza_lineup.json
```

### Step 4 — full run with headless=False to watch it happen
Set `HEADLESS = False` in config.py, then:
```bash
python scheduler.py --verbose
```

### Step 5 — single league first
```bash
python scheduler.py --league rusticatorsapienza
```

---

## Scheduling

### Option A: daemon mode (runs every 2 days internally)
```bash
python scheduler.py --daemon
```

### Option B: cron (recommended — more reliable)
```bash
crontab -e
```
Add:
```
# Every 2 days at 09:00
0 9 */2 * * cd /path/to/fantacalcio_bot && python scheduler.py >> bot.log 2>&1
```

---

## Customising the scoring

Edit `weights.json` (no code change needed):

```json
{
  "W_RATING": 1.0,    // fantacalcio average season rating
  "W_FVM":    0.5,    // fantacalcio median fantasy value
  "W_HOME":   0.3,    // bonus for home fixture this matchday
  "MUST_START": ["Leao", "Barella"],
  "MUST_BENCH": ["Immobile"]
}
```

Or edit the same fields directly in `config.py`.

### Score formula

```
score = rating × W_RATING + fvm × W_FVM + (1 if home else 0) × W_HOME
```

Raise `W_FVM` to favour high-ceiling players. Raise `W_HOME` if home advantage matters a lot in your league's scoring system.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| No players scraped | Set `HEADLESS=False`, run `--scrape-only --verbose`, check selector output |
| Wrong lineup | Inspect `*_players.json` — check ratings/fvm are non-zero |
| Save button not found | Grab the CSS selector from DevTools and update `_click_save()` in submitter.py |
| Drag-and-drop fails | The fallback click approach runs automatically; check screenshots |
| Login fails | Run with `HEADLESS=False` and `--verbose` to see the login page |

Screenshots are saved as `screenshot_{league}_before.png` and `*_after.png` on every run.

---

## Extending

- **Fixture enrichment**: implement `_enrich_with_fixtures()` in `scraper.py` to set `home=True/False` per player, giving the home bonus real effect.
- **Form factor**: add a rolling `form` field (last 3 matchday ratings) and a `W_FORM` weight.
- **Notification**: add a Telegram/email alert at the end of `run_pipeline()` in `scheduler.py`.
