## Scheduling Automatic Updates with Cron (macOS)

Once the project is configured and working correctly, you can schedule it to run automatically using `cron`.

### 1. Find the Python Interpreter

First, determine the Python executable used by your virtual environment:

```bash
which python
```

or

```bash
source .venv/bin/activate
which python
```

Example output:

```bash
/Users/lorenzo/automatic-update-fantasy-football-squad/.venv/bin/python
```

### 2. Test the Script

Verify that the script runs correctly from the terminal:

```bash
cd /path/to/automatic-update-fantasy-football-squad
/path/to/.venv/bin/python update_squad.py
```

### 3. Open the Cron Table

Edit your personal crontab:

```bash
crontab -e
```

### 4. Add a Cron Job

Run the squad update every day at 08:00:

```cron
0 8 * * * cd /Users/lorenzo/automatic-update-fantasy-football-squad && /Users/lorenzo/automatic-update-fantasy-football-squad/.venv/bin/python update_squad.py >> cron.log 2>&1
```

Run every hour:

```cron
0 * * * * cd /Users/lorenzo/automatic-update-fantasy-football-squad && /Users/lorenzo/automatic-update-fantasy-football-squad/.venv/bin/python update_squad.py >> cron.log 2>&1
```

### 5. Verify the Cron Job

List all scheduled jobs:

```bash
crontab -l
```

Monitor the log file:

```bash
tail -f cron.log
```

### Cron Schedule Examples

| Schedule | Expression |
|-----------|------------|
| Every day at 08:00 | `0 8 * * *` |
| Every day at 18:30 | `30 18 * * *` |
| Every hour | `0 * * * *` |
| Every 15 minutes | `*/15 * * * *` |
| Every Monday at 09:00 | `0 9 * * 1` |

### Common Issues

- Use **absolute paths** in cron jobs.
- Activate the virtual environment explicitly or use its Python executable.
- Redirect logs (`>> cron.log 2>&1`) to diagnose failures.
- Environment variables available in your terminal may not be be available to cron. If your script depends on secrets or configuration files, ensure they are loaded explicitly.

### Disabling the Job

Remove all cron jobs:

```bash
crontab -r
```

Or edit the schedule:

```bash
crontab -e
```
