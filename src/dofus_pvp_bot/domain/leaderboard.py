from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from dofus_pvp_bot.domain.models import CombatType

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
class FortnightPeriod:
    year: int
    month: int
    half: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise ValueError("Le mois doit être compris entre 1 et 12.")
        if not 2000 <= self.year <= 9999:
            raise ValueError("L’année doit être comprise entre 2000 et 9999.")
        if self.half not in (1, 2):
            raise ValueError("La quinzaine doit être 1 ou 2.")

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}-{self.half}"

    @property
    def label(self) -> str:
        days = "1er–15" if self.half == 1 else "16–fin"
        return f"{days} {MONTH_NAMES[self.month - 1]} {self.year}"

    @classmethod
    def parse(cls, value: str) -> FortnightPeriod:
        try:
            year_text, month_text, half_text = value.strip().split("-")
            if len(year_text) != 4 or len(month_text) != 2 or half_text not in ("1", "2"):
                raise ValueError("Format incorrect")
            return cls(int(year_text), int(month_text), int(half_text))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "La période doit utiliser le format AAAA-MM-1 ou AAAA-MM-2, par exemple 2026-09-1."
            ) from exc

    @classmethod
    def current(cls, timezone: ZoneInfo, now: datetime | None = None) -> FortnightPeriod:
        instant = now or datetime.now(UTC)
        if instant.tzinfo is None:
            raise ValueError("La date courante doit inclure un fuseau horaire.")
        local = instant.astimezone(timezone)
        return cls(local.year, local.month, 1 if local.day <= 15 else 2)

    def next(self) -> FortnightPeriod:
        if self.half == 1:
            return FortnightPeriod(self.year, self.month, 2)
        if self.month == 12:
            return FortnightPeriod(self.year + 1, 1, 1)
        return FortnightPeriod(self.year, self.month + 1, 1)

    def previous(self) -> FortnightPeriod:
        if self.half == 2:
            return FortnightPeriod(self.year, self.month, 1)
        if self.month == 1:
            return FortnightPeriod(self.year - 1, 12, 2)
        return FortnightPeriod(self.year, self.month - 1, 2)

    def utc_bounds(self, timezone: ZoneInfo) -> tuple[str, str]:
        start_local = datetime(self.year, self.month, 1 if self.half == 1 else 16, tzinfo=timezone)
        following = self.next()
        end_local = datetime(
            following.year, following.month, 1 if following.half == 1 else 16, tzinfo=timezone
        )
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
class FortnightLeaderboard:
    period: FortnightPeriod
    entries: tuple[LeaderboardEntry, ...]
    approved_submission_count: int
    pending_submission_count: int
    finalized: bool
    closed_at: str | None = None
    combat_type: CombatType | None = None


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
