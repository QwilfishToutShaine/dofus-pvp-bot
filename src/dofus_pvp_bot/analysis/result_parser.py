from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from dofus_pvp_bot.analysis.ocr import OcrToken


@dataclass(frozen=True, slots=True)
class CombatRow:
    name: str
    level: int
    side: str
    objective_evidence: str | None
    confidence: float

    @property
    def is_objective(self) -> bool:
        return self.objective_evidence is not None


@dataclass(frozen=True, slots=True)
class ParsedCombatImage:
    rows: tuple[CombatRow, ...]
    confidence: float
    duration_seconds: int | None = None
    issue: str | None = None


def _normalise(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", without_accents.casefold().replace("’", "'")).strip()


def _similarity(token: OcrToken, expected: str) -> float:
    value = _normalise(token.text)
    target = _normalise(expected)
    if value == target:
        return 1.0
    if target in value:
        return 0.95
    return SequenceMatcher(None, value, target).ratio()


def _best_token(
    tokens: list[OcrToken],
    expected: str,
    *,
    minimum_similarity: float = 0.72,
) -> OcrToken | None:
    candidates = [
        (token, _similarity(token, expected))
        for token in tokens
        if _similarity(token, expected) >= minimum_similarity
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[1] * candidate[0].confidence)[0]


def _is_integer(text: str) -> bool:
    return re.fullmatch(r"\d{1,3}", _normalise(text)) is not None


def _is_ui_text(text: str) -> bool:
    normalised = _normalise(text)
    return any(
        marker in normalised
        for marker in (
            "resultat du combat",
            "victoire",
            "duree",
            "gagnants",
            "perdants",
            "xp gagnee",
            "objets gagnes",
            "kamas",
        )
    ) or normalised in {"nom", "niv", "niv."}


def _objective_evidence(name: str, *, left: float, objective_cutoff: float) -> str | None:
    normalised = _normalise(name)
    if "prisme" in normalised:
        return "nom du prisme"
    if re.search(r"\b[a-z][a-z-]*\s+do['’]?[a-z-]+\b", normalised):
        return "nom du percepteur"
    if left < objective_cutoff:
        return "alignement sans portrait"
    return None


def _duration_seconds(tokens: list[OcrToken]) -> int | None:
    duration_token = _best_token(tokens, "durée", minimum_similarity=0.45)
    if duration_token is None:
        return None
    match = re.search(r"(\d{1,2})\D+(\d{2})\D+(\d{2})", duration_token.text)
    if match is None:
        return None
    hours, minutes, seconds = (int(value) for value in match.groups())
    if minutes >= 60 or seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


def parse_combat_image(tokens: list[OcrToken]) -> ParsedCombatImage:
    if not tokens:
        return ParsedCombatImage((), 0.0, issue="Aucun texte détecté.")

    title = _best_token(tokens, "résultat du combat")
    victory = _best_token(tokens, "victoire")
    name_header = _best_token(tokens, "nom", minimum_similarity=0.82)
    level_header = _best_token(tokens, "niv", minimum_similarity=0.72)
    winners_header = _best_token(tokens, "gagnants")
    losers_header = _best_token(tokens, "perdants")
    if title is None or victory is None:
        return ParsedCombatImage((), 0.0, issue="La capture ne montre pas une victoire.")
    if name_header is None or level_header is None:
        return ParsedCombatImage((), 0.0, issue="Les colonnes du résultat sont illisibles.")
    if winners_header is None and losers_header is None:
        return ParsedCombatImage(
            (),
            0.0,
            issue="Les sections Gagnants et Perdants sont illisibles.",
        )

    column_gap = max(level_header.left - name_header.left, level_header.width * 2)
    name_left_bound = name_header.left - 1.4 * column_gap
    name_right_bound = level_header.left - 2
    objective_cutoff = name_header.left - 0.4 * column_gap
    level_tolerance = max(level_header.width * 2, column_gap * 0.45)
    table_top = max(name_header.bottom, level_header.bottom)

    rows: list[CombatRow] = []
    for level_token in tokens:
        if not _is_integer(level_token.text):
            continue
        level = int(_normalise(level_token.text))
        if not 1 <= level <= 200 or level_token.center_y <= table_top:
            continue
        if abs(level_token.center_x - level_header.center_x) > level_tolerance:
            continue

        row_tolerance = max(level_token.height * 1.5, column_gap * 0.25, 16.0)
        name_candidates = [
            token
            for token in tokens
            if name_left_bound <= token.left < name_right_bound
            and abs(token.center_y - level_token.center_y) <= row_tolerance
            and not _is_integer(token.text)
            and not _is_ui_text(token.text)
        ]
        if not name_candidates:
            continue
        name_token = min(
            name_candidates,
            key=lambda token: (
                abs(token.center_y - level_token.center_y),
                -token.confidence,
            ),
        )

        side: str | None = None
        if losers_header is not None and level_token.center_y > losers_header.bottom:
            side = "losers"
        elif (
            (
                losers_header is not None
                and level_token.center_y < losers_header.top
                and (winners_header is None or level_token.center_y > winners_header.bottom)
            )
            or (
                losers_header is None
                and winners_header is not None
                and level_token.center_y > winners_header.bottom
            )
        ):
            side = "winners"
        if side is None:
            continue

        rows.append(
            CombatRow(
                name=name_token.text,
                level=level,
                side=side,
                objective_evidence=_objective_evidence(
                    name_token.text,
                    left=name_token.left,
                    objective_cutoff=objective_cutoff,
                ),
                confidence=min(name_token.confidence, level_token.confidence),
            )
        )

    if not rows:
        return ParsedCombatImage((), 0.0, issue="Aucune ligne de combattant lisible.")
    structural_tokens = [title, victory, name_header, level_header]
    if winners_header is not None:
        structural_tokens.append(winners_header)
    if losers_header is not None:
        structural_tokens.append(losers_header)
    confidence = min(
        [token.confidence for token in structural_tokens]
        + [row.confidence for row in rows]
    )
    return ParsedCombatImage(
        tuple(rows),
        min(confidence, 0.99),
        duration_seconds=_duration_seconds(tokens),
    )


def canonical_name(name: str) -> str:
    normalised = _normalise(name)
    normalised = re.sub(r"[^a-z0-9'….-]+$", "", normalised)
    return normalised.strip(" .")


def names_match(left: str, right: str) -> bool:
    first_is_truncated = _normalise(left).rstrip().endswith(("...", "…"))
    second_is_truncated = _normalise(right).rstrip().endswith(("...", "…"))
    first = canonical_name(left)
    second = canonical_name(right)
    if first == second:
        return True
    first_prefix = first.rstrip(".…")
    second_prefix = second.rstrip(".…")
    return (
        (first_is_truncated or second_is_truncated)
        and min(len(first_prefix), len(second_prefix)) >= 5
        and (
        first_prefix.startswith(second_prefix) or second_prefix.startswith(first_prefix)
        )
    )
