from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

from dofus_pvp_bot.analysis.team_balance import ImageEvidence

MAX_REVIEW_IMAGE_SIDE = 2560
MIN_REVIEW_IMAGE_SIDE = 640


@dataclass(frozen=True, slots=True)
class PreparedReviewImage:
    filename: str
    content: bytes


def prepare_review_images(
    images: Sequence[ImageEvidence],
    *,
    max_total_bytes: int,
) -> list[PreparedReviewImage]:
    """Create staff-review copies that fit inside one Discord request."""

    if not images:
        return []
    if max_total_bytes < len(images):
        raise ValueError("La limite totale est trop petite pour le nombre de captures.")

    if sum(len(image.content) for image in images) <= max_total_bytes:
        return [
            PreparedReviewImage(filename=image.filename, content=image.content) for image in images
        ]

    per_image_budget = max_total_bytes // len(images)
    return [
        _compress_as_jpeg(image, index=index, max_bytes=per_image_budget)
        for index, image in enumerate(images, start=1)
    ]


def _compress_as_jpeg(
    evidence: ImageEvidence,
    *,
    index: int,
    max_bytes: int,
) -> PreparedReviewImage:
    try:
        with Image.open(BytesIO(evidence.content)) as decoded:
            decoded.seek(0)
            oriented = ImageOps.exif_transpose(decoded)
            oriented.load()
            working = _to_rgb(oriented)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{evidence.filename} n'est pas une image compressible.") from exc

    if max(working.size) > MAX_REVIEW_IMAGE_SIDE:
        working.thumbnail(
            (MAX_REVIEW_IMAGE_SIDE, MAX_REVIEW_IMAGE_SIDE),
            Image.Resampling.LANCZOS,
        )

    for quality in (88, 78, 68, 58):
        encoded = _encode_jpeg(working, quality)
        if len(encoded) <= max_bytes:
            return PreparedReviewImage(
                filename=_review_filename(evidence.filename, index),
                content=encoded,
            )

    while min(working.size) > MIN_REVIEW_IMAGE_SIDE:
        width, height = working.size
        new_size = (
            max(MIN_REVIEW_IMAGE_SIDE, int(width * 0.8)),
            max(MIN_REVIEW_IMAGE_SIDE, int(height * 0.8)),
        )
        working = working.resize(new_size, Image.Resampling.LANCZOS)
        encoded = _encode_jpeg(working, 68)
        if len(encoded) <= max_bytes:
            return PreparedReviewImage(
                filename=_review_filename(evidence.filename, index),
                content=encoded,
            )

    raise ValueError(f"Impossible de réduire {evidence.filename} sous la limite Discord.")


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode == "RGB":
        return image.copy()
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def _encode_jpeg(image: Image.Image, quality: int) -> bytes:
    output = BytesIO()
    image.save(output, format="JPEG", quality=quality, optimize=True)
    return output.getvalue()


def _review_filename(original: str, index: int) -> str:
    stem = Path(original).stem.strip() or "capture"
    return f"{index}-{stem}.jpg"
