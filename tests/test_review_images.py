from __future__ import annotations

import unittest
from io import BytesIO

from PIL import Image

from dofus_pvp_bot.analysis.team_balance import ImageEvidence
from dofus_pvp_bot.discord_app.review_images import prepare_review_images


def image_bytes(
    image_format: str,
    *,
    size: tuple[int, int] = (800, 800),
    colour: str = "green",
) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, colour).save(output, format=image_format)
    return output.getvalue()


class PrepareReviewImagesTest(unittest.TestCase):
    def test_preserves_originals_when_combined_size_is_below_budget(self) -> None:
        content = image_bytes("PNG", size=(20, 20))
        images = [ImageEvidence("capture.png", "image/png", content)]

        prepared = prepare_review_images(images, max_total_bytes=len(content))

        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0].filename, "capture.png")
        self.assertEqual(prepared[0].content, content)

    def test_compresses_large_source_copies_below_total_budget(self) -> None:
        images = [
            ImageEvidence("capture-1.bmp", "image/bmp", image_bytes("BMP", colour="red")),
            ImageEvidence("capture-2.bmp", "image/bmp", image_bytes("BMP", colour="blue")),
        ]

        prepared = prepare_review_images(images, max_total_bytes=300_000)

        self.assertEqual(len(prepared), 2)
        self.assertLessEqual(sum(len(image.content) for image in prepared), 300_000)
        self.assertTrue(all(image.filename.endswith(".jpg") for image in prepared))
        for image in prepared:
            with Image.open(BytesIO(image.content)) as decoded:
                self.assertEqual(decoded.format, "JPEG")

    def test_rejects_invalid_image_when_compression_is_required(self) -> None:
        images = [ImageEvidence("capture.png", "image/png", b"not-an-image")]

        with self.assertRaisesRegex(ValueError, "compressible"):
            prepare_review_images(images, max_total_bytes=2)


if __name__ == "__main__":
    unittest.main()
