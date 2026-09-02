from __future__ import annotations

import unittest
from pathlib import Path

from dofus_pvp_bot.domain.models import FightBalance, SubmissionLane
from dofus_pvp_bot.domain.scoring import ScoringEngine, ScoringError, ScoringRules


class ScoringEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        rules_path = Path(__file__).parents[1] / "config" / "scoring.json"
        cls.engine = ScoringEngine(ScoringRules.from_file(rules_path))

    def test_equal_teams_are_four_points_in_normal_lane(self) -> None:
        result = self.engine.calculate(
            FightBalance.from_counts(allies=1, opponents=1),
            SubmissionLane.NORMAL,
        )
        self.assertEqual(result.base_points, 4)
        self.assertEqual(result.multiplier, 1)
        self.assertEqual(result.total_points, 4)

    def test_allies_outnumbered_are_four_points(self) -> None:
        result = self.engine.calculate(
            FightBalance.from_counts(allies=1, opponents=2),
            SubmissionLane.NORMAL,
        )
        self.assertEqual(result.total_points, 4)

    def test_only_opponents_outnumbered_are_one_point(self) -> None:
        result = self.engine.calculate(
            FightBalance.from_counts(allies=3, opponents=2),
            SubmissionLane.NORMAL,
        )
        self.assertEqual(result.total_points, 1)

    def test_sng_lane_doubles_both_scores(self) -> None:
        equal_or_outnumbered = self.engine.calculate(
            FightBalance.EQUAL_OR_OUTNUMBERED,
            SubmissionLane.SNG,
        )
        opponents_outnumbered = self.engine.calculate(
            FightBalance.OPPONENTS_OUTNUMBERED,
            SubmissionLane.SNG,
        )
        self.assertEqual(equal_or_outnumbered.total_points, 8)
        self.assertEqual(opponents_outnumbered.total_points, 2)

    def test_missing_lane_multiplier_is_rejected(self) -> None:
        rules = ScoringRules(
            version="test",
            equal_or_outnumbered_points=4,
            opponents_outnumbered_points=1,
            lane_multipliers={SubmissionLane.NORMAL: 1},
        )
        with self.assertRaisesRegex(ScoringError, "sng"):
            rules._validate_configuration()


if __name__ == "__main__":
    unittest.main()
