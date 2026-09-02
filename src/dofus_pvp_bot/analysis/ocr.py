from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any, Protocol

from PIL import Image


@dataclass(frozen=True, slots=True)
class OcrToken:
    text: str
    confidence: float
    left: float
    top: float
    right: float
    bottom: float

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top


class OcrBackend(Protocol):
    def recognize(self, image: bytes) -> list[OcrToken]: ...


class RapidOcrBackend:
    """Adaptateur minimal autour de RapidOCR, chargé au premier besoin."""

    def __init__(self, *, max_image_pixels: int = 20_000_000) -> None:
        if max_image_pixels < 1:
            raise ValueError("La limite de pixels doit être positive.")
        self._engine: Any | None = None
        self.max_image_pixels = max_image_pixels

    def recognize(self, image: bytes) -> list[OcrToken]:
        if not image:
            raise ValueError("La capture est vide.")
        try:
            with Image.open(BytesIO(image)) as decoded:
                width, height = decoded.size
                if width * height > self.max_image_pixels:
                    raise ValueError(
                        f"La capture dépasse {self.max_image_pixels:,} pixels."
                    )
                decoded.verify()
        except OSError as exc:
            raise ValueError("Le fichier joint n’est pas une image valide.") from exc
        if self._engine is None:
            from rapidocr import RapidOCR

            self._engine = RapidOCR()

        result: Any = self._engine(image)
        boxes = result.boxes
        texts = result.txts
        scores = result.scores
        if boxes is None or texts is None or scores is None:
            return []

        tokens: list[OcrToken] = []
        for box, text, score in zip(boxes, texts, scores, strict=True):
            points = box.tolist()
            x_values = [float(point[0]) for point in points]
            y_values = [float(point[1]) for point in points]
            tokens.append(
                OcrToken(
                    text=str(text).strip(),
                    confidence=float(score),
                    left=min(x_values),
                    top=min(y_values),
                    right=max(x_values),
                    bottom=max(y_values),
                )
            )
        return tokens
