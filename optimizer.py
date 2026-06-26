"""
optimizer.py — picks the best lineup from your scraped squad.

Algorithm
─────────
1. Compute a score for every player:
       score = rating × W_RATING + fvm × W_FVM + home_bonus × W_HOME
2. Apply overrides from config (MUST_START / MUST_BENCH).
3. For each role, sort by score descending.
4. Pick the required number of starters per role (from config.FORMATION).
5. Remaining players go to bench (sorted by role priority + score).
6. Returns a dict:  { "starters": [...], "bench": [...], "formation": "4-3-3" }
"""

import json
import logging
from typing import Any

import config

logger = logging.getLogger(__name__)

# Role priority order for bench selection (goalkeeper last)
BENCH_PRIORITY = ["A", "C", "D", "P"]


def _score(player: dict) -> float:
    """Compute the composite score for a single player."""
    home_bonus = 1.0 if player.get("home") else 0.0
    return (
        player.get("rating", 0.0) * config.W_RATING
        + player.get("fvm",    0.0) * config.W_FVM
        + home_bonus                * config.W_HOME
    )


def _is_available(player: dict) -> bool:
    """Return True if a player can be fielded (not injured, not forced to bench)."""
    if player["status"] == "injured":
        return False
    if player["name"] in config.MUST_BENCH:
        return False
    return True


def optimise(players: list[dict]) -> dict[str, Any]:
    """
    Given the full squad, return the optimal lineup.

    Args:
        players: list of player dicts (as returned by scraper.scrape())

    Returns:
        {
            "starters":  list[dict],   # 11 players
            "bench":     list[dict],   # remaining available players
            "formation": str,          # e.g. "4-3-3"
            "scores":    dict[str, float],  # name → score (for debugging)
        }
    """
    # Attach scores
    for p in players:
        p["_score"] = _score(p)

    # Separate must-starts
    must_start_names = set(config.MUST_START)
    must_starters = [p for p in players if p["name"] in must_start_names]
    pool = [p for p in players if p["name"] not in must_start_names]

    # Validate must-starts fit formation
    must_by_role: dict[str, list] = {r: [] for r in ("P", "D", "C", "A")}
    for p in must_starters:
        role = p["role"]
        if role not in must_by_role:
            logger.warning(f"Must-start player {p['name']} has unknown role '{role}' — skipping.")
            continue
        if len(must_by_role[role]) >= config.FORMATION.get(role, 0):
            logger.warning(
                f"Too many must-starts for role {role} — {p['name']} will bench."
            )
            pool.append(p)
        else:
            must_by_role[role].append(p)

    # Group available pool players by role, sorted by score
    by_role: dict[str, list] = {r: [] for r in ("P", "D", "C", "A")}
    for p in pool:
        role = p.get("role", "?")
        if role in by_role and _is_available(p):
            by_role[role].append(p)

    for role in by_role:
        by_role[role].sort(key=lambda x: x["_score"], reverse=True)

    # Pick starters per role
    starters: list[dict] = list(must_starters)
    for role, quota in config.FORMATION.items():
        already = len(must_by_role.get(role, []))
        need    = quota - already
        chosen  = by_role[role][:need]

        if len(chosen) < need:
            logger.warning(
                f"Not enough available {role}s: need {need}, found {len(chosen)}. "
                "Check your squad for injuries / must-bench players."
            )
        starters.extend(chosen)
        by_role[role] = by_role[role][need:]   # remaining → bench candidates

    # Bench: everyone not starting, sorted by role priority then score
    bench_pool = [
        p for p in players
        if p not in starters
    ]
    bench_pool.sort(key=lambda x: (BENCH_PRIORITY.index(x["role"]) if x["role"] in BENCH_PRIORITY else 99, -x["_score"]))

    # Build formation string  e.g. "4-3-3"
    form_str = "-".join(
        str(config.FORMATION[r])
        for r in ("D", "C", "A")
    )

    scores = {p["name"]: round(p["_score"], 3) for p in players}

    result = {
        "starters":  starters,
        "bench":     bench_pool,
        "formation": form_str,
        "scores":    scores,
    }

    _log_lineup(result)
    return result


def _log_lineup(result: dict) -> None:
    logger.info(f"=== Optimal lineup ({result['formation']}) ===")
    for p in result["starters"]:
        logger.info(f"  STARTER  [{p['role']}] {p['name']:25s}  score={p['_score']:.2f}")
    logger.info("  --- bench ---")
    for p in result["bench"][:7]:
        logger.info(f"  BENCH    [{p['role']}] {p['name']:25s}  score={p['_score']:.2f}")


def optimise_and_save(players: list[dict], output_path: str = config.LINEUP_FILE) -> dict:
    result = optimise(players)
    with open(output_path, "w", encoding="utf-8") as f:
        # Don't serialise the internal _score key
        clean = {
            "starters":  [{k: v for k, v in p.items() if k != "_score"} for p in result["starters"]],
            "bench":     [{k: v for k, v in p.items() if k != "_score"} for p in result["bench"]],
            "formation": result["formation"],
            "scores":    result["scores"],
        }
        json.dump(clean, f, ensure_ascii=False, indent=2)
    logger.info(f"Lineup saved → {output_path}")
    return result


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Quick test with dummy data
    dummy_squad = [
        {"name": "Maignan",    "role": "P", "team": "Milan",    "rating": 6.8, "fvm": 12, "status": "available", "home": True},
        {"name": "Theo",       "role": "D", "team": "Milan",    "rating": 6.5, "fvm": 10, "status": "available", "home": True},
        {"name": "Bastoni",    "role": "D", "team": "Inter",    "rating": 6.7, "fvm": 11, "status": "available", "home": False},
        {"name": "Di Lorenzo", "role": "D", "team": "Napoli",   "rating": 6.6, "fvm": 9,  "status": "available", "home": True},
        {"name": "Mancini",    "role": "D", "team": "Roma",     "rating": 6.3, "fvm": 7,  "status": "available", "home": False},
        {"name": "Barella",    "role": "C", "team": "Inter",    "rating": 7.0, "fvm": 14, "status": "available", "home": False},
        {"name": "Pellegrini", "role": "C", "team": "Roma",     "rating": 6.4, "fvm": 9,  "status": "available", "home": True},
        {"name": "Brozovic",   "role": "C", "team": "Al-Nassr", "rating": 6.1, "fvm": 6,  "status": "doubtful",  "home": False},
        {"name": "Leao",       "role": "A", "team": "Milan",    "rating": 7.2, "fvm": 18, "status": "available", "home": True},
        {"name": "Osimhen",    "role": "A", "team": "Galatasaray","rating": 6.9, "fvm": 15,"status": "available", "home": False},
        {"name": "Immobile",   "role": "A", "team": "Lazio",    "rating": 6.0, "fvm": 8,  "status": "injured",   "home": True},
        {"name": "Raspadori",  "role": "A", "team": "Napoli",   "rating": 6.5, "fvm": 10, "status": "available", "home": True},
    ]

    result = optimise(dummy_squad)
    print("\nStarters:")
    for p in result["starters"]:
        print(f"  [{p['role']}] {p['name']}")
    print("\nBench:")
    for p in result["bench"]:
        print(f"  [{p['role']}] {p['name']}")
