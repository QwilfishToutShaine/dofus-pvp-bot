from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SubmissionLane(StrEnum):
    NORMAL = "normal"
    SNG = "sng"

    @property
    def label(self) -> str:
        return {self.NORMAL: "Salon normal", self.SNG: "Salon SNG"}[self]


class FightBalance(StrEnum):
    EQUAL_OR_OUTNUMBERED = "equal_or_outnumbered"
    OPPONENTS_OUTNUMBERED = "opponents_outnumbered"

    @property
    def label(self) -> str:
        return {
            self.EQUAL_OR_OUTNUMBERED: "Égalité ou adversaires plus nombreux",
            self.OPPONENTS_OUTNUMBERED: "Adversaires en infériorité",
        }[self]

    @classmethod
    def from_counts(cls, allies: int, opponents: int) -> FightBalance:
        if not 1 <= allies <= 4:
            raise ValueError("Le nombre d’alliés doit être compris entre 1 et 4.")
        if not 0 <= opponents <= 4:
            raise ValueError("Le nombre d’adversaires doit être compris entre 0 et 4.")
        if allies <= opponents:
            return cls.EQUAL_OR_OUTNUMBERED
        return cls.OPPONENTS_OUTNUMBERED


class DetectionMethod(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"

    @property
    def label(self) -> str:
        return {
            self.AUTOMATIC: "Détection automatique",
            self.MANUAL: "Correction du staff",
        }[self]


class SubmissionStatus(StrEnum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class DetectionResult:
    fight_balance: FightBalance | None
    allies_count: int | None = None
    opponents_count: int | None = None
    confidence: float | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("La confiance doit être comprise entre 0 et 1.")
        if self.fight_balance is None and self.confidence is not None:
            raise ValueError("Une confiance nécessite un résultat de détection.")
        counts = (self.allies_count, self.opponents_count)
        if (counts[0] is None) != (counts[1] is None):
            raise ValueError("Les deux effectifs doivent être fournis ensemble.")
        if counts[0] is not None and counts[1] is not None:
            detected_balance = FightBalance.from_counts(counts[0], counts[1])
            if self.fight_balance is not detected_balance:
                raise ValueError("Les effectifs et la catégorie détectée sont incohérents.")


@dataclass(frozen=True, slots=True)
class PointsBreakdown:
    rule_version: str
    base_points: int
    multiplier: int
    total_points: int
    explanation: tuple[str, ...]


@dataclass(slots=True)
class Submission:
    id: str
    guild_id: int
    source_channel_id: int
    source_message_id: int
    submitter_id: int
    lane: SubmissionLane
    status: SubmissionStatus = SubmissionStatus.DRAFT
    prompt_message_id: int | None = None
    review_message_id: int | None = None
    fight_balance: FightBalance | None = None
    allies_count: int | None = None
    opponents_count: int | None = None
    detected_allies_count: int | None = None
    detected_opponents_count: int | None = None
    detection_method: DetectionMethod | None = None
    detection_confidence: float | None = None
    detection_detail: str | None = None
    participant_ids: list[int] = field(default_factory=list)
    note: str | None = None
    points: PointsBreakdown | None = None
    rejection_reason: str | None = None
    review_submitted_at: str | None = None
    approved_at: str | None = None
