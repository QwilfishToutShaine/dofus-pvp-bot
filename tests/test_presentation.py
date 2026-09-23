from __future__ import annotations

import unittest

from dofus_pvp_bot.discord_app.presentation import build_leaderboard_embed
from dofus_pvp_bot.domain.leaderboard import LeaderboardEntry, MonthlyLeaderboard, MonthPeriod


class LeaderboardPresentationTest(unittest.TestCase):
    def test_uses_resolved_names_instead_of_raw_mentions(self) -> None:
        leaderboard = MonthlyLeaderboard(
            period=MonthPeriod(2026, 9),
            entries=(
                LeaderboardEntry(10, 8, 2, 1),
                LeaderboardEntry(20, 4, 1, 2),
            ),
            approved_submission_count=3,
            pending_submission_count=0,
            finalized=False,
        )

        embed = build_leaderboard_embed(
            leaderboard,
            {10: "[TBK] Kyss", 20: "Pseudo_*"},
        )

        self.assertNotIn("<@", embed.description or "")
        self.assertIn("[TBK] Kyss", embed.description or "")
        self.assertIn(r"Pseudo\_\*", embed.description or "")

    def test_keeps_the_id_only_as_an_unresolvable_user_fallback(self) -> None:
        leaderboard = MonthlyLeaderboard(
            period=MonthPeriod(2026, 9),
            entries=(LeaderboardEntry(1477560394695577782, 4, 1, 1),),
            approved_submission_count=1,
            pending_submission_count=0,
            finalized=False,
        )

        embed = build_leaderboard_embed(leaderboard, {})

        self.assertIn("Utilisateur inconnu", embed.description or "")
        self.assertIn("1477560394695577782", embed.description or "")


if __name__ == "__main__":
    unittest.main()
