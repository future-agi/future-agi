import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

import yaml

spec = importlib.util.spec_from_file_location(
    "verify_tags", Path(__file__).with_name("verify-platform-image-tags.py")
)
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)

VERSION = "v1.40.0"
OLD = "v1.39.0"
MIRROR = "us-east5-docker.pkg.dev/futureagiprimary/dockerhub-mirror/"
PLATFORM_KEYS = (
    "backend",
    "embedding",
    "runner",
    "gateway",
    "executor",
    "collector",
    "catalog",
    "lifecycle",
    "worker",
)


def image(repository, tag, **extra):
    return {"image": {"repository": repository, "tag": tag, **extra}}


def values(with_property_catalog=True, **tags):
    def tag(key):
        return tags.get(key, VERSION)

    document = {
        "backend": image(MIRROR + "futureagi/future-agi-ee", tag("backend")),
        "embedding": image("futureagi/serving", tag("embedding")),
        "nginx": image("nginx", "1.25.0"),
        "rabbitmq": image("bitnamilegacy/rabbitmq", "4.1.3-debian-12-r1"),
        "flower": image("mher/flower", "2.0.0"),
        "temporal_worker_simulation_runner": image(
            "futureagi/future-agi-simulation-runner", tag("runner")
        ),
        "agentcc_gateway": image("futureagi/agentcc-gateway", tag("gateway")),
        "codeExecutor": image("futureagi/code-executor", tag("executor")),
        "fiCollector": image("futureagi/fi-collector", tag("collector")),
        "errorFeed": image(
            "futureagi/omega-error-feed-worker",
            tag("worker"),
            digest="sha256:" + "a" * 64,
        ),
    }
    if with_property_catalog:
        document["propertyCatalog"] = {
            **image(MIRROR + "futureagi/fi-collector", tag("catalog")),
            "lifecycle": image(MIRROR + "futureagi/future-agi-ee", tag("lifecycle")),
            "sequencer": {"spool": {"ownerInit": image("busybox", "1.37.0")}},
        }
    return document


class StaleImagesTests(unittest.TestCase):
    def test_a_fully_bumped_file_is_clean(self):
        self.assertEqual(verify.stale_images(values(), VERSION), [])

    def test_a_region_without_property_catalog_is_clean(self):
        document = values(with_property_catalog=False)
        self.assertEqual(verify.stale_images(document, VERSION), [])

    def test_both_nested_property_catalog_tags_are_caught_with_their_paths(self):
        stale = verify.stale_images(values(catalog=OLD, lifecycle=OLD), VERSION)
        self.assertEqual(
            stale,
            [
                (".propertyCatalog.image", MIRROR + "futureagi/fi-collector", OLD),
                (
                    ".propertyCatalog.lifecycle.image",
                    MIRROR + "futureagi/future-agi-ee",
                    OLD,
                ),
            ],
        )

    def test_the_simulation_runner_and_error_feed_worker_are_in_the_lane(self):
        stale = verify.stale_images(values(runner=OLD, worker=OLD), VERSION)
        self.assertEqual(
            [path for path, _, _ in stale],
            [".temporal_worker_simulation_runner.image", ".errorFeed.image"],
        )

    def test_every_platform_image_is_covered(self):
        stale = verify.stale_images(
            values(**{key: OLD for key in PLATFORM_KEYS}), VERSION
        )
        repositories = {repo.rsplit("futureagi/", 1)[1] for _, repo, _ in stale}
        self.assertEqual(repositories, set(verify.PLATFORM_IMAGES))

    def test_third_party_images_never_count(self):
        document = values()
        document["nginx"]["image"]["tag"] = "0.0.1"
        document["rabbitmq"]["image"]["tag"] = "0.0.1"
        document["propertyCatalog"]["sequencer"]["spool"]["ownerInit"]["image"][
            "tag"
        ] = "0.0.1"
        self.assertEqual(verify.stale_images(document, VERSION), [])

    def test_a_lookalike_repository_outside_the_org_is_ignored(self):
        document = values()
        document["backend"]["image"] = {
            "repository": "someone-else/future-agi-ee",
            "tag": OLD,
        }
        self.assertEqual(verify.stale_images(document, VERSION), [])


class MainTests(unittest.TestCase):
    def run_main(self, *documents, version=VERSION):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index, document in enumerate(documents):
                path = Path(directory) / f"values-{index}.yaml"
                path.write_text(yaml.safe_dump(document), encoding="utf-8")
                paths.append(str(path))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = verify.main(["verify", version, *paths])
            return rc, out.getvalue()

    def test_clean_files_exit_zero_and_print_nothing(self):
        self.assertEqual(self.run_main(values(), values()), (0, ""))

    def test_a_stale_file_fails_the_job_and_names_the_image(self):
        rc, out = self.run_main(values(), values(catalog=OLD))
        self.assertEqual(rc, 1)
        self.assertIn("::error::", out)
        self.assertIn("values-1.yaml has platform images not bumped to v1.40.0", out)
        self.assertIn(
            ".propertyCatalog.image: " + MIRROR + "futureagi/fi-collector = v1.39.0",
            out,
        )
        self.assertNotIn("values-0.yaml", out)

    def test_every_stale_file_is_reported_not_just_the_first(self):
        rc, out = self.run_main(values(backend=OLD), values(worker=OLD))
        self.assertEqual(rc, 1)
        self.assertIn("values-0.yaml", out)
        self.assertIn("values-1.yaml", out)

    def test_missing_arguments_are_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(verify.main(["verify", VERSION]), 2)


if __name__ == "__main__":
    unittest.main()
