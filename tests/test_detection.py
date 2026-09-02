from __future__ import annotations

import unittest

from dofus_pvp_bot.analysis.team_balance import (
    ImageEvidence,
    UndeterminedTeamBalanceDetector,
)
from dofus_pvp_bot.domain.models import DetectionResult, FightBalance


class DetectionResultTest(unittest.TestCase):
    def test_confidence_must_be_between_zero_and_one(self) -> None:
        with self.assertRaisesRegex(ValueError, "comprise entre 0 et 1"):
            DetectionResult(FightBalance.EQUAL_OR_OUTNUMBERED, confidence=1.1)

    def test_confidence_requires_a_classification(self) -> None:
        with self.assertRaisesRegex(ValueError, "nécessite un résultat"):
            DetectionResult(None, confidence=0.5)

    def test_counts_determine_the_same_balance_as_the_category(self) -> None:
        result = DetectionResult(
            FightBalance.EQUAL_OR_OUTNUMBERED,
            allies_count=1,
            opponents_count=2,
            confidence=0.9,
        )
        self.assertEqual(result.allies_count, 1)
        self.assertEqual(result.opponents_count, 2)

    def test_inconsistent_counts_and_category_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "incohérents"):
            DetectionResult(
                FightBalance.OPPONENTS_OUTNUMBERED,
                allies_count=1,
                opponents_count=2,
            )


class UndeterminedTeamBalanceDetectorTest(unittest.IsolatedAsyncioTestCase):
    async def test_safe_fallback_does_not_invent_a_result(self) -> None:
        detector = UndeterminedTeamBalanceDetector()
        result = await detector.detect(
            [ImageEvidence("capture.png", "image/png", b"not-an-image-yet")]
        )
        self.assertIsNone(result.fight_balance)
        self.assertIsNone(result.confidence)


if __name__ == "__main__":
    unittest.main()
