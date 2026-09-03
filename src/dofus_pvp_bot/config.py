from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigurationError(RuntimeError):
    pass


def _required_int(name: str) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        raise ConfigurationError(f"La variable {name} est obligatoire.")
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"La variable {name} doit être un entier.") from exc


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else None


def _int_set(name: str) -> frozenset[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return frozenset()
    try:
        return frozenset(int(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError as exc:
        message = f"La variable {name} doit contenir des entiers séparés par des virgules."
        raise ConfigurationError(message) from exc


@dataclass(frozen=True, slots=True)
class BotSettings:
    token: str
    normal_submission_channel_id: int
    sng_submission_channel_id: int
    review_channel_id: int
    member_role_id: int | None
    reviewer_role_ids: frozenset[int]
    database_path: Path
    scoring_config_path: Path
    max_screenshots: int
    max_image_bytes: int
    max_review_upload_bytes: int
    max_image_pixels: int
    draft_timeout_seconds: int
    leaderboard_timezone: ZoneInfo
    log_level: str

    @classmethod
    def from_env(cls) -> BotSettings:
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise ConfigurationError("La variable DISCORD_TOKEN est obligatoire.")
        timezone_name = os.getenv("LEADERBOARD_TIMEZONE", "Europe/Paris").strip()
        try:
            leaderboard_timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigurationError(
                f"Le fuseau LEADERBOARD_TIMEZONE est inconnu : {timezone_name}."
            ) from exc
        settings = cls(
            token=token,
            normal_submission_channel_id=_required_int("NORMAL_SUBMISSION_CHANNEL_ID"),
            sng_submission_channel_id=_required_int("SNG_SUBMISSION_CHANNEL_ID"),
            review_channel_id=_required_int("REVIEW_CHANNEL_ID"),
            member_role_id=_optional_int("MEMBER_ROLE_ID"),
            reviewer_role_ids=_int_set("REVIEWER_ROLE_IDS"),
            database_path=Path(os.getenv("DATABASE_PATH", "data/dofus_pvp.sqlite3")),
            scoring_config_path=Path(os.getenv("SCORING_CONFIG_PATH", "config/scoring.json")),
            max_screenshots=int(os.getenv("MAX_SCREENSHOTS", "4")),
            max_image_bytes=int(os.getenv("MAX_IMAGE_BYTES", str(10 * 1024 * 1024))),
            max_review_upload_bytes=int(
                os.getenv("MAX_REVIEW_UPLOAD_BYTES", str(7 * 1024 * 1024))
            ),
            max_image_pixels=int(os.getenv("MAX_IMAGE_PIXELS", "20000000")),
            draft_timeout_seconds=int(os.getenv("DRAFT_TIMEOUT_SECONDS", "600")),
            leaderboard_timezone=leaderboard_timezone,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
        if settings.max_screenshots < 1:
            raise ConfigurationError("MAX_SCREENSHOTS doit être supérieur ou égal à 1.")
        if settings.max_image_bytes < 1:
            raise ConfigurationError("MAX_IMAGE_BYTES doit être supérieur ou égal à 1.")
        if settings.max_review_upload_bytes < 1:
            raise ConfigurationError(
                "MAX_REVIEW_UPLOAD_BYTES doit être supérieur ou égal à 1."
            )
        if settings.max_image_pixels < 1:
            raise ConfigurationError("MAX_IMAGE_PIXELS doit être supérieur ou égal à 1.")
        if settings.draft_timeout_seconds < 60:
            raise ConfigurationError("DRAFT_TIMEOUT_SECONDS doit être supérieur ou égal à 60.")
        channel_ids = {
            settings.normal_submission_channel_id,
            settings.sng_submission_channel_id,
            settings.review_channel_id,
        }
        if len(channel_ids) != 3:
            raise ConfigurationError(
                "Les salons normal, SNG et de vérification doivent être distincts."
            )
        return settings
