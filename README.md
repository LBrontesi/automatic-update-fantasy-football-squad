# Automatic Squad Update (Fantacalcio)

Selenium bot for [fantacalcio.it](https://www.fantacalcio.it) that logs into your leagues, **picks the best XI with a scoring model** when the lineup is empty, and always **confirms the formation** so you never miss the lineup deadline.

Because matchday deadlines are not on a fixed weekday, the bot is designed to run **daily**: a daily run guarantees a pick + confirm within 24h of any deadline, and confirming an already-saved lineup is idempotent (never harmful).

## How it works

Per league, every run:

1. Login only when the session expired.
2. Read the roster and each player's past matchday votes from the league pages.
3. Score every player: `0.4·avg(last 5 votes) + 0.3·trend + 0.2·home + 0.1·opponent weakness` (weights configurable).
4. Pick the best XI for the configured formation (`FORMATION=3-4-3` by default), captain = top scorer.
5. **If the lineup is empty** (the site resets it after each matchday) → set the recommended XI and confirm.
6. **If the lineup is already set** → confirm only, leaving your manual picks untouched.

The scoring is plugged behind a `BasePredictionSource` interface: `historical` (past league votes) is implemented, `external` (forward-looking previsioni voti from fantacalcio.it/Gazzetta APIs) is a documented stub for future work.

## Setup

1. Clone and create a virtual environment:

   ```bash
   git clone https://github.com/LBrontesi/automatic-update-fantasy-football-squad.git
   cd automatic-update-fantasy-football-squad
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Create your config file and fill it in:

   ```bash
   cp config.example.env .env
   ```

   The script reads environment variables first and falls back to `.env`, so it behaves identically locally and on GitHub Actions. `.env` is git-ignored — never commit it.

3. Run the tests:

   ```bash
   python -m pytest
   ```

## Local usage

Dry run (reads your leagues, prints the recommended XI, changes nothing):

```bash
python auto_clicker_fanta.py --dry-run
```

Live run:

```bash
python auto_clicker_fanta.py
```

Extra flags:

| Flag | Effect |
|------|--------|
| `--dry-run` | Login + read data + print the recommended XI, no site changes |
| `--visible` | Show the browser window even when `HEADLESS=true` |
| `--debug-dir DIR` | Where to save HTML snapshots when extraction fails (default `debug/`) |

The output of a dry run includes the recommended XI with per-player scores, the captain, and any candidate API endpoints observed — useful when the site changes.

## Option A: Local scheduling with cron (macOS/Linux)

1. `crontab -e` and add a daily job (08:00):

   ```cron
   0 8 * * * cd /path/to/automatic-update-fantasy-football-squad && /path/to/automatic-update-fantasy-football-squad/.venv/bin/python auto_clicker_fanta.py >> cron.log 2>&1
   ```

2. Monitor with `tail -f cron.log`. Always use absolute paths in cron jobs.

## Option B: Cloud cron with GitHub Actions

`.github/workflows/update-squad.yml` runs the tests and then the bot every day at 06:00 UTC, plus manual runs from the **Actions** tab.

1. Add the credentials as repository secrets (Settings → Secrets and variables → Actions), or from the CLI:

   ```bash
   gh secret set FANTACALCIO_EMAIL
   gh secret set FANTACALCIO_PASSWORD
   gh secret set LEAGUE_URL
   ```

   `LEAGUE_URL` is the same (comma-separated) formation URL list as in `.env`. If the secret is missing, the script falls back to the two default league URLs.

2. Test the pipeline: **Actions** → **Update fantasy squad** → **Run workflow**.

### Caveats

- **UTC schedule**: 06:00 UTC is 08:00 in Italy in summer, 07:00 in winter. Adjust the `cron` if you want a fixed Italian time.
- **Inactivity cutoff**: GitHub disables scheduled workflows after 60 days without a push. If the bot silently stops, push any change (or run it manually) to re-enable it.
- Chrome is installed automatically on the runner by `selenium-manager` (bundled with Selenium ≥ 4.6). Headless mode is forced by the workflow.

## Current limitations

- **Setting the lineup is not mapped yet.** The bot can read players, score them and pick the XI (tested), but the add/remove-player controls of the formation UI still need their selectors mapped. Until then, a live run with an empty lineup stops with a clear error instead of confirming a half-set squad. Run `python auto_clicker_fanta.py --dry-run` locally and share the `debug/` HTML snapshots to get the UI mapping implemented.
- `home` advantage is only applied when the league pages expose the venue; `opponent` weakness only when standings/results are extractable.
- Picks can be up to 24h stale; the future `external` prediction source will reduce this.
