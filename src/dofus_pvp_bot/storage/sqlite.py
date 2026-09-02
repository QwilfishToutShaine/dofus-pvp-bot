from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from dofus_pvp_bot.domain.models import (
    DetectionMethod,
    FightBalance,
    PointsBreakdown,
    Submission,
    SubmissionLane,
    SubmissionStatus,
)

T = TypeVar("T")


class SQLiteSubmissionRepository:
    """Dépôt SQLite asynchrone, sans connexion partagée entre les threads."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._write_lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    async def _read(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        return await asyncio.to_thread(self._run_read, operation)

    def _run_read(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        with self._connect() as connection:
            return operation(connection)

    async def _write(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        async with self._write_lock:
            return await asyncio.to_thread(self._run_write, operation)

    def _run_write(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        with self._connect() as connection:
            result = operation(connection)
            connection.commit()
            return result

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        def operation(connection: sqlite3.Connection) -> None:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS submissions (
                    id TEXT PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    source_channel_id INTEGER NOT NULL,
                    source_message_id INTEGER NOT NULL UNIQUE,
                    submitter_id INTEGER NOT NULL,
                    lane TEXT NOT NULL DEFAULT 'normal',
                    status TEXT NOT NULL,
                    prompt_message_id INTEGER,
                    review_message_id INTEGER,
                    fight_balance TEXT,
                    allies_count INTEGER,
                    opponents_count INTEGER,
                    detected_allies_count INTEGER,
                    detected_opponents_count INTEGER,
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
                    review_submitted_at TEXT,
                    approved_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS submission_participants (
                    submission_id TEXT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL,
                    PRIMARY KEY (submission_id, user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_submissions_status
                    ON submissions(status);
                CREATE INDEX IF NOT EXISTS idx_participants_user
                    ON submission_participants(user_id);

                CREATE TABLE IF NOT EXISTS leaderboard_months (
                    guild_id INTEGER NOT NULL,
                    period_key TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    approved_submission_count INTEGER NOT NULL,
                    closed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, period_key)
                );

                CREATE TABLE IF NOT EXISTS leaderboard_entries (
                    guild_id INTEGER NOT NULL,
                    period_key TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    total_points INTEGER NOT NULL,
                    submission_count INTEGER NOT NULL,
                    rank INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, period_key, user_id),
                    FOREIGN KEY (guild_id, period_key)
                        REFERENCES leaderboard_months(guild_id, period_key)
                        ON DELETE CASCADE
                );
                """
            )
            self._migrate_legacy_schema(connection)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_submissions_lane ON submissions(lane)"
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_submissions_leaderboard
                ON submissions(guild_id, review_submitted_at, status)"""
            )

        await self._write(operation)

    @staticmethod
    def _migrate_legacy_schema(connection: sqlite3.Connection) -> None:
        """Ajoute sans perte les champs introduits par le parcours simplifié."""
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(submissions)").fetchall()
        }
        additions = {
            "lane": "TEXT NOT NULL DEFAULT 'normal'",
            "fight_balance": "TEXT",
            "allies_count": "INTEGER",
            "opponents_count": "INTEGER",
            "detected_allies_count": "INTEGER",
            "detected_opponents_count": "INTEGER",
            "detection_method": "TEXT",
            "detection_confidence": "REAL",
            "detection_detail": "TEXT",
            "multiplier": "INTEGER",
            "review_submitted_at": "TEXT",
            "approved_at": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE submissions ADD COLUMN {name} {definition}")

        legacy_columns = {"allies", "enemies"}
        if legacy_columns <= columns:
            connection.execute(
                """
                UPDATE submissions
                SET fight_balance = CASE
                    WHEN allies <= enemies THEN 'equal_or_outnumbered'
                    WHEN allies > enemies THEN 'opponents_outnumbered'
                    ELSE NULL
                END,
                    allies_count = allies,
                    opponents_count = enemies,
                    detected_allies_count = allies,
                    detected_opponents_count = enemies
                WHERE fight_balance IS NULL AND allies IS NOT NULL AND enemies IS NOT NULL
                """
            )
        connection.execute(
            """
            UPDATE submissions
            SET allies_count = detected_allies_count,
                opponents_count = detected_opponents_count
            WHERE allies_count IS NULL AND opponents_count IS NULL
                AND detected_allies_count IS NOT NULL
                AND detected_opponents_count IS NOT NULL
            """
        )
        if "opponent_group" in columns:
            connection.execute(
                """
                UPDATE submissions
                SET fight_balance = 'equal_or_outnumbered'
                WHERE fight_balance IS NULL AND opponent_group = 'four'
                """
            )
            connection.execute(
                """
                UPDATE submissions
                SET fight_balance = 'opponents_outnumbered'
                WHERE fight_balance IS NULL
                    AND opponent_group IN ('fewer', 'fewer_than_four')
                    AND status != 'draft'
                """
            )
        connection.execute(
            """
            UPDATE submissions
            SET detection_method = 'manual'
            WHERE fight_balance IS NOT NULL AND detection_method IS NULL
            """
        )
        old_bonus_columns = {"bonus_guild_key", "bonus_points"}
        if old_bonus_columns <= columns:
            connection.execute(
                """
                UPDATE submissions
                SET lane = CASE WHEN bonus_guild_key = 'sng' THEN 'sng' ELSE 'normal' END,
                    multiplier = CASE WHEN bonus_guild_key = 'sng' THEN 2 ELSE 1 END
                WHERE multiplier IS NULL
                """
            )
        else:
            connection.execute(
                "UPDATE submissions SET multiplier = 1 WHERE multiplier IS NULL"
            )
        connection.execute(
            """
            UPDATE submissions
            SET review_submitted_at = updated_at
            WHERE review_submitted_at IS NULL
                AND status IN ('pending_review', 'approved', 'rejected')
            """
        )
        connection.execute(
            """
            UPDATE submissions
            SET approved_at = updated_at
            WHERE approved_at IS NULL AND status = 'approved'
            """
        )

    async def create_draft(self, submission: Submission) -> Submission:
        def operation(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                INSERT OR IGNORE INTO submissions (
                    id, guild_id, source_channel_id, source_message_id,
                    submitter_id, lane, status, fight_balance,
                    allies_count, opponents_count,
                    detected_allies_count, detected_opponents_count,
                    detection_method, detection_confidence, detection_detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    submission.id,
                    submission.guild_id,
                    submission.source_channel_id,
                    submission.source_message_id,
                    submission.submitter_id,
                    submission.lane.value,
                    submission.status.value,
                    submission.fight_balance.value if submission.fight_balance else None,
                    submission.allies_count,
                    submission.opponents_count,
                    submission.detected_allies_count,
                    submission.detected_opponents_count,
                    submission.detection_method.value if submission.detection_method else None,
                    submission.detection_confidence,
                    submission.detection_detail,
                ),
            )

        await self._write(operation)
        existing = await self.get_by_source_message_id(submission.source_message_id)
        if existing is None:
            raise RuntimeError("La création de la soumission a échoué.")
        return existing

    async def get(self, submission_id: str) -> Submission | None:
        return await self._read(
            lambda connection: self._fetch_one(connection, "id = ?", (submission_id,))
        )

    async def get_by_source_message_id(self, message_id: int) -> Submission | None:
        return await self._read(
            lambda connection: self._fetch_one(connection, "source_message_id = ?", (message_id,))
        )

    async def list_by_status(self, status: SubmissionStatus) -> list[Submission]:
        def operation(connection: sqlite3.Connection) -> list[Submission]:
            rows = connection.execute(
                "SELECT * FROM submissions WHERE status = ? ORDER BY created_at",
                (status.value,),
            ).fetchall()
            return [self._row_to_submission(connection, row) for row in rows]

        return await self._read(operation)

    async def list_leaderboard_guild_ids(self) -> list[int]:
        def operation(connection: sqlite3.Connection) -> list[int]:
            rows = connection.execute(
                """
                SELECT DISTINCT guild_id
                FROM submissions
                WHERE review_submitted_at IS NOT NULL
                ORDER BY guild_id
                """
            ).fetchall()
            return [int(row["guild_id"]) for row in rows]

        return await self._read(operation)

    async def earliest_review_submission(self, guild_id: int) -> str | None:
        def operation(connection: sqlite3.Connection) -> str | None:
            row = connection.execute(
                """
                SELECT MIN(review_submitted_at) AS earliest
                FROM submissions
                WHERE guild_id = ? AND review_submitted_at IS NOT NULL
                """,
                (guild_id,),
            ).fetchone()
            return str(row["earliest"]) if row is not None and row["earliest"] else None

        return await self._read(operation)

    async def aggregate_leaderboard(
        self,
        guild_id: int,
        start_utc: str,
        end_utc: str,
    ) -> tuple[list[tuple[int, int, int]], int]:
        def operation(
            connection: sqlite3.Connection,
        ) -> tuple[list[tuple[int, int, int]], int]:
            rows = connection.execute(
                """
                SELECT participant.user_id AS user_id,
                       SUM(submission.total_points) AS total_points,
                       COUNT(*) AS submission_count
                FROM submissions AS submission
                JOIN submission_participants AS participant
                    ON participant.submission_id = submission.id
                WHERE submission.guild_id = ?
                    AND submission.status = 'approved'
                    AND submission.total_points IS NOT NULL
                    AND submission.review_submitted_at >= ?
                    AND submission.review_submitted_at < ?
                GROUP BY participant.user_id
                ORDER BY total_points DESC, participant.user_id ASC
                """,
                (guild_id, start_utc, end_utc),
            ).fetchall()
            count_row = connection.execute(
                """
                SELECT COUNT(*) AS submission_count
                FROM submissions
                WHERE guild_id = ? AND status = 'approved' AND total_points IS NOT NULL
                    AND review_submitted_at >= ? AND review_submitted_at < ?
                """,
                (guild_id, start_utc, end_utc),
            ).fetchone()
            scores = [
                (int(row["user_id"]), int(row["total_points"]), int(row["submission_count"]))
                for row in rows
            ]
            approved_count = int(count_row["submission_count"]) if count_row else 0
            return scores, approved_count

        return await self._read(operation)

    async def count_pending_reviews(
        self,
        guild_id: int,
        start_utc: str,
        end_utc: str,
    ) -> int:
        def operation(connection: sqlite3.Connection) -> int:
            row = connection.execute(
                """
                SELECT COUNT(*) AS pending_count
                FROM submissions
                WHERE guild_id = ? AND status = 'pending_review'
                    AND review_submitted_at >= ? AND review_submitted_at < ?
                """,
                (guild_id, start_utc, end_utc),
            ).fetchone()
            return int(row["pending_count"]) if row else 0

        return await self._read(operation)

    async def load_leaderboard_snapshot(
        self,
        guild_id: int,
        period_key: str,
    ) -> tuple[list[tuple[int, int, int, int]], int, str] | None:
        def operation(
            connection: sqlite3.Connection,
        ) -> tuple[list[tuple[int, int, int, int]], int, str] | None:
            month = connection.execute(
                """
                SELECT approved_submission_count, closed_at
                FROM leaderboard_months
                WHERE guild_id = ? AND period_key = ?
                """,
                (guild_id, period_key),
            ).fetchone()
            if month is None:
                return None
            rows = connection.execute(
                """
                SELECT user_id, total_points, submission_count, rank
                FROM leaderboard_entries
                WHERE guild_id = ? AND period_key = ?
                ORDER BY rank, user_id
                """,
                (guild_id, period_key),
            ).fetchall()
            entries = [
                (
                    int(row["user_id"]),
                    int(row["total_points"]),
                    int(row["submission_count"]),
                    int(row["rank"]),
                )
                for row in rows
            ]
            return entries, int(month["approved_submission_count"]), str(month["closed_at"])

        return await self._read(operation)

    async def close_leaderboard_if_ready(
        self,
        *,
        guild_id: int,
        period_key: str,
        timezone_name: str,
        start_utc: str,
        end_utc: str,
    ) -> bool:
        def operation(connection: sqlite3.Connection) -> bool:
            existing = connection.execute(
                """
                SELECT 1 FROM leaderboard_months
                WHERE guild_id = ? AND period_key = ?
                """,
                (guild_id, period_key),
            ).fetchone()
            if existing is not None:
                return True

            pending = connection.execute(
                """
                SELECT COUNT(*) AS pending_count
                FROM submissions
                WHERE guild_id = ? AND status = 'pending_review'
                    AND review_submitted_at >= ? AND review_submitted_at < ?
                """,
                (guild_id, start_utc, end_utc),
            ).fetchone()
            if pending is not None and int(pending["pending_count"]) > 0:
                return False

            rows = connection.execute(
                """
                SELECT participant.user_id AS user_id,
                       SUM(submission.total_points) AS total_points,
                       COUNT(*) AS submission_count
                FROM submissions AS submission
                JOIN submission_participants AS participant
                    ON participant.submission_id = submission.id
                WHERE submission.guild_id = ?
                    AND submission.status = 'approved'
                    AND submission.total_points IS NOT NULL
                    AND submission.review_submitted_at >= ?
                    AND submission.review_submitted_at < ?
                GROUP BY participant.user_id
                ORDER BY total_points DESC, participant.user_id ASC
                """,
                (guild_id, start_utc, end_utc),
            ).fetchall()
            count_row = connection.execute(
                """
                SELECT COUNT(*) AS submission_count
                FROM submissions
                WHERE guild_id = ? AND status = 'approved' AND total_points IS NOT NULL
                    AND review_submitted_at >= ? AND review_submitted_at < ?
                """,
                (guild_id, start_utc, end_utc),
            ).fetchone()
            approved_submission_count = (
                int(count_row["submission_count"]) if count_row is not None else 0
            )
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO leaderboard_months (
                    guild_id, period_key, timezone, approved_submission_count
                ) VALUES (?, ?, ?, ?)
                """,
                (guild_id, period_key, timezone_name, approved_submission_count),
            )
            if cursor.rowcount != 1:
                return True

            previous_points: int | None = None
            current_rank = 0
            entries: list[tuple[int, int, int, int]] = []
            for position, row in enumerate(rows, start=1):
                points = int(row["total_points"])
                if points != previous_points:
                    current_rank = position
                    previous_points = points
                entries.append(
                    (
                        int(row["user_id"]),
                        points,
                        int(row["submission_count"]),
                        current_rank,
                    )
                )
            connection.executemany(
                """
                INSERT INTO leaderboard_entries (
                    guild_id, period_key, user_id, total_points, submission_count, rank
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (guild_id, period_key, user_id, points, count, rank)
                    for user_id, points, count, rank in entries
                ],
            )
            return True

        return await self._write(operation)

    def _fetch_one(
        self,
        connection: sqlite3.Connection,
        condition: str,
        parameters: tuple[object, ...],
    ) -> Submission | None:
        row = connection.execute(
            f"SELECT * FROM submissions WHERE {condition}", parameters
        ).fetchone()
        return self._row_to_submission(connection, row) if row is not None else None

    def _row_to_submission(self, connection: sqlite3.Connection, row: sqlite3.Row) -> Submission:
        participant_rows = connection.execute(
            "SELECT user_id FROM submission_participants WHERE submission_id = ? ORDER BY user_id",
            (row["id"],),
        ).fetchall()
        points = None
        if row["total_points"] is not None:
            points = PointsBreakdown(
                rule_version=row["rule_version"],
                base_points=row["base_points"],
                multiplier=row["multiplier"],
                total_points=row["total_points"],
                explanation=tuple(json.loads(row["points_explanation"])),
            )
        return Submission(
            id=row["id"],
            guild_id=row["guild_id"],
            source_channel_id=row["source_channel_id"],
            source_message_id=row["source_message_id"],
            submitter_id=row["submitter_id"],
            lane=SubmissionLane(row["lane"]),
            status=SubmissionStatus(row["status"]),
            prompt_message_id=row["prompt_message_id"],
            review_message_id=row["review_message_id"],
            fight_balance=(
                FightBalance(row["fight_balance"]) if row["fight_balance"] else None
            ),
            allies_count=row["allies_count"],
            opponents_count=row["opponents_count"],
            detected_allies_count=row["detected_allies_count"],
            detected_opponents_count=row["detected_opponents_count"],
            detection_method=(
                DetectionMethod(row["detection_method"]) if row["detection_method"] else None
            ),
            detection_confidence=row["detection_confidence"],
            detection_detail=row["detection_detail"],
            participant_ids=[participant["user_id"] for participant in participant_rows],
            note=row["note"],
            points=points,
            rejection_reason=row["rejection_reason"],
            review_submitted_at=row["review_submitted_at"],
            approved_at=row["approved_at"],
        )

    async def save(self, submission: Submission) -> None:
        def operation(connection: sqlite3.Connection) -> None:
            points = submission.points
            cursor = connection.execute(
                """
                UPDATE submissions SET
                    lane = ?, status = ?, prompt_message_id = ?, review_message_id = ?,
                    fight_balance = ?, allies_count = ?, opponents_count = ?,
                    detected_allies_count = ?, detected_opponents_count = ?, detection_method = ?,
                    detection_confidence = ?, detection_detail = ?, note = ?,
                    rule_version = ?, base_points = ?,
                    multiplier = ?, total_points = ?, points_explanation = ?,
                    rejection_reason = ?, review_submitted_at = ?, approved_at = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    submission.lane.value,
                    submission.status.value,
                    submission.prompt_message_id,
                    submission.review_message_id,
                    submission.fight_balance.value if submission.fight_balance else None,
                    submission.allies_count,
                    submission.opponents_count,
                    submission.detected_allies_count,
                    submission.detected_opponents_count,
                    submission.detection_method.value if submission.detection_method else None,
                    submission.detection_confidence,
                    submission.detection_detail,
                    submission.note,
                    points.rule_version if points else None,
                    points.base_points if points else None,
                    points.multiplier if points else None,
                    points.total_points if points else None,
                    json.dumps(points.explanation, ensure_ascii=False) if points else None,
                    submission.rejection_reason,
                    submission.review_submitted_at,
                    submission.approved_at,
                    submission.id,
                ),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"Soumission inconnue : {submission.id}")
            connection.execute(
                "DELETE FROM submission_participants WHERE submission_id = ?",
                (submission.id,),
            )
            connection.executemany(
                "INSERT INTO submission_participants (submission_id, user_id) VALUES (?, ?)",
                [(submission.id, user_id) for user_id in submission.participant_ids],
            )

        await self._write(operation)
