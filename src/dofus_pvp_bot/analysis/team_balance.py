from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from dofus_pvp_bot.domain.models import DetectionResult


@dataclass(frozen=True, slots=True)
class ImageEvidence:
    filename: str
    content_type: str | None
    content: bytes


class TeamBalanceDetector(Protocol):
    async def detect(self, images: Sequence[ImageEvidence]) -> DetectionResult: ...


class UndeterminedTeamBalanceDetector:
    """Repli sûr tant que le détecteur n'a pas été calibré sur des captures réelles."""

    async def detect(self, images: Sequence[ImageEvidence]) -> DetectionResult:
        if not images:
            raise ValueError("Au moins une capture est nécessaire.")
        return DetectionResult(
            fight_balance=None,
            detail="Détecteur automatique en attente de calibration.",
        )
