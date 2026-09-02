from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from dofus_pvp_bot.domain.models import (
    DetectionMethod,
    DetectionResult,
    FightBalance,
    PointsBreakdown,
    Submission,
    SubmissionLane,
    SubmissionStatus,
)
from dofus_pvp_bot.domain.scoring import ScoringEngine
from dofus_pvp_bot.storage.sqlite import SQLiteSubmissionRepository


class SubmissionError(ValueError):
    pass


class SubmissionService:
    def __init__(
        self,
        repository: SQLiteSubmissionRepository,
        scoring: ScoringEngine,
    ) -> None:
        self.repository = repository
        self.scoring = scoring

    async def create_draft(
        self,
        *,
        guild_id: int,
        channel_id: int,
        message_id: int,
        submitter_id: int,
        lane: SubmissionLane,
        detection: DetectionResult | None = None,
    ) -> Submission:
        draft = Submission(
            id=uuid4().hex,
            guild_id=guild_id,
            source_channel_id=channel_id,
            source_message_id=message_id,
            submitter_id=submitter_id,
            lane=lane,
        )
        if detection is not None:
            draft.fight_balance = detection.fight_balance
            draft.allies_count = detection.allies_count
            draft.opponents_count = detection.opponents_count
            draft.detected_allies_count = detection.allies_count
            draft.detected_opponents_count = detection.opponents_count
            draft.detection_confidence = detection.confidence
            draft.detection_detail = detection.detail
            if detection.fight_balance is not None:
                draft.detection_method = DetectionMethod.AUTOMATIC
        return await self.repository.create_draft(draft)

    async def require(self, submission_id: str) -> Submission:
        submission = await self.repository.get(submission_id)
        if submission is None:
            raise SubmissionError("Soumission introuvable.")
        return submission

    @staticmethod
    def _ensure_draft(submission: Submission) -> None:
        if submission.status is not SubmissionStatus.DRAFT:
            raise SubmissionError("Cette soumission n’est plus modifiable.")

    async def set_prompt_message(self, submission_id: str, message_id: int) -> None:
        submission = await self.require(submission_id)
        submission.prompt_message_id = message_id
        await self.repository.save(submission)

    async def set_review_counts(
        self,
        submission_id: str,
        allies_count: int,
        opponents_count: int,
    ) -> Submission:
        submission = await self.require(submission_id)
        if submission.status is not SubmissionStatus.PENDING_REVIEW:
            raise SubmissionError("Seule une soumission en attente peut être corrigée.")
        try:
            fight_balance = FightBalance.from_counts(allies_count, opponents_count)
        except ValueError as exc:
            raise SubmissionError(str(exc)) from exc
        submission.allies_count = allies_count
        submission.opponents_count = opponents_count
        submission.fight_balance = fight_balance
        previous_detail = submission.detection_detail
        was_manually_corrected = submission.detection_method is DetectionMethod.MANUAL
        submission.detection_method = DetectionMethod.MANUAL
        submission.detection_confidence = None
        submission.detection_detail = "Effectifs vérifiés ou corrigés par le staff."
        if previous_detail and not was_manually_corrected:
            submission.detection_detail += f" Analyse OCR initiale : {previous_detail}"
        submission.points = self.calculate(submission)
        await self.repository.save(submission)
        return submission

    async def set_participants(self, submission_id: str, participant_ids: list[int]) -> Submission:
        submission = await self.require(submission_id)
        self._ensure_draft(submission)
        unique_ids = list(dict.fromkeys(participant_ids))
        if not 1 <= len(unique_ids) <= 4:
            raise SubmissionError("Sélectionne entre 1 et 4 participant(s).")
        submission.participant_ids = unique_ids
        await self.repository.save(submission)
        return submission

    async def set_note(self, submission_id: str, note: str | None) -> Submission:
        submission = await self.require(submission_id)
        self._ensure_draft(submission)
        cleaned = note.strip() if note else None
        if cleaned and len(cleaned) > 500:
            raise SubmissionError("La note ne peut pas dépasser 500 caractères.")
        submission.note = cleaned or None
        await self.repository.save(submission)
        return submission

    def calculate(self, submission: Submission) -> PointsBreakdown:
        if submission.fight_balance is None:
            raise SubmissionError("La comparaison des effectifs doit être déterminée.")
        if not submission.participant_ids:
            raise SubmissionError("Sélectionne au moins un participant Discord.")
        return self.scoring.calculate(submission.fight_balance, submission.lane)

    async def mark_pending(
        self,
        submission_id: str,
        review_message_id: int,
        points: PointsBreakdown | None,
    ) -> Submission:
        submission = await self.require(submission_id)
        self._ensure_draft(submission)
        submission.points = points
        submission.review_message_id = review_message_id
        submission.review_submitted_at = _utc_now()
        submission.status = SubmissionStatus.PENDING_REVIEW
        await self.repository.save(submission)
        return submission

    async def approve(self, submission_id: str) -> Submission:
        submission = await self.require(submission_id)
        if submission.status is not SubmissionStatus.PENDING_REVIEW:
            raise SubmissionError("Seule une soumission en attente peut être validée.")
        if submission.fight_balance is None or submission.points is None:
            raise SubmissionError("Le staff doit d’abord renseigner les effectifs du combat.")
        submission.status = SubmissionStatus.APPROVED
        submission.approved_at = _utc_now()
        await self.repository.save(submission)
        return submission

    async def reject(self, submission_id: str, reason: str | None = None) -> Submission:
        submission = await self.require(submission_id)
        if submission.status is not SubmissionStatus.PENDING_REVIEW:
            raise SubmissionError("Seule une soumission en attente peut être refusée.")
        submission.status = SubmissionStatus.REJECTED
        submission.rejection_reason = reason.strip() if reason else None
        await self.repository.save(submission)
        return submission

    async def cancel(self, submission_id: str) -> Submission:
        submission = await self.require(submission_id)
        self._ensure_draft(submission)
        submission.status = SubmissionStatus.CANCELLED
        await self.repository.save(submission)
        return submission


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
