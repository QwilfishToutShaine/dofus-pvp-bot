from __future__ import annotations

import unittest
from io import BytesIO

from PIL import Image

from dofus_pvp_bot.analysis.ocr import RapidOcrBackend


def png_bytes(width: int, height: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "black").save(output, format="PNG")
    return output.getvalue()


class RapidOcrBackendValidationTest(unittest.TestCase):
    def test_rejects_empty_input_before_loading_the_model(self) -> None:
        backend = RapidOcrBackend()
        with self.assertRaisesRegex(ValueError, "vide"):
            backend.recognize(b"")

    def test_rejects_invalid_image_before_loading_the_model(self) -> None:
        backend = RapidOcrBackend()
        with self.assertRaisesRegex(ValueError, "image valide"):
            backend.recognize(b"not-an-image")

    def test_rejects_images_over_pixel_limit_before_loading_the_model(self) -> None:
        backend = RapidOcrBackend(max_image_pixels=99)
        with self.assertRaisesRegex(ValueError, "99 pixels"):
            backend.recognize(png_bytes(10, 10))


if __name__ == "__main__":
    unittest.main()
