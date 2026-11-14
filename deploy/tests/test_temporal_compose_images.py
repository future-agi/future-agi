"""Temporal server images in the compose files: one digest-pinned release."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = (
    "docker-compose.distributed.yml",
    "futureagi/docker-compose.yml",
    "futureagi/docker-compose.peerdb.yml",
)
IMAGE = re.compile(r"^\s*image:\s*(temporalio/auto-setup\S*)\s*$", re.MULTILINE)
PINNED = re.compile(
    r"^temporalio/auto-setup:(\d+)\.(\d+)\.(\d+)@sha256:[0-9a-f]{64}$"
)
MINIMUM = (1, 29, 7)


def temporal_images() -> dict[str, list[str]]:
    return {
        name: IMAGE.findall((ROOT / name).read_text(encoding="utf-8"))
        for name in COMPOSE_FILES
    }


class TemporalComposeImageTest(unittest.TestCase):
    def test_every_compose_file_runs_a_temporal_server(self) -> None:
        for name, images in temporal_images().items():
            with self.subTest(file=name):
                self.assertTrue(images, f"{name} has no temporalio/auto-setup image")

    def test_temporal_images_use_a_pinned_patch_release(self) -> None:
        for name, images in temporal_images().items():
            for image in images:
                with self.subTest(file=name, image=image):
                    match = PINNED.match(image)
                    self.assertIsNotNone(
                        match, f"{image} must name a full x.y.z tag and a sha256 digest"
                    )
                    version = tuple(int(part) for part in match.groups())
                    self.assertGreaterEqual(version, MINIMUM)

    def test_app_and_peerdb_temporal_share_one_image(self) -> None:
        images = {image for found in temporal_images().values() for image in found}
        self.assertEqual(len(images), 1, sorted(images))


if __name__ == "__main__":
    unittest.main()
