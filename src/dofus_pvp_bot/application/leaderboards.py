from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from dofus_pvp_bot.domain.leaderboard import (
    FortnightLeaderboard,
    FortnightPeriod,
    LeaderboardEntry,
    assign_competition_ranks,
)
from dofus_pvp_bot.domain.models import CombatType
from dofus_pvp_bot.storage.sqlite import SQLiteSubmissionRepository


class LeaderboardError(ValueError):
    pass


class LeaderboardService:
    def __init__(
        self,
        repository: SQLiteSubmissionRepository,
        timezone: ZoneInfo,
    ) -> None:
        self.repository = repository
        self.timezone = timezone

    async def get(
        self,
        guild_id: int,
        period: FortnightPeriod,
        *,
        now: datetime | None = None,
        combat_type: CombatType | None = None,
    ) -> FortnightLeaderboard:
        current = FortnightPeriod.current(self.timezone, now)
        if period > current:
            raise LeaderboardError("Le classement d’une période future n’existe pas.")

        snapshot = await self.repository.load_leaderboard_snapshot(
            guild_id, period.key, combat_type
        )
        if snapshot is not None:
            stored_entries, approved_count, closed_at = snapshot
            entries = tuple(
                LeaderboardEntry(
                    user_id=user_id,
                    total_points=points,
                    submission_count=count,
                    rank=rank,
                )
                for user_id, points, count, rank in stored_entries
            )
            return FortnightLeaderboard(
                period=period,
                entries=entries,
                approved_submission_count=approved_count,
                pending_submission_count=0,
                finalized=True,
                closed_at=closed_at,
                combat_type=combat_type,
            )

        start_utc, end_utc = period.utc_bounds(self.timezone)
        scores, approved_count = await self.repository.aggregate_leaderboard(
            guild_id, start_utc, end_utc, combat_type
        )
        pending_count = await self.repository.count_pending_reviews(
            guild_id, start_utc, end_utc, combat_type
        )
        entries = assign_competition_ranks(scores)

        if period < current and pending_count == 0:
            await self.repository.close_leaderboard_if_ready(
                guild_id=guild_id,
                period_key=period.key,
                timezone_name=self.timezone.key,
                start_utc=start_utc,
                end_utc=end_utc,
                combat_type=combat_type,
            )
            stored = await self.repository.load_leaderboard_snapshot(
                guild_id, period.key, combat_type
            )
            if stored is not None:
                stored_entries, stored_count, closed_at = stored
                return FortnightLeaderboard(
                    period=period,
                    entries=tuple(
                        LeaderboardEntry(
                            user_id=user_id,
                            total_points=points,
                            submission_count=count,
                            rank=rank,
                        )
                        for user_id, points, count, rank in stored_entries
                    ),
                    approved_submission_count=stored_count,
                    pending_submission_count=0,
                    finalized=True,
                    closed_at=closed_at,
                    combat_type=combat_type,
                )

        return FortnightLeaderboard(
            period=period,
            entries=entries,
            approved_submission_count=approved_count,
            pending_submission_count=pending_count,
            finalized=False,
            combat_type=combat_type,
        )

    async def close_due_periods(
        self,
        guild_id: int,
        *,
        now: datetime | None = None,
    ) -> list[FortnightLeaderboard]:
        instant = now or datetime.now(UTC)
        current = FortnightPeriod.current(self.timezone, instant)
        earliest = await self.repository.earliest_review_submission(guild_id)
        if earliest is None:
            return []
        earliest_instant = _parse_utc_timestamp(earliest)
        period = FortnightPeriod.current(self.timezone, earliest_instant)
        results: list[FortnightLeaderboard] = []
        while period < current:
            for combat_type in (None, CombatType.ATTACK, CombatType.DEFENSE):
                results.append(
                    await self.get(guild_id, period, now=instant, combat_type=combat_type)
                )
            period = period.next()
        return results


def _parse_utc_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
