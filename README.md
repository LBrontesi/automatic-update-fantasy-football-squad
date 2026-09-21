# Automatic Squad Update (Fantacalcio)

Selenium bot for Classic leagues on [Fantacalcio](https://leghe.fantacalcio.it). It ranks each league's own players, chooses an allowed formation, fills starters and reserves, and verifies the saved lineup after reloading. GitHub Actions attempts an update every three hours; scheduled runs can be delayed, so submission before a deadline is not guaranteed.

## How it works

Per league, every run:

1. Log in and open the requested competition. Skip a locked/live matchday.
2. Read names, IDs, roles, labelled fantasy averages (FM), average ratings (MV), and starting percentages from that competition's roster drawer. Membership and statistics come from the same cards used to select players, avoiding reuse of another league's roster.
3. Rank players by `0.4 × rating × starting probability`. Prefer FM, then MV. Missing ratings use an explicit neutral rating of 6; unknown starting likelihood uses 50%. A roster with no readable rating data is rejected. Players with a displayed injury/suspension label or zero starting chance cannot start.
4. With `FORMATION=auto` (default), compare the formations offered by the league and choose the highest total starting score that can also fill the reserves. Set a fixed formation to constrain the choice. Unsupported Mantra cards are rejected.
5. Select reserves by score while respecting the number and role restrictions of the rendered bench slots. Occupied slots hide these restrictions: the bot inspects an unsaved empty draft, then reloads and verifies the original saved lineup before proceeding. Assign captain and vice only when the league exposes those controls.
6. Validate ownership before clearing the draft. Populate and check the XI, bench order and formation before saving. Players are checked by provider ID from their rendered portraits, not display names that may change between roster and pitch. Failed edits discard the unsaved draft. Only report **saved** after the site acknowledges the save and the reloaded lineup matches every field.

These scores are a transparent ranking heuristic, not calibrated forecasts or guaranteed fantasy points. Starting percentages can change and do not measure substitute minutes. FM and MV are separate averages, not two historical match votes. The legacy historical scorer supports trends when actual vote history is supplied; the current live card source does not invent that history, venue or opponent-strength data.

By default an existing lineup is replaced with the recommendation. Set `REPLACE_EXISTING_LINEUP=false` locally to preserve it without submitting changes.

Before a live run the bot checks the Serie A calendar, and checks again immediately before saving. On days with games it runs only before the first kickoff. On other days it runs normally, unless the competition is locked. An unreadable, empty or stale calendar blocks live updates. Dry runs can inspect data after kickoff but still respect the site's live lock.

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
| `--dry-run` | Login + inspect data + print the recommended XI; never save. Reserve-rule inspection may temporarily clear the local draft, then reload the unchanged saved lineup. |
| `--visible` | Show the browser window even when `HEADLESS=true` |
| `--debug-dir DIR` | Where to save HTML snapshots when extraction fails (default `debug/`) |

Dry runs print the recommendation and write `reports/latest.json` and `reports/latest.md`, without clearing or saving the squad. Opening roster and formation menus only changes the browser view.

Run the local browser contract tests (synthetic page, no account access):

```bash
FANTA_BROWSER_TESTS=true python -m pytest -q
```

## Option A: Local scheduling with cron (macOS/Linux)

1. `crontab -e` and add a daily job (08:00):

   ```cron
   0 8 * * * cd /path/to/automatic-update-fantasy-football-squad && /path/to/automatic-update-fantasy-football-squad/.venv/bin/python auto_clicker_fanta.py >> cron.log 2>&1
   ```

2. Monitor with `tail -f cron.log`. Always use absolute paths in cron jobs.

## Option B: Cloud cron with GitHub Actions

`.github/workflows/update-squad.yml` tries at minute 17 every three UTC hours to avoid the busy top of the hour. The schedule guard allows live attempts only before the day's first game. A concurrency group prevents overlapping workflows. Every workflow runs unit and local Chrome contract tests, even on skipped matchdays.

1. Add the credentials as repository secrets (Settings → Secrets and variables → Actions), or from the CLI:

   ```bash
   gh secret set FANTACALCIO_EMAIL
   gh secret set FANTACALCIO_PASSWORD
   gh secret set LEAGUE_URL
   ```

   `LEAGUE_URL` is the same (comma-separated) formation URL list as in `.env`. If the secret is missing, the script falls back to the two default league URLs.

2. Test the pipeline: **Actions** → **Update fantasy squad** → **Run workflow**, enable **dry_run** first.

3. Optional repository variable `FORMATION`: use `auto` or a fixed formation such as `3-4-3`. Each league's available formations and bench restrictions are read independently.

4. Open a run's **Summary** for each league's **saved**, **dry_run**, **skipped**, or **failed** result and ranking details. A green workflow does not necessarily mean a squad was changed. Download the `squad-report` artifact for the JSON/Markdown report (14 days); browser debug files expire after 3 days.

### Caveats

- **Schedule polling**: GitHub Actions uses a three-hour UTC cron to provide several chances to run. GitHub may still delay scheduled workflows, but a missed check is less likely to miss the update window.
- **Matchday guard**: the default schedule guard uses `https://www.fantacalcio.it/serie-a/calendario` and `Europe/Rome`; override `SCHEDULE_URL` or `SCHEDULE_TIMEZONE` if needed.
- **Inactivity cutoff**: GitHub disables scheduled workflows after 60 days without a push. If the bot silently stops, push any change (or run it manually) to re-enable it.
- Chrome is installed automatically on the runner by `selenium-manager` (bundled with Selenium ≥ 4.6). Headless mode is forced by the workflow.

## Current limitations

- The live path uses the current Angular lineup UI: it selects the configured formation, double-clicks the recommended players into the first valid slots, assigns captain and vice-captain, and saves the formation. If Fantacalcio changes the UI again, the run fails safely and saves an HTML snapshot under `debug/`.
- Legacy URLs in the form `.../area-gioco/inserisci-formazione?id=...` are converted automatically to the current `.../view/competition/<id>/lineup` route.
- `home` advantage is only applied when the league pages expose the venue; `opponent` weakness only when standings/results are extractable.
- The active editor is `lineup_editor.py`; `player_data.py` also retains legacy parsing helpers. The browser tests model observed DOM contracts and cannot guarantee future site compatibility. A successful locked-matchday dry run does not validate a live submission; look for **saved** with a verified reload.
