"""Image size budget: scripts/image_size_budget.py against a fake registry,
and the budget file against the compose files and the release workflow."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "image_size_budget.py"
BUDGETS = ROOT / "scripts" / "image_size_budget.json"

_spec = importlib.util.spec_from_file_location("image_size_budget", SCRIPT)
budget = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(budget)

MB = 1_000_000


def manifest(arch: str = "", **layer_mb: float) -> dict:
    """A single-architecture image manifest; layer name -> size in MB."""
    return {
        "layers": [
            {"digest": f"sha256:{name}", "size": int(size * MB)}
            for name, size in layer_mb.items()
        ],
        "_arch": arch,
    }


def index(repo: str, **arch_digests: str) -> tuple[dict, dict]:
    """An image index and the per-arch references layers() resolves."""
    doc = {
        "manifests": [
            {"digest": digest, "platform": {"os": "linux", "architecture": arch}}
            for arch, digest in arch_digests.items()
        ]
        # Attestation manifests carry an unknown platform and must be skipped.
        + [
            {
                "digest": "sha256:att",
                "platform": {"os": "unknown", "architecture": "unknown"},
            }
        ]
    }
    return doc, {arch: f"docker.io/{repo}@{d}" for arch, d in arch_digests.items()}


class FakeRegistry:
    def __init__(self, images: dict[str, dict]):
        self.images = images

    def manifest(self, ref: str) -> dict:
        if ref not in self.images:
            raise LookupError(f"{ref}: manifest unknown")
        return self.images[ref]

    def architecture(self, ref: str, doc: dict) -> str:
        return doc["_arch"]


CONFIG = {
    "arches": ["amd64", "arm64"],
    "images": [
        {"image": "futureagi/platform", "budget_mb": 100},
        {"image": "futureagi/serving", "budget_mb": {"amd64": 50, "arm64": 40}},
        {
            "image": "futureagi/serving",
            "tag_suffix": "-gpu",
            "arches": ["amd64"],
            "budget_mb": 500,
        },
        {"image": "futureagi/runner", "budget_mb": 10, "enforce": False},
    ],
    "default_install": {
        "app": "futureagi/platform",
        "with": ["postgres:16"],
        "budget_mb": 150,
    },
}


class ScriptTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.budgets = Path(directory.name) / "budgets.json"
        self.budgets.write_text(json.dumps(CONFIG))
        environ = mock.patch.dict(os.environ, {}, clear=False)
        environ.start()
        self.addCleanup(environ.stop)
        for key in (
            "GITHUB_ACTIONS",
            "GITHUB_STEP_SUMMARY",
            "PR_NUMBER",
            "GH_TOKEN",
            "WAIVER_LABEL",
        ):
            os.environ.pop(key, None)

        postgres, postgres_arch = index(
            "library/postgres", amd64="sha256:pg-a", arm64="sha256:pg-b"
        )
        self.registry = FakeRegistry(
            {
                # Pushed by digest for one architecture (a build leg).
                "localhost:5000/futureagi/platform:ci": manifest(base=30, app_v2=60),
                "localhost:5000/futureagi/serving:ci": manifest(torch=45),
                "localhost:5000/futureagi/runner:ci": manifest(sdk=25),
                # Published: an older single-arch tag and a multi-arch index.
                "docker.io/futureagi/platform:latest": manifest(
                    "amd64", base=30, app=50
                ),
                "postgres:16": postgres,
                postgres_arch["amd64"]: manifest(base=30, pg=40),
                postgres_arch["arm64"]: manifest(base=30, pg=39),
            }
        )

    def run_script(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = budget.main(
                ["--budgets", str(self.budgets), *argv], registry=self.registry
            )
        return code, out.getvalue()

    def check(self, image: str, arch: str = "amd64", *extra: str) -> tuple[int, str]:
        return self.run_script(
            "check",
            "--image",
            image,
            "--arch",
            arch,
            "--ref",
            f"localhost:5000/{image}:ci",
            *extra,
        )

    def test_parse_ref(self):
        cases = {
            "postgres:16": ("docker.io", "library/postgres", "16"),
            "futureagi/platform": ("docker.io", "futureagi/platform", "latest"),
            "docker.io/futureagi/platform@sha256:ab": (
                "docker.io",
                "futureagi/platform",
                "sha256:ab",
            ),
            "futureagi/future-agi:v1.42.0@sha256:ab": (
                "docker.io",
                "futureagi/future-agi",
                "sha256:ab",
            ),
            "localhost:5000/futureagi/platform:ci": (
                "localhost:5000",
                "futureagi/platform",
                "ci",
            ),
            "ghcr.io/future-agi/future-agi/build-cache:backend-amd64": (
                "ghcr.io",
                "future-agi/future-agi/build-cache",
                "backend-amd64",
            ),
        }
        for ref, expected in cases.items():
            with self.subTest(ref=ref):
                self.assertEqual(budget.parse_ref(ref), expected)
        self.assertEqual(budget.repository("docker.io/library/postgres:16"), "postgres")

    def test_index_resolves_the_architecture_and_single_arch_tags_are_verified(self):
        self.assertEqual(
            budget.mb(budget.layers("postgres:16", "arm64", self.registry)), 69
        )
        with self.assertRaisesRegex(LookupError, "linux/amd64 image only"):
            budget.layers("docker.io/futureagi/platform:latest", "arm64", self.registry)
        # A build leg's own push is trusted to be the architecture it built.
        self.assertEqual(
            budget.mb(
                budget.layers(
                    "localhost:5000/futureagi/serving:ci",
                    "arm64",
                    self.registry,
                    built_for_arch=True,
                )
            ),
            45,
        )

    def test_within_budget_passes_and_reports_the_upgrade_delta(self):
        code, out = self.check(
            "futureagi/platform",
            "amd64",
            "--baseline",
            "docker.io/futureagi/platform:latest",
            "--max-growth-percent",
            "20",
        )
        self.assertEqual(code, 0, out)
        # 90 MB vs 80 MB: +12.5%; only the changed 60 MB `app` layer is new.
        self.assertIn("+12.5%", out)
        self.assertIn("downloads 60.0 MB", out)

    def test_over_budget_fails_per_architecture(self):
        self.assertEqual(self.check("futureagi/serving", "amd64")[0], 0)
        code, out = self.check("futureagi/serving", "arm64")
        self.assertEqual(code, 1)
        self.assertIn("over its 40 MB budget", out)

    def test_growth_limit_fails_unless_waived(self):
        args = (
            "--baseline",
            "docker.io/futureagi/platform:latest",
            "--max-growth-percent",
            "10",
        )
        code, out = self.check("futureagi/platform", "amd64", *args)
        self.assertEqual(code, 1)
        self.assertIn("grew +12.5%", out)
        with mock.patch.object(budget, "waived", return_value=True):
            code, out = self.check(
                "futureagi/platform", "amd64", *args, "--waiver-label", "ok"
            )
        self.assertEqual(code, 0, out)
        self.assertIn("waived", out)

    def test_missing_baseline_skips_growth_but_keeps_the_budget(self):
        code, out = self.check(
            "futureagi/serving",
            "arm64",
            "--baseline",
            "docker.io/futureagi/serving:latest",
            "--max-growth-percent",
            "10",
        )
        self.assertEqual(code, 1)
        self.assertIn("no baseline", out)

    def test_default_install_counts_a_shared_layer_once(self):
        # platform 90 + postgres 70, sharing the 30 MB base layer: 130 MB.
        code, out = self.check("futureagi/platform", "amd64", "--default-install")
        self.assertEqual(code, 0, out)
        self.assertIn("standalone install linux/amd64: 130.0 MB", out)
        config = dict(
            CONFIG, default_install=dict(CONFIG["default_install"], budget_mb=120)
        )
        self.budgets.write_text(json.dumps(config))
        code, out = self.check("futureagi/platform", "amd64", "--default-install")
        self.assertEqual(code, 1)
        self.assertIn("standalone install downloads 130.0 MB", out)

    def test_image_without_a_budget_fails_and_report_only_never_fails(self):
        self.registry.images["localhost:5000/futureagi/unknown:ci"] = manifest(x=1)
        self.assertEqual(self.check("futureagi/unknown")[0], 1)
        self.assertEqual(
            self.check("futureagi/serving", "arm64", "--report-only")[0], 0
        )

    def test_unenforced_entry_warns_only(self):
        code, out = self.check("futureagi/runner")
        self.assertEqual(code, 0, out)
        self.assertIn("WARNING", out)

    def test_variant_budget_is_selected_by_tag_suffix(self):
        self.registry.images["localhost:5000/futureagi/serving-gpu:ci"] = manifest(
            torch=450
        )
        code, out = self.run_script(
            "check",
            "--image",
            "futureagi/serving",
            "--tag-suffix=-gpu",
            "--arch",
            "amd64",
            "--ref",
            "localhost:5000/futureagi/serving-gpu:ci",
        )
        self.assertEqual(code, 0, out)
        self.assertIn("budget 500 MB", out)

    def test_report_lists_every_image_and_enforces_only_when_asked(self):
        self.registry.images["futureagi/platform:v9"] = manifest(
            "amd64", base=30, app=60
        )
        code, out = self.run_script("report", "--tag", "v9")
        self.assertEqual(code, 0)
        self.assertIn("futureagi/serving:v9-gpu", out)
        self.assertIn("standalone install (distinct layers)", out)
        self.registry.images["futureagi/platform:v9"] = manifest(
            "amd64", base=30, app=200
        )
        self.assertEqual(self.run_script("report", "--tag", "v9", "--enforce")[0], 1)


def rendered_images(compose_file: str, profiles: str) -> set[str]:
    if __package__:
        from .test_observed_catalog_compose import compose
    else:
        from test_observed_catalog_compose import compose
    services = compose(base_file=compose_file, COMPOSE_PROFILES=profiles)["services"]
    return {service["image"] for service in services.values() if "image" in service}


def untagged(image: str) -> str:
    return budget.repository(image.split("@", 1)[0])


class BudgetFileTests(unittest.TestCase):
    config = json.loads(BUDGETS.read_text())

    def entries(self) -> set[tuple[str, str]]:
        return {(e["image"], e.get("tag_suffix", "")) for e in self.config["images"]}

    def test_entries_are_well_formed(self):
        self.assertEqual(
            len(self.entries()), len(self.config["images"]), "duplicate entry"
        )
        for entry in self.config["images"]:
            for arch in budget.arches_of(self.config, entry):
                with self.subTest(
                    image=entry["image"], suffix=entry.get("tag_suffix", ""), arch=arch
                ):
                    self.assertGreater(budget.budget_for(entry, arch), 0)
                    projected = entry.get("projected_mb")
                    if isinstance(projected, dict):
                        projected = projected[arch]
                    if projected is not None:
                        self.assertGreater(budget.budget_for(entry, arch), projected)

    def test_every_released_image_has_a_budget(self):
        workflow = (ROOT / ".github/workflows/release-images.yml").read_text()
        released = set(re.findall(r"\bimage:\s*(futureagi/[a-z0-9-]+)", workflow))
        self.assertIn("futureagi/platform", released)
        for image in released:
            with self.subTest(image=image):
                self.assertIn((image, ""), self.entries())
        for suffix in set(re.findall(r"tag-suffix:\s*(-[a-z0-9]+)", workflow)):
            with self.subTest(suffix=suffix):
                self.assertTrue(any(s == suffix for _, s in self.entries()))

    @unittest.skipUnless(shutil.which("docker"), "docker CLI unavailable")
    def test_default_install_matches_docker_compose_yml(self):
        images = rendered_images("docker-compose.yml", "")
        install = self.config["default_install"]
        self.assertEqual(
            {untagged(image) for image in images if image.startswith("futureagi/")},
            {install["app"]},
        )
        self.assertEqual(
            {image for image in images if not image.startswith("futureagi/")},
            set(install["with"]),
        )

    def test_every_compose_image_of_ours_has_a_budget(self):
        # The raw files, so services behind every profile count too.
        for compose_file in ("docker-compose.yml", "docker-compose.distributed.yml"):
            text = (ROOT / compose_file).read_text()
            images = set(
                re.findall(r"^\s*image:\s*[\"']?(futureagi/[a-z0-9-]+)", text, re.M)
            )
            self.assertIn("futureagi/serving", images)
            for image in images:
                with self.subTest(compose_file=compose_file, image=image):
                    self.assertIn((image, ""), self.entries())


if __name__ == "__main__":
    unittest.main()
