from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dofus_pvp_bot.application.leaderboards import LeaderboardService
from dofus_pvp_bot.domain.leaderboard import MonthPeriod, assign_competition_ranks
from dofus_pvp_bot.domain.models import (
    FightBalance,
    PointsBreakdown,
    Submission,
    SubmissionLane,
    SubmissionStatus,
)
from dofus_pvp_bot.storage.sqlite import SQLiteSubmissionRepository


class LeaderboardDomainTest(unittest.TestCase):
    def test_competition_ranks_are_one_one_three(self) -> None:
        entries = assign_competition_ranks(
            [
                (30, 4, 1),
                (10, 12, 3),
                (20, 12, 2),
                (40, 1, 1),
            ]
        )
        self.assertEqual(
            [(entry.user_id, entry.total_points, entry.rank) for entry in entries],
            [(10, 12, 1), (20, 12, 1), (30, 4, 3), (40, 1, 4)],
        )

    def test_month_boundaries_follow_europe_paris_timezone(self) -> None:
        timezone = ZoneInfo("Europe/Paris")
        self.assertEqual(
            MonthPeriod(2026, 9).utc_bounds(timezone),
            ("2026-08-31 22:00:00", "2026-09-30 22:00:00"),
        )
        self.assertEqual(
            MonthPeriod(2026, 10).utc_bounds(timezone),
            ("2026-09-30 22:00:00", "2026-10-31 23:00:00"),
        )

    def test_month_parser_rejects_invalid_values(self) -> None:
        self.assertEqual(MonthPeriod.parse("2026-09"), MonthPeriod(2026, 9))
        for invalid in ("septembre", "2026-13", "26-09", "2026/09"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                MonthPeriod.parse(invalid)


class LeaderboardServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database = Path(self.temporary_directory.name) / "leaderboard.sqlite3"
        self.repository = SQLiteSubmissionRepository(database)
        await self.repository.initialize()
        self.timezone = ZoneInfo("Europe/Paris")
        self.service = LeaderboardService(self.repository, self.timezone)
        self.message_id = 1000

    async def asyncTearDown(self) -> None:
        self.temporary_directory.cleanup()

    async def _store_submission(
        self,
        *,
        participant_ids: list[int],
        points: int,
        submitted_at: str,
        status: SubmissionStatus = SubmissionStatus.APPROVED,
    ) -> Submission:
        self.message_id += 1
        submission = Submission(
            id=f"submission-{self.message_id}",
            guild_id=1,
            source_channel_id=2,
            source_message_id=self.message_id,
            submitter_id=99,
            lane=SubmissionLane.NORMAL,
            status=status,
            fight_balance=FightBalance.EQUAL_OR_OUTNUMBERED,
            allies_count=1,
            opponents_count=1,
            participant_ids=participant_ids,
            points=PointsBreakdown(
                rule_version="test",
                base_points=points,
                multiplier=1,
                total_points=points,
                explanation=("Test",),
            ),
            review_submitted_at=submitted_at,
            approved_at=submitted_at if status is SubmissionStatus.APPROVED else None,
        )
        await self.repository.create_draft(submission)
        await self.repository.save(submission)
        return submission

    async def test_current_month_is_provisional_and_counts_each_beneficiary(self) -> None:
        await self._store_submission(
            participant_ids=[10, 20], points=4, submitted_at="2026-09-01 10:00:00"
        )
        await self._store_submission(
            participant_ids=[10], points=1, submitted_at="2026-09-02 10:00:00"
        )
        await self._store_submission(
            participant_ids=[30], points=4, submitted_at="2026-09-03 10:00:00"
        )

        leaderboard = await self.service.get(
            1,
            MonthPeriod(2026, 9),
            now=datetime(2026, 9, 15, tzinfo=UTC),
        )

        self.assertFalse(leaderboard.finalized)
        self.assertEqual(leaderboard.approved_submission_count, 3)
        self.assertEqual(
            [
                (entry.user_id, entry.total_points, entry.submission_count, entry.rank)
                for entry in leaderboard.entries
            ],
            [(10, 5, 2, 1), (20, 4, 1, 2), (30, 4, 1, 2)],
        )

    async def test_past_month_waits_for_staff_then_freezes_snapshot(self) -> None:
        await self._store_submission(
            participant_ids=[10], points=4, submitted_at="2026-08-31 21:30:00"
        )
        pending = await self._store_submission(
            participant_ids=[20],
            points=1,
            submitted_at="2026-08-20 10:00:00",
            status=SubmissionStatus.PENDING_REVIEW,
        )
        now = datetime(2026, 9, 1, 1, tzinfo=UTC)

        waiting = await self.service.get(1, MonthPeriod(2026, 8), now=now)
        self.assertFalse(waiting.finalized)
        self.assertEqual(waiting.pending_submission_count, 1)

        pending.status = SubmissionStatus.REJECTED
        pending.points = None
        await self.repository.save(pending)
        finalized = await self.service.get(1, MonthPeriod(2026, 8), now=now)
        self.assertTrue(finalized.finalized)
        self.assertEqual(
            [(entry.user_id, entry.total_points) for entry in finalized.entries],
            [(10, 4)],
        )

        await self._store_submission(
            participant_ids=[30], points=8, submitted_at="2026-08-15 10:00:00"
        )
        frozen = await self.service.get(1, MonthPeriod(2026, 8), now=now)
        self.assertEqual(
            [(entry.user_id, entry.total_points) for entry in frozen.entries],
            [(10, 4)],
        )

    async def test_utc_boundary_assigns_submission_to_local_month(self) -> None:
        await self._store_submission(
            participant_ids=[10], points=4, submitted_at="2026-08-31 21:59:59"
        )
        await self._store_submission(
            participant_ids=[20], points=4, submitted_at="2026-08-31 22:00:00"
        )
        now = datetime(2026, 9, 15, tzinfo=UTC)

        august = await self.service.get(1, MonthPeriod(2026, 8), now=now)
        september = await self.service.get(1, MonthPeriod(2026, 9), now=now)
        self.assertEqual([entry.user_id for entry in august.entries], [10])
        self.assertEqual([entry.user_id for entry in september.entries], [20])


if __name__ == "__main__":
    unittest.main()
