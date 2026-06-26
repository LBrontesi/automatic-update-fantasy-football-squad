"""
config.py — all user-configurable settings in one place.
Edit this file before running.
"""

# ── Credentials ──────────────────────────────────────────────────────────────
EMAIL    = "your@email.com"
PASSWORD = "your_password"

# ── Leagues ──────────────────────────────────────────────────────────────────
# Each entry: { "name": str, "formation_url": str }
# The formation URL is the page you land on when clicking "Inserisci formazione"
LEAGUES = [
    {
        "name": "rusticatorsapienza",
        "formation_url": "https://leghe.fantacalcio.it/rusticatorsapienza/area-gioco/inserisci-formazione?id=362238",
    },
    {
        "name": "ilprimoverofanta",
        "formation_url": "https://leghe.fantacalcio.it/ilprimoverofanta/area-gioco/inserisci-formazione?id=416045",
    },
]

# ── Formation ─────────────────────────────────────────────────────────────────
# How many players per role in your preferred starting 11
# Roles on fantacalcio: P (portiere), D (difensore), C (centrocampista), A (attaccante)
FORMATION = {
    "P": 1,
    "D": 4,
    "C": 3,
    "A": 3,
}

# ── Scoring weights ───────────────────────────────────────────────────────────
# Final score = rating * W_RATING + fvm * W_FVM + (1 if home else 0) * W_HOME
# Tweak these to change how the optimizer prioritises players.
W_RATING = 1.0   # fantacalcio average rating (voto medio)
W_FVM    = 0.5   # fantacalcio FVM (fantasy value median)
W_HOME   = 0.3   # bonus for a home fixture this matchday

# ── Player overrides ─────────────────────────────────────────────────────────
# Force a player in or out regardless of their score.
# Use the exact name as it appears on fantacalcio.it
MUST_START = []   # e.g. ["Leao", "Bastoni"]
MUST_BENCH = []   # e.g. ["Immobile"]   (injured but still in squad)

# ── Paths ─────────────────────────────────────────────────────────────────────
PLAYERS_FILE = "players.json"   # scraped squad data (auto-generated)
LINEUP_FILE  = "lineup.json"    # chosen lineup (auto-generated)
LOG_FILE     = "bot.log"

# ── Selenium ──────────────────────────────────────────────────────────────────
HEADLESS      = True    # set False to watch the browser
PAGE_LOAD_WAIT = 3      # seconds to wait after page loads
LOGIN_URL     = "https://www.fantacalcio.it"
