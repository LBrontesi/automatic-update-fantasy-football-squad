from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from squad_picker import Player, compute_score

DEFAULT_WEIGHTS = {"avg": 0.4, "trend": 0.3, "home": 0.2, "opponent": 0.1}


@dataclass
class LeagueData:
    name: str
    url: str
    players: list[Player] = field(default_factory=list)
    lineup_empty: bool | None = None


class BasePredictionSource(ABC):
    @abstractmethod
    def predict(self, league) -> dict[str, float]:
        """Return a dict of player name -> expected score."""
        raise NotImplementedError


class HistoricalSource(BasePredictionSource):
    """Scores players from their past league votes (last-5 average, trend, venue)."""

    def __init__(self, weights: dict[str, float] | None = None):
        self.weights = dict(DEFAULT_WEIGHTS if weights is None else weights)

    def predict(self, league) -> dict[str, float]:
        return {p.name: compute_score(p, self.weights) for p in league.players}


class ExternalSourceStub(BasePredictionSource):
    """Future: forward-looking 'previsioni voti' from fantacalcio.it / Gazzetta API."""

    def predict(self, league) -> dict[str, float]:
        raise NotImplementedError(
            "The external previsioni-voti source is not implemented yet. "
            "Pick PREDICTION_SOURCE=historical for now."
        )


def get_prediction_source(name: str, weights: dict[str, float] | None = None) -> BasePredictionSource:
    if name == "historical":
        return HistoricalSource(weights=weights)
    if name == "external":
        return ExternalSourceStub()
    raise ValueError(f"Unknown PREDICTION_SOURCE {name!r}; use 'historical' or 'external'")
