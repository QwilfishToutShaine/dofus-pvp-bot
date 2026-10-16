from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from dofus_pvp_bot.application.leaderboards import LeaderboardService
from dofus_pvp_bot.discord_app.bot import DofusPvpBot
from dofus_pvp_bot.domain.leaderboard import FortnightPeriod, assign_competition_ranks
from dofus_pvp_bot.domain.models import (
    CombatType,
    FightBalance,
    PointsBreakdown,
    Submission,
    SubmissionLane,
    SubmissionStatus,
)
from dofus_pvp_bot.storage.sqlite import SQLiteSubmissionRepository


class LeaderboardDomainTest(unittest.TestCase):
    def test_command_exposes_optional_period_and_category(self) -> None:
        bot = DofusPvpBot(Mock(), Mock(), Mock(), Mock())
        command = bot.tree.get_command("classement")
        self.assertIsNotNone(command)
        assert command is not None
        options = command.to_dict(bot.tree)["options"]
        self.assertEqual([option["name"] for option in options], ["periode", "categorie"])
        self.assertTrue(all(not option["required"] for option in options))
        self.assertEqual(
            [choice["value"] for choice in options[1]["choices"]],
            ["global", "attack", "defense"],
        )

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

    def test_fortnight_boundaries_follow_europe_paris_timezone(self) -> None:
        timezone = ZoneInfo("Europe/Paris")
        self.assertEqual(
            FortnightPeriod(2026, 9, 1).utc_bounds(timezone),
            ("2026-08-31 22:00:00", "2026-09-15 22:00:00"),
        )
        self.assertEqual(
            FortnightPeriod(2026, 10, 2).utc_bounds(timezone),
            ("2026-10-15 22:00:00", "2026-10-31 23:00:00"),
        )
        self.assertEqual(FortnightPeriod(2026, 12, 2).next(), FortnightPeriod(2027, 1, 1))
        self.assertEqual(FortnightPeriod(2027, 1, 1).previous(), FortnightPeriod(2026, 12, 2))
        self.assertEqual(
            FortnightPeriod.current(timezone, datetime(2026, 9, 15, 22, tzinfo=UTC)),
            FortnightPeriod(2026, 9, 2),
        )

    def test_fortnight_parser_rejects_invalid_values(self) -> None:
        self.assertEqual(FortnightPeriod.parse("2026-09-1"), FortnightPeriod(2026, 9, 1))
        for invalid in ("septembre", "2026-13-1", "26-09-1", "2026/09", "2026-09", "2026-09-3"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                FortnightPeriod.parse(invalid)


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
        combat_type: CombatType | None = None,
        guild_id: int = 1,
    ) -> Submission:
        self.message_id += 1
        submission = Submission(
            id=f"submission-{self.message_id}",
            guild_id=guild_id,
            source_channel_id=2,
            source_message_id=self.message_id,
            submitter_id=99,
            lane=SubmissionLane.NORMAL,
            status=status,
            combat_type=combat_type,
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

    async def test_current_fortnight_is_provisional_and_counts_each_beneficiary(self) -> None:
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
            FortnightPeriod(2026, 9, 1),
            now=datetime(2026, 9, 15, 20, tzinfo=UTC),
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

    async def test_past_fortnight_waits_for_staff_then_freezes_snapshot(self) -> None:
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

        waiting = await self.service.get(1, FortnightPeriod(2026, 8, 2), now=now)
        self.assertFalse(waiting.finalized)
        self.assertEqual(waiting.pending_submission_count, 1)

        pending.status = SubmissionStatus.REJECTED
        pending.points = None
        await self.repository.save(pending)
        finalized = await self.service.get(1, FortnightPeriod(2026, 8, 2), now=now)
        self.assertTrue(finalized.finalized)
        self.assertEqual(
            [(entry.user_id, entry.total_points) for entry in finalized.entries],
            [(10, 4)],
        )

        await self._store_submission(
            participant_ids=[30], points=8, submitted_at="2026-08-20 10:00:00"
        )
        frozen = await self.service.get(1, FortnightPeriod(2026, 8, 2), now=now)
        self.assertEqual(
            [(entry.user_id, entry.total_points) for entry in frozen.entries],
            [(10, 4)],
        )

    async def test_utc_boundary_assigns_submission_to_local_fortnight(self) -> None:
        await self._store_submission(
            participant_ids=[10], points=4, submitted_at="2026-08-31 21:59:59"
        )
        await self._store_submission(
            participant_ids=[20], points=4, submitted_at="2026-08-31 22:00:00"
        )
        now = datetime(2026, 9, 15, tzinfo=UTC)

        august = await self.service.get(1, FortnightPeriod(2026, 8, 2), now=now)
        september = await self.service.get(1, FortnightPeriod(2026, 9, 1), now=now)
        self.assertEqual([entry.user_id for entry in august.entries], [10])
        self.assertEqual([entry.user_id for entry in september.entries], [20])

    async def test_filters_type_without_losing_legacy_global_points(self) -> None:
        for user_id, combat_type, points in (
            (10, CombatType.ATTACK, 4),
            (20, CombatType.DEFENSE, 4),
            (10, CombatType.DEFENSE, 1),
            (30, None, 8),
        ):
            await self._store_submission(
                participant_ids=[user_id],
                combat_type=combat_type,
                points=points,
                submitted_at="2026-09-18 10:00:00",
            )
        period = FortnightPeriod(2026, 9, 2)
        now = datetime(2026, 9, 19, tzinfo=UTC)
        global_board = await self.service.get(1, period, now=now)
        attacks = await self.service.get(1, period, now=now, combat_type=CombatType.ATTACK)
        defenses = await self.service.get(1, period, now=now, combat_type=CombatType.DEFENSE)
        self.assertEqual(global_board.approved_submission_count, 4)
        self.assertEqual(
            [(e.user_id, e.total_points, e.rank) for e in global_board.entries],
            [(30, 8, 1), (10, 5, 2), (20, 4, 3)],
        )
        self.assertEqual(attacks.approved_submission_count, 1)
        self.assertEqual([(e.user_id, e.total_points) for e in attacks.entries], [(10, 4)])
        self.assertEqual(defenses.approved_submission_count, 2)
        self.assertEqual(
            [(e.user_id, e.total_points, e.rank) for e in defenses.entries],
            [(20, 4, 1), (10, 1, 2)],
        )

    async def test_pending_unknown_type_blocks_all_views_until_review(self) -> None:
        pending = await self._store_submission(
            participant_ids=[10],
            points=4,
            submitted_at="2026-09-15 21:30:00",
            status=SubmissionStatus.PENDING_REVIEW,
        )
        period = FortnightPeriod(2026, 9, 1)
        now = datetime(2026, 9, 16, 1, tzinfo=UTC)
        for combat_type in (None, CombatType.ATTACK, CombatType.DEFENSE):
            board = await self.service.get(1, period, now=now, combat_type=combat_type)
            self.assertFalse(board.finalized)
            self.assertEqual(board.pending_submission_count, 1)
        pending.combat_type = CombatType.ATTACK
        await self.repository.save(pending)
        defense = await self.service.get(1, period, now=now, combat_type=CombatType.DEFENSE)
        self.assertTrue(defense.finalized)
        self.assertEqual(defense.pending_submission_count, 0)
        self.assertFalse((await self.service.get(1, period, now=now)).finalized)

    async def test_auto_closure_creates_separate_snapshots(self) -> None:
        await self._store_submission(
            participant_ids=[10],
            points=4,
            combat_type=CombatType.ATTACK,
            submitted_at="2026-09-15 21:59:59",
        )
        await self._store_submission(
            participant_ids=[20],
            points=1,
            combat_type=CombatType.DEFENSE,
            submitted_at="2026-09-15 22:00:00",
        )
        closed = await self.service.close_due_periods(1, now=datetime(2026, 9, 17, tzinfo=UTC))
        first = [board for board in closed if board.period == FortnightPeriod(2026, 9, 1)]
        self.assertEqual(len(first), 3)
        self.assertTrue(all(board.finalized for board in first))
        self.assertEqual([board.approved_submission_count for board in first], [1, 1, 0])
        self.assertEqual(
            (
                await self.service.get(
                    1, FortnightPeriod(2026, 9, 2), now=datetime(2026, 9, 17, tzinfo=UTC)
                )
            ).approved_submission_count,
            1,
        )

    async def test_new_period_tables_preserve_existing_monthly_archive(self) -> None:
        with closing(sqlite3.connect(self.repository.path)) as connection, connection:
            connection.execute(
                "INSERT INTO leaderboard_months "
                "(guild_id, period_key, timezone, approved_submission_count) "
                "VALUES (1, '2026-08', 'Europe/Paris', 5)"
            )
        await self.repository.initialize()
        with closing(sqlite3.connect(self.repository.path)) as connection:
            old_archive = connection.execute(
                "SELECT approved_submission_count FROM leaderboard_months "
                "WHERE guild_id = 1 AND period_key = '2026-08'"
            ).fetchone()
        self.assertEqual(old_archive, (5,))
        current = await self.service.get(
            1, FortnightPeriod(2026, 9, 1), now=datetime(2026, 9, 2, tzinfo=UTC)
        )
        self.assertEqual(current.approved_submission_count, 0)


if __name__ == "__main__":
    unittest.main()
