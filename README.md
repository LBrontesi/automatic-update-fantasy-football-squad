# Automatic Squad Update (Fantacalcio)

Selenium bot that logs into [fantacalcio.it](https://www.fantacalcio.it) and confirms your formation for one or more leagues, so you never forget the lineup deadline.

## Setup

1. Clone the repo and create a virtual environment:

   ```bash
   git clone https://github.com/LBrontesi/automatic-update-fantasy-football-squad.git
   cd automatic-update-fantasy-football-squad
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Create your config file:

   ```bash
   cp config.example.env .env
   ```

3. Edit `.env` and fill in:

   | Variable | Description |
   |----------|-------------|
   | `FANTA_EMAIL` | Your fantacalcio.it email |
   | `FANTA_PASSWORD` | Your fantacalcio.it password |
   | `LEAGUE_URLS` | Comma-separated formation page URLs (one per league) |
   | `HEADLESS` | `true` = no browser window (default), `false` = watch it run |

   The script reads environment variables first and falls back to `.env`, so it works the same way locally and on GitHub Actions. `.env` is git-ignored — never commit it.

4. Run it manually:

   ```bash
   python auto_clicker_fanta.py
   ```

   The bot checks whether you are logged in and only performs the login flow when needed. Each league is confirmed with retries, and every action is logged with a timestamp.

## Option A: Local scheduling with cron (macOS/Linux)

1. Open your personal crontab: `crontab -e`
2. Add a job. Every 2 days at 08:00:

   ```cron
   0 8 */2 * * cd /path/to/automatic-update-fantasy-football-squad && /path/to/automatic-update-fantasy-football-squad/.venv/bin/python auto_clicker_fanta.py >> cron.log 2>&1
   ```

3. Monitor with `tail -f cron.log`. Always use absolute paths in cron jobs.

## Option B: Cloud cron with GitHub Actions

The `.github/workflows/update-squad.yml` workflow runs the script on GitHub's servers every 2 days at 06:00 UTC and can also be triggered manually from the **Actions** tab.

1. Add the credentials as repository secrets (Settings → Secrets and variables → Actions), or from the CLI:

   ```bash
   gh secret set FANTA_EMAIL
   gh secret set FANTA_PASSWORD
   gh secret set LEAGUE_URLS
   ```

   `LEAGUE_URLS` is the same comma-separated list as in `.env`. If the secret is missing, the script falls back to the two default league URLs.

2. Test the pipeline: open the **Actions** tab → **Update fantasy squad** → **Run workflow**.

### Caveats

- **UTC schedule**: 06:00 UTC is 08:00 in Italy during summer time but 07:00 in winter. Adjust the `cron` expression if you want a fixed Italian time.
- **Inactivity cutoff**: GitHub disables scheduled workflows after 60 days with no push to the repository. If the bot silently stops, push any change (or run it manually) to re-enable it.
- Chrome is installed automatically on the runner by `selenium-manager` (bundled with Selenium ≥ 4.6) — no extra setup needed. Headless mode is required on runners and is forced by the workflow.

## Common issues

- Credentials missing → script exits with a clear message. Set them in `.env` or as secrets.
- Schedule runs on the **default branch** — merge changes to `main` for the cron schedule to take effect.
- If the site changes its layout, the bot falls back to the original XPath selectors; update the selector lists in `auto_clicker_fanta.py` if both fail.
