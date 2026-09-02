from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dofus_pvp_bot.domain.models import FightBalance, PointsBreakdown, SubmissionLane


class ScoringError(ValueError):
    """Résultat de détection ou configuration du barème invalide."""


@dataclass(frozen=True, slots=True)
class ScoringRules:
    version: str
    equal_or_outnumbered_points: int
    opponents_outnumbered_points: int
    lane_multipliers: dict[SubmissionLane, int]

    @classmethod
    def from_file(cls, path: Path) -> ScoringRules:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        points = raw["fight_balance"]
        multipliers = {
            SubmissionLane(key): int(value)
            for key, value in raw["lane_multipliers"].items()
        }
        rules = cls(
            version=str(raw["version"]),
            equal_or_outnumbered_points=int(points["equal_or_outnumbered"]),
            opponents_outnumbered_points=int(points["opponents_outnumbered"]),
            lane_multipliers=multipliers,
        )
        rules._validate_configuration()
        return rules

    def _validate_configuration(self) -> None:
        if not self.version.strip():
            raise ScoringError("La version du barème ne peut pas être vide.")
        if self.equal_or_outnumbered_points < 0 or self.opponents_outnumbered_points < 0:
            raise ScoringError("Les points doivent être positifs ou nuls.")
        missing_lanes = set(SubmissionLane) - self.lane_multipliers.keys()
        if missing_lanes:
            missing = ", ".join(sorted(lane.value for lane in missing_lanes))
            raise ScoringError(f"Multiplicateur manquant pour : {missing}.")
        if any(multiplier < 1 for multiplier in self.lane_multipliers.values()):
            raise ScoringError("Les multiplicateurs doivent être supérieurs ou égaux à 1.")


class ScoringEngine:
    def __init__(self, rules: ScoringRules) -> None:
        self.rules = rules

    def calculate(
        self,
        fight_balance: FightBalance,
        lane: SubmissionLane,
    ) -> PointsBreakdown:
        base = (
            self.rules.equal_or_outnumbered_points
            if fight_balance is FightBalance.EQUAL_OR_OUTNUMBERED
            else self.rules.opponents_outnumbered_points
        )
        multiplier = self.rules.lane_multipliers[lane]
        total = base * multiplier
        lines = [
            fight_balance.label,
            f"Base : {base}",
            f"Multiplicateur {lane.label} : ×{multiplier}",
            f"Total : {total}",
        ]
        return PointsBreakdown(
            rule_version=self.rules.version,
            base_points=base,
            multiplier=multiplier,
            total_points=total,
            explanation=tuple(lines),
        )
