from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

MONTH_NAMES = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


@dataclass(frozen=True, slots=True, order=True)
class MonthPeriod:
    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise ValueError("Le mois doit être compris entre 1 et 12.")
        if not 2000 <= self.year <= 9999:
            raise ValueError("L’année doit être comprise entre 2000 et 9999.")

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def label(self) -> str:
        return f"{MONTH_NAMES[self.month - 1].capitalize()} {self.year}"

    @classmethod
    def parse(cls, value: str) -> MonthPeriod:
        try:
            year_text, month_text = value.strip().split("-", maxsplit=1)
            return cls(int(year_text), int(month_text))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Le mois doit utiliser le format AAAA-MM, par exemple 2026-09."
            ) from exc

    @classmethod
    def current(cls, timezone: ZoneInfo, now: datetime | None = None) -> MonthPeriod:
        instant = now or datetime.now(UTC)
        if instant.tzinfo is None:
            raise ValueError("La date courante doit inclure un fuseau horaire.")
        local = instant.astimezone(timezone)
        return cls(local.year, local.month)

    def next(self) -> MonthPeriod:
        if self.month == 12:
            return MonthPeriod(self.year + 1, 1)
        return MonthPeriod(self.year, self.month + 1)

    def previous(self) -> MonthPeriod:
        if self.month == 1:
            return MonthPeriod(self.year - 1, 12)
        return MonthPeriod(self.year, self.month - 1)

    def utc_bounds(self, timezone: ZoneInfo) -> tuple[str, str]:
        start_local = datetime(self.year, self.month, 1, tzinfo=timezone)
        following = self.next()
        end_local = datetime(following.year, following.month, 1, tzinfo=timezone)
        return _sqlite_timestamp(start_local.astimezone(UTC)), _sqlite_timestamp(
            end_local.astimezone(UTC)
        )


@dataclass(frozen=True, slots=True)
class LeaderboardEntry:
    user_id: int
    total_points: int
    submission_count: int
    rank: int


@dataclass(frozen=True, slots=True)
class MonthlyLeaderboard:
    period: MonthPeriod
    entries: tuple[LeaderboardEntry, ...]
    approved_submission_count: int
    pending_submission_count: int
    finalized: bool
    closed_at: str | None = None


def assign_competition_ranks(
    scores: list[tuple[int, int, int]],
) -> tuple[LeaderboardEntry, ...]:
    """Classe par points décroissants avec des rangs 1, 1, 3."""
    ordered = sorted(scores, key=lambda item: (-item[1], item[0]))
    entries: list[LeaderboardEntry] = []
    previous_points: int | None = None
    current_rank = 0
    for position, (user_id, total_points, submission_count) in enumerate(ordered, start=1):
        if total_points != previous_points:
            current_rank = position
            previous_points = total_points
        entries.append(
            LeaderboardEntry(
                user_id=user_id,
                total_points=total_points,
                submission_count=submission_count,
                rank=current_rank,
            )
        )
    return tuple(entries)


def _sqlite_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
