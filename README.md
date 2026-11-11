# Automatic Squad Update (Fantacalcio)

**The squad-update workflow was disabled on 11 November 2026 after Fantacalcio requested that automatic account access stop. Leave it disabled.** The earlier bot documentation below is retained as historical implementation documentation.

## Telegram formation reminders

`serie_a_reminder.py` reads Lega Serie A's public calendar and its public fixture feed. It resolves the current Serie A season, groups games by numbered giornata, and calculates one reminder two hours before the earliest confirmed kickoff. Simultaneous opening games share one reminder. Later days and postponed catch-up games in a round that has already started cannot trigger another reminder. Unconfirmed times and incomplete or conflicting fixture data are not guessed. The reminder contains links for manually submitting both league lineups; the script never logs into or submits anything to Fantacalcio.

Fixture checks are cached in `data/telegram_reminder_state.json`. The calendar refreshes at least every six hours and on each new Italian calendar day. On the opening day, or while kickoff information is unconfirmed, it refreshes every 30 minutes. A cached deadline is also rechecked before delivery. Temporary public-calendar failures get three bounded attempts. Times use Europe/Rome, including daylight saving. A scheduler must run `tick` frequently enough to reach the deadline; delayed execution can produce a later reminder, and no reminder is sent after kickoff.

Create a dedicated bot using [Telegram's BotFather](https://t.me/BotFather). Store its token in the ignored, private `telegram.local.env` file as `TELEGRAM_BOT_TOKEN=...`. Send your new bot `/start` followed by the code in the ignored `.telegram-pairing-code` file. Run `python serie_a_reminder.py pair` to pair only the personal chat containing that exact code and send a connection test. Add `--github` to store `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as repository secrets as well. No token or chat ID is written to the public reminder state or logs.

Commands:

- `python serie_a_reminder.py check`: validate the live public schedule and display the next reminder; needs no Telegram credentials and sends nothing.
- `python serie_a_reminder.py test`: send one connection test to the paired personal chat.
- `python serie_a_reminder.py tick`: refresh if needed and send only if due, using local state.
- `python serie_a_reminder.py tick --persist-git`: persist the delivery reservation to GitHub **before** calling Telegram, then persist the result. Used by the separate reminder workflow.

Telegram does not provide an idempotency key for `sendMessage`. A request timeout or interrupted run leaves a durable `unconfirmed` or `claimed` record. Later attempts report a failure requiring inspection instead of silently returning success or risking a duplicate. Explicit rate-limit rejections can retry after Telegram's requested delay; permanent rejections stay visible as failures. Successful delivery records are retained across runs and keyed by season and giornata. A GitHub concurrency group and a local file lock prevent overlapping checks. An unrelated repository push during a wait is handled by rebasing the reminder's state commit; a conflicting reservation stops delivery. The Actions summary shows the next deadline, last calendar check, and unresolved delivery records.

`.github/workflows/telegram-reminder.yml` supports manual `check`, `test`, and `tick` runs, plus a cloud check every 15 minutes. Scheduled delivery is enabled only when the repository variable `TELEGRAM_REMINDERS_ENABLED` equals `true`; pair the bot and verify a cloud connection test before setting it. Early cloud runs wait up to 20 minutes for the exact two-hour target, then recheck the calendar before sending. GitHub scheduling can still be delayed, so exact delivery time is not guaranteed. The Mac does not need to remain on. To pause reminders, set the variable to `false` or disable **Telegram formation reminder** in Actions. This workflow is separate from the disabled squad-update workflow. The `check` mode runs only reminder tests and a public fixture check.

## Historical squad bot

Selenium bot for Classic leagues on [Fantacalcio](https://leghe.fantacalcio.it). It ranks each league's own players, chooses an allowed formation, fills starters and reserves, and verifies the saved lineup after reloading. GitHub Actions attempts an update every three hours; scheduled runs can be delayed, so submission before a deadline is not guaranteed.

## How it works

Per league, every run:

1. Log in and open the requested competition. Skip a locked/live matchday.
2. Read names, IDs, roles, labelled fantasy averages (FM), average ratings (MV), and starting percentages from that competition's roster drawer. Membership and statistics come from the same cards used to select players, avoiding reuse of another league's roster.
3. Rank players by `0.4 × rating × starting probability`. Prefer FM, then MV. Missing ratings use an explicit neutral rating of 6; unknown starting likelihood uses 50%. A roster with no readable rating data is rejected. Players with a displayed injury/suspension label or zero starting chance cannot start.
4. With `FORMATION=auto` (default), compare the formations offered by the league and choose the highest total starting score that can also fill the reserves. Set a fixed formation to constrain the choice. Unsupported Mantra cards are rejected.
5. Select reserves by score while respecting the number and role restrictions of the rendered bench slots. Occupied slots hide these restrictions: the bot inspects an unsaved empty draft, then reloads and verifies the original saved lineup before proceeding. Assign captain and vice only when the league exposes those controls.
6. Validate ownership before clearing the draft. Populate and check the XI, bench order and formation before saving. Players are checked by provider ID from their rendered portraits, not display names that may change between roster and pitch. Failed edits discard the unsaved draft. Only report **saved** after the site acknowledges the save and the reloaded lineup matches every field.

These scores are a transparent ranking heuristic, not guaranteed fantasy points. Starting percentages can change and do not measure substitute minutes. FM and MV are separate averages, not two historical match votes. The legacy historical scorer supports trends when actual vote history is supplied; the current live card source does not invent that history, venue or opponent-strength data.

After a verified save, the bot records the full owned roster: provider IDs, names, roles, ranking scores, estimated contributions, and who started, sat on the bench, or was excluded. On a later permitted run it matches the submitted squad and timestamp to the competition's calculated result, resolves the corresponding Serie A matchday, and reads each player's official Fantacalcio history using the league's rating source. It applies the league's individual bonus/malus rules and cross-checks the submitted players against the calculated league scores. Missing, unfinished, ambiguous, or inconsistent results leave feedback pending and do not block a lineup update.

The Actions report shows goals from unused reserves and excluded players, reserves who actually came on, the original XI's individual points, and the best legal XI both with the original formation and across all allowed formations. The hindsight gap excludes automatic substitutions and team/captain modifiers; it measures opportunity in hindsight, not predictable points before kickoff. The original decision and complete result are retained for the last 12 reviewed rounds.

Player-specific corrections learn from all owned players, including those omitted, after three completed matchdays. Residuals compare actual individual contributions with the contribution estimated when selecting the squad. Corrections are shrunk, capped at two fantasy-point units, and converted to the ranking-score scale. They cannot promote unavailable or zero-chance players. Repeated runs cannot train the same Serie A matchday twice, and models are separated by league/competition and season. Earlier aggregate-only state migrates without losing pending rounds. `data/learning_state.json` now contains footballer IDs/names and decision history, but no account credentials or tokens.

To validate historical result coverage without saving a lineup or training, run `python auto_clicker_fanta.py --check-feedback-source`, or enable **check_feedback_source** when manually running the GitHub workflow. This checks the latest calculated round for the entire current roster and reports how many individual scores matched the league result.

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

The update job uploads learning state as a short-lived artifact. A separate `persist-learning-state` job has `contents: write` and commits only `data/learning_state.json`; the browser and test jobs keep read-only repository access.

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

- The live path uses the current Angular lineup UI: it selects the configured formation, selects each compatible empty slot and clicks its recommended player, assigns captain and vice-captain when enabled, and saves the formation. Each placement is checked by player ID, and the saved XI, reserve order, formation and captaincy are verified after reload. If the UI changes, the run fails safely and saves an HTML snapshot under `debug/`.
- Legacy URLs in the form `.../area-gioco/inserisci-formazione?id=...` are converted automatically to the current `.../view/competition/<id>/lineup` route.
- `home` advantage is only applied when the league pages expose the venue; `opponent` weakness only when standings/results are extractable.
- The active editor is `lineup_editor.py`; `player_data.py` also retains legacy parsing helpers. The browser tests model observed DOM contracts and cannot guarantee future site compatibility. A successful locked-matchday dry run does not validate a live submission; look for **saved** with a verified reload.
