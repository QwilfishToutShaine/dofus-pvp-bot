from __future__ import annotations

import unittest

from dofus_pvp_bot.analysis.ocr import OcrToken
from dofus_pvp_bot.analysis.rapidocr_detector import RapidOcrTeamBalanceDetector
from dofus_pvp_bot.analysis.result_parser import names_match, parse_combat_image
from dofus_pvp_bot.analysis.team_balance import ImageEvidence
from dofus_pvp_bot.domain.models import FightBalance


def token(text: str, x: float, y: float, *, confidence: float = 0.98) -> OcrToken:
    return OcrToken(text, confidence, x, y, x + 80, y + 24)


def base_tokens() -> list[OcrToken]:
    return [
        token("Résultat du combat", 350, 10),
        token("Victoire", 250, 60),
        token("Durée :00:01:30 (2 tours)", 550, 60),
        token("Nom", 300, 110),
        token("Niv.", 500, 110),
        token("Gagnants", 220, 150),
        token("Perdants", 220, 430),
    ]


def row(name: str, y: float, *, objective: bool = False) -> list[OcrToken]:
    name_x = 180 if objective else 280
    return [token(name, name_x, y), token("200", 505, y)]


class ResultParserTest(unittest.TestCase):
    def test_parses_human_rows_and_excludes_perceptor(self) -> None:
        tokens = base_tokens()
        tokens += row("Alpha", 210)
        tokens += row("Beta", 270)
        tokens += row("Gamma", 330)
        tokens += row("Licar Do'Bradiga", 390, objective=True)
        for index, name in enumerate(("One", "Two", "Three", "Four")):
            tokens += row(name, 480 + index * 60)

        parsed = parse_combat_image(tokens)

        self.assertIsNone(parsed.issue)
        self.assertEqual(
            len([item for item in parsed.rows if item.side == "winners" and not item.is_objective]),
            3,
        )
        self.assertEqual(
            len([item for item in parsed.rows if item.side == "losers" and not item.is_objective]),
            4,
        )
        self.assertEqual(len([item for item in parsed.rows if item.is_objective]), 1)

    def test_prism_is_detected_by_its_name(self) -> None:
        tokens = base_tokens()
        tokens += row("Alpha", 210)
        tokens += row("Prisme d'alliance", 270)
        tokens += row("Enemy", 480)

        parsed = parse_combat_image(tokens)

        objectives = [item for item in parsed.rows if item.is_objective]
        self.assertEqual([item.name for item in objectives], ["Prisme d'alliance"])

    def test_nearest_name_is_paired_with_a_level_on_tilted_photo(self) -> None:
        tokens = base_tokens()
        tokens += [token("First", 280, 210, confidence=1.0), token("200", 505, 225)]
        tokens += [token("Second", 280, 270, confidence=0.90), token("200", 505, 285)]
        tokens += row("Target Do'Name", 390, objective=True)
        tokens += row("Enemy", 480)

        parsed = parse_combat_image(tokens)

        winner_names = [item.name for item in parsed.rows if item.side == "winners"]
        self.assertEqual(winner_names, ["First", "Second", "Target Do'Name"])

    def test_extracts_combat_duration(self) -> None:
        tokens = base_tokens() + row("Alpha", 210) + row("Target Do'Name", 390)
        parsed = parse_combat_image(tokens)
        self.assertEqual(parsed.duration_seconds, 90)

    def test_rejects_an_image_without_victory_marker(self) -> None:
        tokens = [item for item in base_tokens() if item.text != "Victoire"]
        parsed = parse_combat_image(tokens)
        self.assertEqual(parsed.rows, ())
        self.assertIn("victoire", parsed.issue or "")

    def test_truncated_and_complete_names_match(self) -> None:
        self.assertTrue(names_match("Venissieux-...", "Venissieux-complet"))
        self.assertFalse(names_match("Deus-nova", "Deus-cura"))


class FakeBackend:
    def __init__(self, results: dict[bytes, list[OcrToken]]) -> None:
        self.results = results

    def recognize(self, image: bytes) -> list[OcrToken]:
        return self.results[image]


class RapidOcrTeamBalanceDetectorTest(unittest.IsolatedAsyncioTestCase):
    async def test_aggregates_scrolled_images_without_counting_duplicates(self) -> None:
        first = base_tokens()
        for index, name in enumerate(("Alpha", "Beta", "Gamma", "Delta")):
            first += row(name, 210 + index * 50)
        first += row("Chene Do'Douce", 410, objective=True)

        second = base_tokens()
        second += row("Delta", 350)
        second += row("Chene Do'Douce", 400, objective=True)
        for index, name in enumerate(("One", "Two", "Three", "Four")):
            second += row(name, 480 + index * 50)

        detector = RapidOcrTeamBalanceDetector(
            FakeBackend({b"first": first, b"second": second})
        )
        result = await detector.detect(
            [
                ImageEvidence("first.png", "image/png", b"first"),
                ImageEvidence("second.png", "image/png", b"second"),
            ]
        )

        self.assertEqual(result.fight_balance, FightBalance.EQUAL_OR_OUTNUMBERED)
        self.assertEqual(result.allies_count, 4)
        self.assertEqual(result.opponents_count, 4)

    async def test_returns_undetermined_without_exactly_one_objective(self) -> None:
        tokens = base_tokens() + row("Alpha", 210) + row("Enemy", 480)
        detector = RapidOcrTeamBalanceDetector(FakeBackend({b"image": tokens}))

        result = await detector.detect(
            [ImageEvidence("image.png", "image/png", b"image")]
        )

        self.assertIsNone(result.fight_balance)
        self.assertIn("exactement un", result.detail or "")

    async def test_returns_undetermined_for_different_fight_durations(self) -> None:
        first = base_tokens() + row("Alpha", 210) + row("Target Do'Name", 390)
        second = [
            item
            for item in base_tokens()
            if not item.text.startswith("Durée")
        ]
        second.append(token("Durée :00:02:00 (3 tours)", 550, 60))
        second += row("Enemy", 480)
        detector = RapidOcrTeamBalanceDetector(
            FakeBackend({b"first": first, b"second": second})
        )

        result = await detector.detect(
            [
                ImageEvidence("first.png", "image/png", b"first"),
                ImageEvidence("second.png", "image/png", b"second"),
            ]
        )

        self.assertIsNone(result.fight_balance)
        self.assertIn("même combat", result.detail or "")

    async def test_alignment_only_objective_requires_manual_confirmation(self) -> None:
        tokens = base_tokens()
        tokens += row("Alpha", 210)
        tokens += row("Unclear Name", 390, objective=True)
        tokens += row("Enemy", 480)
        detector = RapidOcrTeamBalanceDetector(FakeBackend({b"image": tokens}))

        result = await detector.detect(
            [ImageEvidence("image.png", "image/png", b"image")]
        )

        self.assertIsNone(result.fight_balance)
        self.assertIn("probable", result.detail or "")


if __name__ == "__main__":
    unittest.main()
