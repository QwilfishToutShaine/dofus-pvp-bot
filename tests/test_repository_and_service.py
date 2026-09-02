from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dofus_pvp_bot.application.submissions import SubmissionError, SubmissionService
from dofus_pvp_bot.domain.models import (
    DetectionMethod,
    DetectionResult,
    FightBalance,
    SubmissionLane,
    SubmissionStatus,
)
from dofus_pvp_bot.domain.scoring import ScoringEngine, ScoringRules
from dofus_pvp_bot.storage.sqlite import SQLiteSubmissionRepository


class SubmissionServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        repository = SQLiteSubmissionRepository(root / "test.sqlite3")
        await repository.initialize()
        rules_path = Path(__file__).parents[1] / "config" / "scoring.json"
        self.repository = repository
        self.service = SubmissionService(
            repository,
            ScoringEngine(ScoringRules.from_file(rules_path)),
        )

    async def asyncTearDown(self) -> None:
        self.temporary_directory.cleanup()

    async def test_complete_automatic_sng_submission_lifecycle(self) -> None:
        draft = await self.service.create_draft(
            guild_id=1,
            channel_id=2,
            message_id=3,
            submitter_id=4,
            lane=SubmissionLane.SNG,
            detection=DetectionResult(
                FightBalance.EQUAL_OR_OUTNUMBERED,
                allies_count=1,
                opponents_count=2,
                confidence=0.91,
            ),
        )
        await self.service.set_participants(draft.id, [4, 5])

        ready = await self.service.require(draft.id)
        self.assertEqual(ready.detection_method, DetectionMethod.AUTOMATIC)
        self.assertEqual((ready.allies_count, ready.opponents_count), (1, 2))
        points = self.service.calculate(ready)
        self.assertEqual(points.total_points, 8)

        pending = await self.service.mark_pending(draft.id, 99, points)
        self.assertEqual(pending.status, SubmissionStatus.PENDING_REVIEW)
        self.assertIsNotNone(pending.review_submitted_at)
        approved = await self.service.approve(draft.id)
        self.assertEqual(approved.status, SubmissionStatus.APPROVED)
        self.assertIsNotNone(approved.approved_at)

    async def test_staff_correction_preserves_original_detection(self) -> None:
        draft = await self.service.create_draft(
            guild_id=1,
            channel_id=2,
            message_id=4,
            submitter_id=4,
            lane=SubmissionLane.NORMAL,
            detection=DetectionResult(
                FightBalance.EQUAL_OR_OUTNUMBERED,
                allies_count=1,
                opponents_count=1,
                confidence=0.75,
            ),
        )
        await self.service.set_participants(draft.id, [4])
        ready = await self.service.require(draft.id)
        await self.service.mark_pending(draft.id, 100, self.service.calculate(ready))
        corrected = await self.service.set_review_counts(draft.id, 4, 1)
        self.assertEqual(corrected.fight_balance, FightBalance.OPPONENTS_OUTNUMBERED)
        self.assertEqual((corrected.allies_count, corrected.opponents_count), (4, 1))
        self.assertEqual(
            (corrected.detected_allies_count, corrected.detected_opponents_count),
            (1, 1),
        )
        self.assertEqual(corrected.detection_method, DetectionMethod.MANUAL)
        self.assertIsNone(corrected.detection_confidence)
        assert corrected.points is not None
        self.assertEqual(corrected.points.total_points, 1)

    async def test_indeterminate_ocr_can_be_sent_but_requires_staff_counts(self) -> None:
        draft = await self.service.create_draft(
            guild_id=1,
            channel_id=2,
            message_id=40,
            submitter_id=4,
            lane=SubmissionLane.NORMAL,
            detection=DetectionResult(None, detail="Capture indéterminée"),
        )
        await self.service.set_participants(draft.id, [4])
        pending = await self.service.mark_pending(draft.id, 101, None)
        self.assertIsNone(pending.points)
        with self.assertRaisesRegex(SubmissionError, "renseigner les effectifs"):
            await self.service.approve(draft.id)

        corrected = await self.service.set_review_counts(draft.id, 1, 2)
        self.assertEqual(corrected.fight_balance, FightBalance.EQUAL_OR_OUTNUMBERED)
        assert corrected.points is not None
        self.assertEqual(corrected.points.total_points, 4)
        approved = await self.service.approve(draft.id)
        self.assertEqual(approved.status, SubmissionStatus.APPROVED)

    async def test_between_one_and_four_unique_participants_are_required(self) -> None:
        draft = await self.service.create_draft(
            guild_id=1,
            channel_id=2,
            message_id=5,
            submitter_id=4,
            lane=SubmissionLane.NORMAL,
        )
        with self.assertRaisesRegex(SubmissionError, "1 et 4"):
            await self.service.set_participants(draft.id, [])
        with self.assertRaisesRegex(SubmissionError, "1 et 4"):
            await self.service.set_participants(draft.id, [1, 2, 3, 4, 5])

    async def test_source_message_is_idempotent(self) -> None:
        first = await self.service.create_draft(
            guild_id=1,
            channel_id=2,
            message_id=42,
            submitter_id=4,
            lane=SubmissionLane.NORMAL,
        )
        second = await self.service.create_draft(
            guild_id=1,
            channel_id=2,
            message_id=42,
            submitter_id=4,
            lane=SubmissionLane.SNG,
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.lane, SubmissionLane.NORMAL)


class LegacyDatabaseMigrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_first_prototype_database_is_migrated_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "legacy.sqlite3"
            with sqlite3.connect(database_path) as connection:
                connection.executescript(
                    """
                    CREATE TABLE submissions (
                        id TEXT PRIMARY KEY,
                        guild_id INTEGER NOT NULL,
                        source_channel_id INTEGER NOT NULL,
                        source_message_id INTEGER NOT NULL UNIQUE,
                        submitter_id INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        prompt_message_id INTEGER,
                        review_message_id INTEGER,
                        context TEXT,
                        allies INTEGER,
                        allied_deaths INTEGER,
                        enemies INTEGER,
                        bonus_guild_key TEXT,
                        note TEXT,
                        rule_version TEXT,
                        base_points INTEGER,
                        bonus_points INTEGER,
                        total_points INTEGER,
                        points_explanation TEXT,
                        rejection_reason TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE submission_participants (
                        submission_id TEXT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
                        user_id INTEGER NOT NULL,
                        PRIMARY KEY (submission_id, user_id)
                    );
                    INSERT INTO submissions (
                        id, guild_id, source_channel_id, source_message_id,
                        submitter_id, status, allies, enemies, bonus_guild_key
                    ) VALUES ('legacy', 1, 2, 3, 4, 'draft', 3, 2, 'sng');
                    """
                )

            repository = SQLiteSubmissionRepository(database_path)
            await repository.initialize()
            migrated = await repository.get("legacy")

            self.assertIsNotNone(migrated)
            assert migrated is not None
            self.assertEqual(migrated.lane, SubmissionLane.SNG)
            self.assertEqual(migrated.fight_balance, FightBalance.OPPONENTS_OUTNUMBERED)
            self.assertEqual((migrated.allies_count, migrated.opponents_count), (3, 2))
            self.assertEqual(migrated.detected_allies_count, 3)
            self.assertEqual(migrated.detected_opponents_count, 2)
            self.assertEqual(migrated.detection_method, DetectionMethod.MANUAL)

    async def test_version_two_database_keeps_only_unambiguous_classifications(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "version-two.sqlite3"
            with sqlite3.connect(database_path) as connection:
                connection.executescript(
                    """
                    CREATE TABLE submissions (
                        id TEXT PRIMARY KEY,
                        guild_id INTEGER NOT NULL,
                        source_channel_id INTEGER NOT NULL,
                        source_message_id INTEGER NOT NULL UNIQUE,
                        submitter_id INTEGER NOT NULL,
                        lane TEXT NOT NULL DEFAULT 'normal',
                        status TEXT NOT NULL,
                        prompt_message_id INTEGER,
                        review_message_id INTEGER,
                        opponent_group TEXT,
                        detection_method TEXT,
                        detection_confidence REAL,
                        detection_detail TEXT,
                        note TEXT,
                        rule_version TEXT,
                        base_points INTEGER,
                        multiplier INTEGER,
                        total_points INTEGER,
                        points_explanation TEXT,
                        rejection_reason TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE submission_participants (
                        submission_id TEXT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
                        user_id INTEGER NOT NULL,
                        PRIMARY KEY (submission_id, user_id)
                    );
                    INSERT INTO submissions (
                        id, guild_id, source_channel_id, source_message_id,
                        submitter_id, status, opponent_group
                    ) VALUES ('full', 1, 2, 10, 4, 'draft', 'four');
                    INSERT INTO submissions (
                        id, guild_id, source_channel_id, source_message_id,
                        submitter_id, status, opponent_group
                    ) VALUES ('partial', 1, 2, 11, 4, 'draft', 'fewer_than_four');
                    INSERT INTO submissions (
                        id, guild_id, source_channel_id, source_message_id,
                        submitter_id, status, opponent_group, rule_version,
                        base_points, multiplier, total_points, points_explanation
                    ) VALUES (
                        'approved-partial', 1, 2, 12, 4, 'approved', 'fewer_than_four',
                        'old-rule', 1, 1, 1, '["Ancien barème"]'
                    );
                    """
                )

            repository = SQLiteSubmissionRepository(database_path)
            await repository.initialize()
            full = await repository.get("full")
            partial = await repository.get("partial")
            approved_partial = await repository.get("approved-partial")

            assert full is not None
            assert partial is not None
            assert approved_partial is not None
            self.assertEqual(full.fight_balance, FightBalance.EQUAL_OR_OUTNUMBERED)
            self.assertIsNone(partial.fight_balance)
            self.assertEqual(
                approved_partial.fight_balance,
                FightBalance.OPPONENTS_OUTNUMBERED,
            )
            assert approved_partial.points is not None
            self.assertEqual(approved_partial.points.total_points, 1)
            self.assertIsNotNone(approved_partial.review_submitted_at)
            self.assertIsNotNone(approved_partial.approved_at)


if __name__ == "__main__":
    unittest.main()
