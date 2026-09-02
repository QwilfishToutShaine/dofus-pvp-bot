from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from dofus_pvp_bot.analysis.ocr import OcrBackend, RapidOcrBackend
from dofus_pvp_bot.analysis.result_parser import (
    CombatRow,
    ParsedCombatImage,
    names_match,
    parse_combat_image,
)
from dofus_pvp_bot.analysis.team_balance import ImageEvidence
from dofus_pvp_bot.domain.models import DetectionResult, FightBalance

LOGGER = logging.getLogger(__name__)
MIN_AUTOMATIC_CONFIDENCE = 0.82


class RapidOcrTeamBalanceDetector:
    """Compte les joueurs des résultats de combat avec un repli manuel sûr."""

    def __init__(
        self,
        backend: OcrBackend | None = None,
        *,
        max_image_pixels: int = 20_000_000,
    ) -> None:
        self.backend = backend or RapidOcrBackend(max_image_pixels=max_image_pixels)
        self._analysis_lock = asyncio.Lock()

    async def detect(self, images: Sequence[ImageEvidence]) -> DetectionResult:
        if not images:
            raise ValueError("Au moins une capture est nécessaire.")
        async with self._analysis_lock:
            return await asyncio.to_thread(self._detect_synchronously, images)

    def _detect_synchronously(self, images: Sequence[ImageEvidence]) -> DetectionResult:
        parsed_images: list[ParsedCombatImage] = []
        issues: list[str] = []
        for image in images:
            try:
                parsed = parse_combat_image(self.backend.recognize(image.content))
            except (RuntimeError, ValueError) as exc:
                LOGGER.warning("OCR impossible pour %s : %s", image.filename, exc)
                issues.append(f"{image.filename} : OCR impossible")
                continue
            if parsed.issue is not None:
                issues.append(f"{image.filename} : {parsed.issue}")
                continue
            parsed_images.append(parsed)

        if not parsed_images:
            detail = " ".join(issues) or "Aucune capture exploitable."
            return DetectionResult(fight_balance=None, detail=detail[:500])
        if issues:
            return DetectionResult(
                fight_balance=None,
                detail=(
                    "Au moins une capture jointe n’a pas pu être analysée. "
                    "Confirme les effectifs manuellement."
                ),
            )

        durations = {image.duration_seconds for image in parsed_images}
        if len(parsed_images) > 1 and (None in durations or len(durations) != 1):
            return DetectionResult(
                fight_balance=None,
                detail=(
                    "Les captures ne peuvent pas être rattachées avec certitude au même combat. "
                    "Un seul combat est autorisé par message."
                ),
            )

        winners = self._unique_rows(parsed_images, "winners", objective=False)
        losers = self._unique_rows(parsed_images, "losers", objective=False)
        objectives = self._unique_rows(parsed_images, None, objective=True)
        if len(objectives) != 1:
            return DetectionResult(
                fight_balance=None,
                detail=(
                    "L’analyse n’a pas identifié exactement un percepteur ou un prisme. "
                    "Confirme les effectifs manuellement."
                ),
            )
        objective = objectives[0]
        if objective.objective_evidence == "alignement sans portrait":
            return DetectionResult(
                fight_balance=None,
                detail=(
                    f"Objectif probable ({objective.name}), mais son nom est incertain. "
                    "Confirme les effectifs manuellement."
                ),
            )
        if not 1 <= len(winners) <= 4 or not 0 <= len(losers) <= 4:
            return DetectionResult(
                fight_balance=None,
                detail=(
                    f"Effectifs OCR incohérents : {len(winners)} gagnant(s), "
                    f"{len(losers)} perdant(s). Confirme manuellement."
                ),
            )

        confidence = min(
            [image.confidence for image in parsed_images]
            + [row.confidence for row in winners + losers + objectives]
            + [0.95]
        )
        if confidence < MIN_AUTOMATIC_CONFIDENCE:
            return DetectionResult(
                fight_balance=None,
                detail=(
                    f"Confiance OCR insuffisante ({confidence:.0%}). "
                    "Confirme les effectifs manuellement."
                ),
            )
        balance = FightBalance.from_counts(len(winners), len(losers))
        return DetectionResult(
            fight_balance=balance,
            allies_count=len(winners),
            opponents_count=len(losers),
            confidence=confidence,
            detail=(
                f"OCR : {len(winners)} allié(s) contre {len(losers)} adversaire(s). "
                f"Objectif exclu : {objective.name}."
            ),
        )

    @staticmethod
    def _unique_rows(
        parsed_images: Sequence[ParsedCombatImage],
        side: str | None,
        *,
        objective: bool,
    ) -> list[CombatRow]:
        unique: list[CombatRow] = []
        for parsed in parsed_images:
            # Deux lignes d'une même capture représentent nécessairement deux
            # combattants distincts, même lorsque leurs noms sont très proches
            # (par exemple ``Perk-I`` et ``Perk-II``). La déduplication sert
            # uniquement à supprimer le chevauchement entre plusieurs captures
            # avec défilement du même résultat de combat.
            previous_images = tuple(unique)
            current_image: list[CombatRow] = []
            for row in parsed.rows:
                if row.is_objective is not objective:
                    continue
                if side is not None and row.side != side:
                    continue
                if any(
                    names_match(row.name, existing.name)
                    for existing in previous_images
                ):
                    continue
                current_image.append(row)
            unique.extend(current_image)
        return unique
