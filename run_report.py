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
        if result.get('feedback_source_check'):
            check = result['feedback_source_check']
            lines += [f"Feedback source checked: {check['players']} owned players, "
                      f"competition round {check['round']} / Serie A matchday {check['real_matchweek']} "
                      f"({check['season']}); {check['official_scores_checked']} submitted-player scores matched the league result.", '']
        for note in result.get('feedback_notes', []):
            lines += [_escape(note), '']
        if result.get('feedback_pending') is not None:
            lines += [f"Whole-roster feedback queued for round {result['feedback_pending']}; awaiting calculated results.", '']
        review = result.get('feedback_review')
        if review:
            display = lambda key: review[key] if review[key] is not None else 'unavailable'
            lines += [f"#### Feedback — round {review['round']} (Serie A {review['real_matchweek']})", '',
                      f"Original XI: {review['starter_points']} individual points. "
                      f"Best XI with the original formation: {display('same_formation_best_points')}. "
                      f"Best legal XI in hindsight: {display('best_points')} ({display('best_formation')}). "
                      f"Gap: {display('hindsight_gap')} points.", '',
                      f"Bench goals: {review['bench_goals']} ({review['unused_bench_goals']} from unused reserves). "
                      f"Goals outside the submitted squad: {review['excluded_goals']}.", '',
                      _escape(review['basis']), '',
                      '| Player | Selection | Goals | Individual points | Used as substitute |',
                      '|---|---|---|---|---|']
            for player in review['alternatives']:
                lines.append(f"| {_escape(player['name'])} | {player['group']} | {player['goals']} | "
                             f"{player['score']} | {'yes' if player['used_substitute'] else 'no'} |")
            lines.append('')
        if result.get('learning_adjustments'):
            lines += ['#### Learning applied to this selection', '',
                      '| Player | Base rank score | Adjusted score | Change |', '|---|---|---|---|']
            for adjustment in result['learning_adjustments']:
                lines.append(f"| {_escape(adjustment['name'])} | {adjustment['base']} | "
                             f"{adjustment['adjusted']} | {adjustment['change']:+g} |")
            lines.append('')
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
