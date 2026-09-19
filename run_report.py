"""Small, credential-free run reports for GitHub Actions and local runs."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def player_report(player, score):
    return {"name": player.name, "role": player.role, "score": score,
            "rating": player.votes[0] if player.votes else None,
            "rating_source": player.rating_source,
            "starting_percent": round(100 * player.start_probability) if player.start_probability is not None else None}


def _escape(value):
    return str(value).replace('|', '\\|').replace('\n', ' ').replace('<', '&lt;').replace('>', '&gt;')


def render_summary(results, dry_run):
    lines = [f"## Squad bot — {'dry run' if dry_run else 'live run'}", "",
             "A green workflow can mean skipped. Only **saved** means the lineup was saved and verified.", ""]
    for result in results:
        lines += [f"### {_escape(result['league'])}: {result['status']}", "", _escape(result['reason']), ""]
        if 'formation' in result:
            lines += [f"Formation: {result['formation']}. Scores rank players; they are not predicted fantasy points.", "",
                      "| Position | Player | Rating source | Starting chance | Score |",
                      "|---|---|---|---|---|"]
            for group in ('starters', 'bench'):
                for player in result.get(group, []):
                    chance = f"{player['starting_percent']}%" if player['starting_percent'] is not None else 'unknown (50% prior)'
                    lines.append(f"| {'Bench ' if group == 'bench' else ''}{player['role']} | {_escape(player['name'])} | {_escape(player['rating_source'])} ({player['rating']}) | {chance} | {player['score']} |")
            lines.append('')
    return '\n'.join(lines)


def write_report(results, dry_run, directory='reports'):
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "dry_run": dry_run, "leagues": results}
    (path / 'latest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    summary = render_summary(results, dry_run)
    (path / 'latest.md').write_text(summary, encoding='utf-8')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as handle:
            handle.write(summary + '\n')
