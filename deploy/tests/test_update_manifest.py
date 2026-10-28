"""Release manifest contract: versions, image references, strict validation,
deployment-file hashing, the release generator CLI and the JSON Schema.

deploy/update/manifest.py is shared by the release workflow
(scripts/generate_update_manifest.py) and the host-side updater, so every rule
here guards both sides. No network, no Docker; the JSON Schema checks that need
the optional 'jsonschema' module skip without it.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.update.manifest import (  # noqa: E402
    BUNDLE_ASSET, CHANNELS, COMPONENT_REPOSITORIES, COMPONENT_TAG_SUFFIX, DEPLOYMENT_FILE_LIST, MANIFEST_ASSET,
    PRODUCT, SCHEMA_VERSION, UPGRADE_FLOOR_FILE, ImageRef, ManifestError, canonical_json, component_tag,
    deployment_hashes, hash_path, is_prerelease, is_version, left_out_of_digests, manifest_hash, parse_image_ref,
    parse_version, read_deployment_file_list, read_upgrade_floor, validate_manifest,
)

GENERATOR = ROOT / "scripts" / "generate_update_manifest.py"
SCHEMA_PATH = ROOT / "deploy" / "update" / "release-manifest.schema.json"

VERSION = "v1.52.0"
SOURCE_SHA = "0123456789abcdef0123456789abcdef01234567"
PUBLISHED_AT = "2026-10-19T00:00:00Z"
CHART = "oci://ghcr.io/future-agi/charts/futureagi"
HELM_DIGEST = "sha256:" + "a" * 64
ISSUER = "https://token.actions.githubusercontent.com"

# The contract, written out independently of the module under test.
EXPECTED_REPOSITORIES = {
    "standalone": "futureagi/standalone",
    "backend": "futureagi/future-agi",
    "frontend": "futureagi/frontend",
    "fi_collector": "futureagi/fi-collector",
    "agentcc_gateway": "futureagi/agentcc-gateway",
    "serving": "futureagi/serving",
    "serving_gpu": "futureagi/serving",
    "code_executor": "futureagi/code-executor",
    "simulation_runner": "futureagi/future-agi-simulation-runner",
}
MULTI_ARCH = {"standalone", "backend", "frontend", "fi_collector", "agentcc_gateway", "serving", "code_executor"}
TOP_LEVEL_REQUIRED = (
    "schema_version", "product", "channel", "version", "source_sha", "published_at", "minimum_version",
    "signature", "components", "helm", "deployment", "migration",
)
COMPONENT_DIGESTS = {
    name: "sha256:" + "123456789"[index] * 64 for index, name in enumerate(sorted(EXPECTED_REPOSITORIES))
}


def tag_for(name: str, version: str = VERSION) -> str:
    return version + ("-gpu" if name == "serving_gpu" else "")


def valid_manifest(version: str = VERSION, channel: str = "stable") -> dict:
    components = {}
    for name, repository in EXPECTED_REPOSITORIES.items():
        components[name] = {
            "image": f"docker.io/{repository}:{tag_for(name, version)}",
            "digest": COMPONENT_DIGESTS[name],
            "platforms": ["linux/amd64", "linux/arm64"] if name in MULTI_ARCH else ["linux/amd64"],
        }
    return {
        "schema_version": 1,
        "product": "future-agi",
        "channel": channel,
        "version": version,
        "source_sha": SOURCE_SHA,
        "published_at": PUBLISHED_AT,
        "minimum_version": "v1.51.0",
        "release_notes": f"https://github.com/future-agi/future-agi/releases/tag/{version}",
        "signature": {
            "bundle": "future-agi-update-manifest.sigstore.json",
            "identity": "https://github.com/future-agi/future-agi/.github/workflows/"
                        f"release-update-manifest.yml@refs/tags/{version}",
            "issuer": ISSUER,
        },
        "components": components,
        "helm": {"chart": CHART, "version": version[1:], "digest": HELM_DIGEST},
        "deployment": {"files": {
            "docker-compose.yml": "b" * 64,
            "futureagi/scripts/property_catalog_oss": "c" * 64,
        }},
        "migration": {"requires_bootstrap": True, "rollback": "manual"},
    }


def retag(manifest: dict, version: str) -> dict:
    """Move a manifest to another version consistently (images and helm too)."""

    manifest["version"] = version
    manifest["helm"]["version"] = version[1:]
    for name, component in manifest["components"].items():
        component["image"] = f"docker.io/{EXPECTED_REPOSITORIES[name]}:{tag_for(name, version)}"
    return manifest


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Relative paths that stay inside the checkout but are not its one normalized spelling.
UNNORMALIZED_PATHS = ("/", "//x", "a//b", "deploy//x.yml", "a/", "deploy/", "a/./b", "a/.", "deploy/.", "./", "x/..",
                      "a/b/../c")
# Names that merely contain dots are ordinary names.
DOTTED_PATHS = ("...", "a/.../b", "..x", "x..", ".hidden/x", "a/.b/c", "deploy/x.y.z", ".env.example")


class ManifestCase(unittest.TestCase):
    def assertRejected(self, manifest, message):
        with self.assertRaisesRegex(ManifestError, message):
            validate_manifest(manifest)

    def assertAccepted(self, manifest):
        try:
            return validate_manifest(manifest)
        except ManifestError as exc:  # pragma: no cover - failure path
            self.fail(f"valid manifest rejected: {exc}")

    def mutated(self, mutate, version=VERSION, channel="stable"):
        manifest = valid_manifest(version, channel)
        mutate(manifest)
        return manifest


# ------------------------------------------------------------------ constants --

class ContractConstantTests(unittest.TestCase):
    def test_component_repositories_are_the_published_repositories(self):
        self.assertEqual(COMPONENT_REPOSITORIES, EXPECTED_REPOSITORIES)

    def test_only_serving_gpu_has_a_tag_suffix(self):
        self.assertEqual(COMPONENT_TAG_SUFFIX, {"serving_gpu": "-gpu"})

    def test_schema_version_product_channels_and_asset_names(self):
        self.assertEqual(SCHEMA_VERSION, 2)
        self.assertEqual(PRODUCT, "future-agi")
        self.assertEqual(CHANNELS, ("stable", "beta"))
        self.assertEqual(MANIFEST_ASSET, "future-agi-update-manifest.json")
        self.assertEqual(BUNDLE_ASSET, "future-agi-update-manifest.sigstore.json")
        self.assertEqual(DEPLOYMENT_FILE_LIST, "deploy/update/deployment-files.txt")
        self.assertEqual(UPGRADE_FLOOR_FILE, "deploy/update/upgrade-floor")

    def test_component_tag_appends_the_suffix(self):
        manifest = valid_manifest()
        for name in EXPECTED_REPOSITORIES:
            with self.subTest(name=name):
                self.assertEqual(component_tag(manifest, name), tag_for(name))
        self.assertEqual(component_tag(retag(valid_manifest(channel="beta"), "v2.0.0-rc.3"), "serving_gpu"),
                         "v2.0.0-rc.3-gpu")


# ------------------------------------------------------------------- versions --

class VersionTests(unittest.TestCase):
    def test_parse_version_returns_sortable_tuples(self):
        self.assertEqual(parse_version("v1.2.3"), (1, 2, 3, 2, 0))
        self.assertEqual(parse_version("v1.2.3-beta.4"), (1, 2, 3, 0, 4))
        self.assertEqual(parse_version("v1.2.3-rc.4"), (1, 2, 3, 1, 4))
        self.assertEqual(parse_version("v0.0.0"), (0, 0, 0, 2, 0))
        self.assertEqual(parse_version("v1.0.0-beta.0"), (1, 0, 0, 0, 0))

    def test_ordering_beta_before_rc_before_release(self):
        ordered = [
            "v0.9.9", "v1.0.0-beta.0", "v1.0.0-beta.1", "v1.0.0-beta.2", "v1.0.0-beta.10",
            "v1.0.0-rc.0", "v1.0.0-rc.1", "v1.0.0-rc.11", "v1.0.0", "v1.0.1-beta.0", "v1.0.1",
            "v1.0.9", "v1.0.10", "v1.1.0", "v1.9.0", "v1.10.0", "v2.0.0-rc.1", "v2.0.0", "v9.99.99", "v10.0.0",
        ]
        shuffled = ordered[::2] + ordered[1::2][::-1]
        self.assertEqual(sorted(shuffled, key=parse_version), ordered)
        for lower, higher in zip(ordered, ordered[1:]):
            with self.subTest(lower=lower, higher=higher):
                self.assertLess(parse_version(lower), parse_version(higher))

    def test_ordering_is_numeric_not_lexical(self):
        self.assertGreater(parse_version("v1.10.0"), parse_version("v1.9.0"))
        self.assertGreater(parse_version("v1.0.10"), parse_version("v1.0.9"))
        self.assertGreater(parse_version("v10.0.0"), parse_version("v9.99.99"))
        self.assertGreater(parse_version("v1.0.0-beta.10"), parse_version("v1.0.0-beta.9"))
        self.assertGreater(parse_version("v1.0.0-rc.0"), parse_version("v1.0.0-beta.99"))
        self.assertGreater(parse_version("v1.0.0"), parse_version("v1.0.0-rc.99"))

    def test_leading_zeros_are_rejected(self):
        for value in ("v01.0.0", "v00.0.0", "v1.01.0", "v1.00.0", "v1.0.01", "v1.0.00",
                      "v1.0.0-beta.01", "v1.0.0-rc.00"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ManifestError, "expected vMAJOR.MINOR.PATCH"):
                    parse_version(value)
                self.assertFalse(is_version(value))

    def test_malformed_versions_are_rejected(self):
        for value in ("", "1.0.0", "V1.0.0", "vv1.0.0", "v1", "v1.0", "v1.0.0.0", "v1.0.0-", "v1.0.0-beta",
                      "v1.0.0-beta1", "v1.0.0-beta.", "v1.0.0-alpha.1", "v1.0.0-RC.1", "v1.0.0-Beta.1",
                      "v1.0.0-rc.1.2", "v1.0.0+build.1", "v1.0.0-rc.-1", "v-1.0.0", "v1.0.0-gpu", "latest",
                      "v1.0.0 v1.0.1", "v1. 0.0", "v1.0.0-rc.1-gpu"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ManifestError, "invalid version"):
                    parse_version(value)
                self.assertFalse(is_version(value))

    def test_non_string_versions_raise_manifest_error(self):
        for value in (None, 1, 1.0, b"v1.0.0", ["v1.0.0"], {"v": 1}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ManifestError, "invalid version"):
                    parse_version(value)
                self.assertFalse(is_version(value))

    def test_non_ascii_digits_are_not_versions(self):
        # \d matches every Unicode decimal digit; a version is ASCII only, and
        # 'v1０.0.0' must not alias v10.0.0.
        for value in ("v1٣.0.0", "v1０.0.0", "v0.1٣.0", "v1.0.0-rc.1١", "v1.0.١"):
            with self.subTest(value=value):
                self.assertFalse(is_version(value))
                with self.assertRaises(ManifestError):
                    parse_version(value)

    def test_is_version_accepts_exactly_the_canonical_form(self):
        for value in ("v0.0.0", "v1.52.0", "v1.52.0-beta.1", "v1.52.0-rc.0", "v123.456.789-rc.10"):
            with self.subTest(value=value):
                self.assertTrue(is_version(value))
        for value in (" v1.0.0", "v1.0.0 ", "v1.0.0\n", "\tv1.0.0"):
            with self.subTest(value=value):
                self.assertFalse(is_version(value))

    def test_is_prerelease(self):
        self.assertTrue(is_prerelease("v1.0.0-beta.0"))
        self.assertTrue(is_prerelease("v1.0.0-rc.3"))
        self.assertFalse(is_prerelease("v1.0.0"))
        self.assertFalse(is_prerelease("v0.0.0"))
        for value in ("1.0.0", "", "v1.0.0-alpha.1"):
            with self.subTest(value=value), self.assertRaises(ManifestError):
                is_prerelease(value)


# ----------------------------------------------------------------- image refs --

class ImageRefTests(unittest.TestCase):
    DIGEST = "sha256:" + "0123456789abcdef" * 4

    def test_docker_hub_spellings_resolve_to_one_name(self):
        expected = ImageRef("docker.io", "futureagi/standalone", "v1.52.0", None)
        for value in ("futureagi/standalone:v1.52.0", "docker.io/futureagi/standalone:v1.52.0",
                      "index.docker.io/futureagi/standalone:v1.52.0",
                      "registry-1.docker.io/futureagi/standalone:v1.52.0",
                      "DOCKER.IO/futureagi/standalone:v1.52.0", "Index.Docker.Io/futureagi/standalone:v1.52.0"):
            with self.subTest(value=value):
                self.assertEqual(parse_image_ref(value), expected)

    def test_official_images_get_the_library_prefix(self):
        expected = ImageRef("docker.io", "library/nginx", "1.27", None)
        for value in ("nginx:1.27", "library/nginx:1.27", "docker.io/nginx:1.27", "docker.io/library/nginx:1.27",
                      "index.docker.io/nginx:1.27"):
            with self.subTest(value=value):
                self.assertEqual(parse_image_ref(value), expected)
        self.assertEqual(parse_image_ref("nginx"), ImageRef("docker.io", "library/nginx", None, None))

    def test_other_registries_keep_their_host_and_path(self):
        cases = {
            "ghcr.io/future-agi/standalone:v1": ImageRef("ghcr.io", "future-agi/standalone", "v1", None),
            "GHCR.IO/futureagi/standalone:v1": ImageRef("ghcr.io", "futureagi/standalone", "v1", None),
            "localhost:5000/futureagi/standalone:v1": ImageRef("localhost:5000", "futureagi/standalone", "v1", None),
            "localhost:5000/futureagi/standalone": ImageRef("localhost:5000", "futureagi/standalone", None, None),
            "localhost/standalone:v1": ImageRef("localhost", "standalone", "v1", None),
            "registry.example.com:443/a/b/c:v1": ImageRef("registry.example.com:443", "a/b/c", "v1", None),
            "myhost:5000/foo": ImageRef("myhost:5000", "foo", None, None),
            "127.0.0.1:5000/foo/bar:t": ImageRef("127.0.0.1:5000", "foo/bar", "t", None),
            "my-registry.internal/x__y/a.b-c--d:t": ImageRef("my-registry.internal", "x__y/a.b-c--d", "t", None),
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(parse_image_ref(value), expected)

    def test_tag_and_digest_together(self):
        ref = parse_image_ref(f"futureagi/standalone:v1.52.0@{self.DIGEST}")
        self.assertEqual(ref, ImageRef("docker.io", "futureagi/standalone", "v1.52.0", self.DIGEST))
        self.assertEqual(str(ref), f"docker.io/futureagi/standalone:v1.52.0@{self.DIGEST}")
        self.assertEqual(ref.name, "docker.io/futureagi/standalone")

    def test_digest_without_tag(self):
        ref = parse_image_ref(f"localhost:5000/futureagi/standalone@{self.DIGEST}")
        self.assertEqual(ref, ImageRef("localhost:5000", "futureagi/standalone", None, self.DIGEST))
        self.assertEqual(str(ref), f"localhost:5000/futureagi/standalone@{self.DIGEST}")

    def test_str_and_name_are_normalized(self):
        self.assertEqual(str(parse_image_ref("nginx")), "docker.io/library/nginx")
        self.assertEqual(str(parse_image_ref("index.docker.io/futureagi/standalone:v1")),
                         "docker.io/futureagi/standalone:v1")
        self.assertEqual(parse_image_ref("localhost:5000/a/b:v1").name, "localhost:5000/a/b")

    def test_image_ref_is_immutable(self):
        ref = parse_image_ref("futureagi/standalone:v1")
        with self.assertRaises(AttributeError):
            ref.tag = "v2"  # type: ignore[misc]

    def test_tag_boundaries(self):
        self.assertEqual(parse_image_ref("a/b:" + "t" * 128).tag, "t" * 128)
        self.assertEqual(parse_image_ref("a/b:_x").tag, "_x")
        self.assertEqual(parse_image_ref("a/b:v1.52.0-gpu").tag, "v1.52.0-gpu")
        for value in ("a/b:" + "t" * 129, "a/b:", "a/b:-x", "a/b:.x", "a/b:v1+1", "a/b:v 1", "a/b:t@g"):
            with self.subTest(value=value), self.assertRaisesRegex(ManifestError, "invalid image (tag|digest)"):
                parse_image_ref(value)

    def test_invalid_digests_are_rejected(self):
        for digest in ("", "sha256:", "sha256:" + "a" * 63, "sha256:" + "a" * 65, "sha256:" + "A" * 64,
                       "sha512:" + "a" * 64, "a" * 64, "sha256:" + "g" * 64, f"{self.DIGEST}@{self.DIGEST}",
                       "sha256:" + "a" * 64 + "١", "sha256 :" + "a" * 64):
            with self.subTest(digest=digest), self.assertRaisesRegex(ManifestError, "invalid image digest"):
                parse_image_ref(f"futureagi/standalone:v1@{digest}")

    def test_uppercase_in_repository_path_is_rejected(self):
        for value in ("futureagi/Standalone:v1", "Futureagi/standalone:v1", "ghcr.io/FutureAGI/standalone:v1",
                      "localhost:5000/futureagi/STANDALONE"):
            with self.subTest(value=value), self.assertRaisesRegex(ManifestError, "invalid repository"):
                parse_image_ref(value)

    def test_malformed_repository_paths_are_rejected(self):
        for value in ("/foo", "foo/", "a//b", "-foo/bar", "foo-/bar", "foo/_bar", "foo/bar_", "foo/b___ar",
                      "foo/b..ar", "fü/bar", f"@{self.DIGEST}", "a/b:c:d", "a b/c", "ghcr.io/"):
            with self.subTest(value=value), self.assertRaisesRegex(ManifestError, "invalid (repository|image)"):
                parse_image_ref(value)

    def test_invalid_registries_are_rejected(self):
        for value in ("-bad.io/a/b", "bad-.io/a/b", "a..b/c/d", "host:abc/a/b", "host:123456/a/b",
                      "[::1]:5000/a/b", "bad_host.io/a/b", "höst.io/a/b"):
            with self.subTest(value=value), self.assertRaisesRegex(ManifestError, "invalid registry"):
                parse_image_ref(value)

    def test_empty_whitespace_and_non_strings_are_rejected(self):
        for value in ("", " ", " futureagi/standalone:v1", "futureagi/standalone:v1 ", "futureagi/standalone:v1\n",
                      "\tfutureagi/standalone", None, 1, b"futureagi/standalone:v1", ["a/b"]):
            with self.subTest(value=value), self.assertRaisesRegex(ManifestError, "invalid image reference"):
                parse_image_ref(value)


# ------------------------------------------------------- canonical form/hash --

class CanonicalJsonTests(unittest.TestCase):
    def test_canonical_json_is_sorted_compact_ascii(self):
        self.assertEqual(canonical_json({"b": 1, "a": [1, {"d": 2, "c": "é"}]}),
                         b'{"a":[1,{"c":"\\u00e9","d":2}],"b":1}')

    def test_manifest_hash_is_sha256_of_canonical_json(self):
        manifest = valid_manifest()
        self.assertEqual(manifest_hash(manifest), sha256_hex(canonical_json(manifest)))
        self.assertRegex(manifest_hash(manifest), r"^[0-9a-f]{64}$")

    def test_manifest_hash_ignores_key_order_and_tracks_values(self):
        manifest = valid_manifest()
        reordered = json.loads(json.dumps(dict(reversed(list(manifest.items())))))
        self.assertEqual(manifest_hash(manifest), manifest_hash(reordered))
        changed = valid_manifest()
        changed["components"]["backend"]["digest"] = "sha256:" + "f" * 64
        self.assertNotEqual(manifest_hash(manifest), manifest_hash(changed))


# ------------------------------------------------------- validate_manifest --

class ValidManifestTests(ManifestCase):
    def test_fully_valid_manifest_passes_unchanged(self):
        manifest = valid_manifest()
        result = self.assertAccepted(manifest)
        self.assertEqual(result, manifest)
        self.assertIsInstance(result, dict)

    def test_returns_a_detached_copy(self):
        manifest = valid_manifest()
        result = validate_manifest(manifest)
        self.assertIsNot(result, manifest)
        self.assertIsNot(result["components"], manifest["components"])
        self.assertIsNot(result["components"]["backend"]["platforms"], manifest["components"]["backend"]["platforms"])
        result["components"]["backend"]["digest"] = "sha256:" + "f" * 64
        result["deployment"]["files"]["new"] = "0" * 64
        self.assertEqual(manifest, valid_manifest())
        snapshot = copy.deepcopy(result)
        manifest["signature"]["bundle"] = "changed.json"
        manifest["components"]["frontend"]["platforms"].append("linux/arm64")
        self.assertEqual(result, snapshot)

    def test_optional_fields_may_be_omitted(self):
        def strip(m):
            del m["release_notes"]
            del m["signature"]["identity"]
            del m["signature"]["issuer"]
            for component in m["components"].values():
                del component["platforms"]
        self.assertAccepted(self.mutated(strip))

    def test_identity_and_issuer_are_independently_optional(self):
        self.assertAccepted(self.mutated(lambda m: m["signature"].pop("issuer")))
        self.assertAccepted(self.mutated(lambda m: m["signature"].pop("identity")))

    def test_beta_channel_accepts_prereleases_and_releases(self):
        for version in ("v1.53.0-beta.1", "v1.53.0-rc.2", "v1.53.0"):
            with self.subTest(version=version):
                self.assertAccepted(retag(valid_manifest(channel="beta"), version))

    def test_docker_hub_short_names_and_other_registry_hosts_are_accepted(self):
        for image in ("futureagi/future-agi:v1.52.0", "index.docker.io/futureagi/future-agi:v1.52.0",
                      "ghcr.io/futureagi/future-agi:v1.52.0", "localhost:5000/futureagi/future-agi:v1.52.0",
                      "mirror.example.com:8443/futureagi/future-agi:v1.52.0",
                      "MIRROR.example.com/futureagi/future-agi:v1.52.0"):
            with self.subTest(image=image):
                self.assertAccepted(self.mutated(lambda m: m["components"]["backend"].__setitem__("image", image)))

    def test_single_platform_lists_are_accepted(self):
        for platforms in (["linux/amd64"], ["linux/arm64"], ["linux/arm64", "linux/amd64"]):
            with self.subTest(platforms=platforms):
                self.assertAccepted(
                    self.mutated(lambda m: m["components"]["serving"].__setitem__("platforms", platforms)))

    def test_minimum_version_may_equal_or_precede_the_version(self):
        for minimum in ("v1.52.0", "v1.52.0-rc.9", "v1.0.0", "v0.0.0", "v1.51.99"):
            with self.subTest(minimum=minimum):
                self.assertAccepted(self.mutated(lambda m: m.__setitem__("minimum_version", minimum)))

    def test_requires_bootstrap_false_and_rollback_unsupported_are_accepted(self):
        def change(m):
            m["migration"] = {"requires_bootstrap": False, "rollback": "unsupported"}
        self.assertAccepted(self.mutated(change))

    def test_helm_chart_on_a_registry_with_a_port_is_accepted(self):
        self.assertAccepted(self.mutated(lambda m: m["helm"].__setitem__("chart", "oci://ghcr.io:443/charts/futureagi")))

    def test_bundle_name_boundaries(self):
        for bundle in ("b", "a" * 201, "Bundle_1.sigstore.json", "x-y.z", "a..b"):
            with self.subTest(bundle=bundle):
                self.assertAccepted(self.mutated(lambda m: m["signature"].__setitem__("bundle", bundle)))

    def test_nested_deployment_paths_are_accepted(self):
        def change(m):
            m["deployment"]["files"] = {"deploy/clickhouse/users.d/zz-default-password.xml": "d" * 64,
                                        ".env.example": "e" * 64}
        self.assertAccepted(self.mutated(change))

    def test_a_json_roundtripped_manifest_is_accepted(self):
        self.assertAccepted(json.loads(json.dumps(valid_manifest())))


class ManifestShapeTests(ManifestCase):
    SECTIONS = ("signature", "components", "helm", "deployment", "migration")

    def test_non_object_manifest_is_rejected(self):
        for value in (None, [], "manifest", 1, True, [valid_manifest()]):
            with self.subTest(value=value):
                self.assertRejected(value, "^manifest must be an object$")

    def test_each_missing_top_level_key_is_rejected(self):
        for key in TOP_LEVEL_REQUIRED:
            with self.subTest(key=key):
                self.assertRejected(self.mutated(lambda m: m.pop(key)), f"^manifest is missing {key}$")

    def test_missing_keys_are_all_named(self):
        manifest = valid_manifest()
        del manifest["version"], manifest["helm"]
        self.assertRejected(manifest, "^manifest is missing helm, version$")

    def test_unknown_top_level_key_is_rejected(self):
        self.assertRejected(self.mutated(lambda m: m.__setitem__("extra", 1)),
                            r"^manifest has unknown field\(s\) extra$")
        self.assertRejected(self.mutated(lambda m: m.update({"zz": 1, "aa": 2})),
                            r"^manifest has unknown field\(s\) aa, zz$")

    def test_unknown_keys_are_rejected_at_every_level(self):
        cases = {
            "manifest.signature": lambda m: m["signature"],
            "manifest.components": lambda m: m["components"],
            "manifest.components.backend": lambda m: m["components"]["backend"],
            "manifest.components.serving_gpu": lambda m: m["components"]["serving_gpu"],
            "manifest.helm": lambda m: m["helm"],
            "manifest.deployment": lambda m: m["deployment"],
            "manifest.migration": lambda m: m["migration"],
        }
        for context, section in cases.items():
            with self.subTest(context=context):
                manifest = self.mutated(lambda m: section(m).__setitem__("unexpected", "x"))
                self.assertRejected(manifest, "^" + re.escape(f"{context} has unknown field(s) unexpected") + "$")

    def test_sections_must_be_objects(self):
        for section in self.SECTIONS:
            for value in (None, [], "x", 1):
                with self.subTest(section=section, value=value):
                    self.assertRejected(self.mutated(lambda m: m.__setitem__(section, value)),
                                        f"^manifest.{section} must be an object$")

    def test_component_must_be_an_object(self):
        for value in (None, "docker.io/futureagi/future-agi:v1.52.0", [], 1):
            with self.subTest(value=value):
                self.assertRejected(self.mutated(lambda m: m["components"].__setitem__("backend", value)),
                                    "^manifest.components.backend must be an object$")

    def test_each_missing_nested_key_is_rejected(self):
        cases = (
            ("signature", "bundle", "manifest.signature"),
            ("helm", "chart", "manifest.helm"),
            ("helm", "version", "manifest.helm"),
            ("helm", "digest", "manifest.helm"),
            ("deployment", "files", "manifest.deployment"),
            ("migration", "requires_bootstrap", "manifest.migration"),
            ("migration", "rollback", "manifest.migration"),
        )
        for section, key, context in cases:
            with self.subTest(section=section, key=key):
                self.assertRejected(self.mutated(lambda m: m[section].pop(key)),
                                    "^" + re.escape(f"{context} is missing {key}") + "$")

    def test_each_missing_component_is_rejected(self):
        for name in EXPECTED_REPOSITORIES:
            with self.subTest(name=name):
                self.assertRejected(self.mutated(lambda m: m["components"].pop(name)),
                                    f"^manifest.components is missing {name}$")

    def test_unknown_component_is_rejected(self):
        def add(m):
            m["components"]["serving_tpu"] = dict(m["components"]["serving"])
        self.assertRejected(self.mutated(add), r"^manifest.components has unknown field\(s\) serving_tpu$")

    def test_each_missing_component_key_is_rejected(self):
        for name in ("standalone", "serving_gpu"):
            for key in ("image", "digest"):
                with self.subTest(name=name, key=key):
                    self.assertRejected(self.mutated(lambda m: m["components"][name].pop(key)),
                                        f"^manifest.components.{name} is missing {key}$")


class ManifestFieldTests(ManifestCase):
    # schema_version / product / channel

    def test_schema_version_must_be_supported(self):
        for value in (3, 0, -1, "1", None, [1], 1.5):
            with self.subTest(value=value):
                self.assertRejected(self.mutated(lambda m: m.__setitem__("schema_version", value)),
                                    "unsupported manifest schema_version .*; update bin/update")

    def test_schema_version_true_is_not_the_integer_1(self):
        # JSON true == 1 in Python; the schema's const: 1 rejects it, so must the validator.
        self.assertRejected(self.mutated(lambda m: m.__setitem__("schema_version", True)),
                            "unsupported manifest schema_version")

    def test_schema_version_must_be_the_integer_1(self):
        for value in (False, 1.0):
            with self.subTest(value=value):
                self.assertRejected(self.mutated(lambda m: m.__setitem__("schema_version", value)),
                                    "^" + re.escape(f"unsupported manifest schema_version {value!r}; update bin/update")
                                    + "$")

    def test_product_must_be_future_agi(self):
        for value in ("future_agi", "Future-AGI", "future-agi ", "", None, "other"):
            with self.subTest(value=value):
                self.assertRejected(self.mutated(lambda m: m.__setitem__("product", value)),
                                    "manifest is for .*, not 'future-agi'")

    def test_channel_must_be_stable_or_beta(self):
        for value in ("nightly", "Stable", "BETA", "", " stable", None, ["stable"], 1):
            with self.subTest(value=value):
                self.assertRejected(self.mutated(lambda m: m.__setitem__("channel", value)),
                                    "unknown manifest channel")

    # version / prerelease / minimum

    def test_version_with_surrounding_spaces_is_rejected(self):
        for value in (" v1.52.0", "v1.52.0 ", "", "   ", None, 152):
            with self.subTest(value=value):
                self.assertRejected(self.mutated(lambda m: m.__setitem__("version", value)),
                                    "manifest.version must be a non-empty string without surrounding spaces")

    def test_malformed_version_is_rejected(self):
        for value in ("1.52.0", "v1.52", "v1.52.0-alpha.1", "v01.52.0", "latest"):
            with self.subTest(value=value):
                self.assertRejected(retag(valid_manifest(channel="beta"), value), "invalid version")

    def test_prerelease_on_stable_is_rejected(self):
        for version in ("v1.53.0-beta.1", "v1.53.0-rc.1", "v1.53.0-rc.0"):
            with self.subTest(version=version):
                self.assertRejected(retag(valid_manifest(), version),
                                    f"^stable manifest carries a prerelease version {re.escape(version)}$")

    def test_minimum_version_newer_than_version_is_rejected(self):
        for minimum in ("v1.52.1", "v1.53.0", "v2.0.0", "v1.52.1-beta.0"):
            with self.subTest(minimum=minimum):
                self.assertRejected(
                    self.mutated(lambda m: m.__setitem__("minimum_version", minimum)),
                    "^" + re.escape(f"manifest.minimum_version {minimum} is newer than its version {VERSION}") + "$")

    def test_release_minimum_is_newer_than_its_own_prerelease(self):
        manifest = retag(valid_manifest(channel="beta"), "v1.53.0-rc.1")
        manifest["minimum_version"] = "v1.53.0"
        self.assertRejected(manifest, "is newer than its version v1.53.0-rc.1")
        manifest["minimum_version"] = "v1.53.0-rc.2"
        self.assertRejected(manifest, "is newer than its version")
        manifest["minimum_version"] = "v1.53.0-beta.7"
        self.assertAccepted(manifest)

    def test_malformed_minimum_version_is_rejected(self):
        for value in ("1.51.0", "v1.51", "latest", "v1.51.0-alpha.1"):
            with self.subTest(value=value):
                self.assertRejected(self.mutated(lambda m: m.__setitem__("minimum_version", value)),
                                    "invalid version")
        for value in (" v1.51.0", "v1.51.0 ", "", None, 1):
            with self.subTest(value=value):
                self.assertRejected(self.mutated(lambda m: m.__setitem__("minimum_version", value)),
                                    "manifest.minimum_version must be a non-empty string")

    def test_minimum_version_with_non_ascii_digits_is_rejected(self):
        self.assertRejected(self.mutated(lambda m: m.__setitem__("minimum_version", "v0.1٣.0")),
                            "invalid version")

    # source_sha / published_at

    def test_source_sha_must_be_40_lowercase_hex(self):
        for value in ("a" * 39, "a" * 41, "A" * 40, "g" * 40, " " + "a" * 39, "a" * 40 + " ", "", None,
                      "0123456789abcdef0123456789abcdef0123456١"):
            with self.subTest(value=value):
                self.assertRejected(self.mutated(lambda m: m.__setitem__("source_sha", value)),
                                    "^manifest.source_sha must be a 40-character commit SHA$")

    def test_source_sha_must_be_a_string(self):
        # The schema says type: string; str(int) of 40 decimal digits must not pass.
        self.assertRejected(self.mutated(lambda m: m.__setitem__("source_sha", int("1" * 40))),
                            "manifest.source_sha must be a 40-character commit SHA")

    def test_published_at_with_non_ascii_digits_is_rejected(self):
        for value in ("２０２６-10-19T00:00:00Z", "2026-1٠-19T00:00:00Z", "2026-10-19T00:00:0٣Z"):
            with self.subTest(value=value):
                self.assertRejected(self.mutated(lambda m: m.__setitem__("published_at", value)),
                                    "^manifest.published_at must be a UTC timestamp")

    def test_digests_and_timestamps_must_be_strings(self):
        cases = (
            (("published_at",), 20261019000000, "manifest.published_at must be a UTC timestamp"),
            (("helm", "digest"), int("1" * 64), "manifest.helm.digest must be sha256:<64 hex>"),
            (("components", "backend", "digest"), ["sha256:" + "a" * 64],
             "manifest.components.backend.digest must be sha256:<64 hex>"),
            (("deployment", "files", "docker-compose.yml"), int("1" * 64),
             re.escape("manifest.deployment.files['docker-compose.yml'] must be a sha256 hex digest")),
        )
        for path, value, message in cases:
            with self.subTest(path=path):
                def change(m, path=path, value=value):
                    target = m
                    for key in path[:-1]:
                        target = target[key]
                    target[path[-1]] = value
                self.assertRejected(self.mutated(change), message)

    def test_published_at_must_be_a_utc_second_timestamp(self):
        for value in ("2026-10-19", "2026-10-19T00:00:00", "2026-10-19T00:00:00+00:00", "2026-10-19T00:00:00.123Z",
                      "2026-10-19 00:00:00Z", "2026-10-19t00:00:00z", " 2026-10-19T00:00:00Z", "26-10-19T00:00:00Z",
                      "", None, 1760832000):
            with self.subTest(value=value):
                self.assertRejected(self.mutated(lambda m: m.__setitem__("published_at", value)),
                                    "^manifest.published_at must be a UTC timestamp")

    # release_notes

    def test_release_notes_must_be_https(self):
        for value in ("http://example.com", "ftp://example.com", "HTTPS://example.com", "example.com",
                      "javascript:alert(1)", "//example.com"):
            with self.subTest(value=value):
                self.assertRejected(self.mutated(lambda m: m.__setitem__("release_notes", value)),
                                    "^manifest.release_notes must be an https URL$")

    def test_release_notes_must_name_something_after_the_scheme_and_hold_no_spaces(self):
        for value in ("https://", "https://example.com/release notes", "https://example.com/\tnotes"):
            with self.subTest(value=value):
                self.assertRejected(self.mutated(lambda m: m.__setitem__("release_notes", value)),
                                    "^manifest.release_notes must be an https URL$")
        self.assertAccepted(self.mutated(lambda m: m.__setitem__("release_notes", "https://example.com")))

    def test_release_notes_must_be_a_trimmed_string(self):
        for value in ("", "   ", " https://example.com", "https://example.com ", None, 1, ["https://x"]):
            with self.subTest(value=value):
                self.assertRejected(self.mutated(lambda m: m.__setitem__("release_notes", value)),
                                    "manifest.release_notes must be a non-empty string")

    # signature

    def test_bundle_must_be_a_plain_file_name(self):
        for value in ("dir/bundle.json", "../bundle.json", "..", ".", "/abs.json", "a\\b.json", ".hidden",
                      "-dash.json", "bundle json", "a" * 202, "bündle.json"):
            with self.subTest(value=value):
                self.assertRejected(self.mutated(lambda m: m["signature"].__setitem__("bundle", value)),
                                    "^manifest.signature.bundle must be a file name, not a path$")

    def test_bundle_must_be_a_trimmed_string(self):
        for value in ("", " ", " bundle.json", "bundle.json\n", None, 1):
            with self.subTest(value=value):
                self.assertRejected(self.mutated(lambda m: m["signature"].__setitem__("bundle", value)),
                                    "manifest.signature.bundle must be a non-empty string")

    def test_identity_and_issuer_must_be_trimmed_non_empty_strings(self):
        for key in ("identity", "issuer"):
            for value in ("", "   ", " x", "x ", None, 1, ["x"]):
                with self.subTest(key=key, value=value):
                    self.assertRejected(self.mutated(lambda m: m["signature"].__setitem__(key, value)),
                                        f"manifest.signature.{key} must be a non-empty string")

    # component images

    def test_component_image_must_not_embed_a_digest(self):
        digest = COMPONENT_DIGESTS["backend"]
        self.assertRejected(
            self.mutated(lambda m: m["components"]["backend"].__setitem__(
                "image", f"docker.io/futureagi/future-agi:v1.52.0@{digest}")),
            "^manifest.components.backend.image must not embed a digest; use manifest.components.backend.digest$")

    def test_component_image_must_carry_the_version_tag(self):
        for image, shown in (("docker.io/futureagi/future-agi:v1.51.0", "'v1.51.0'"),
                             ("docker.io/futureagi/future-agi:latest", "'latest'"),
                             ("docker.io/futureagi/future-agi", "None"),
                             ("docker.io/futureagi/future-agi:v1.52.0-gpu", "'v1.52.0-gpu'"),
                             ("docker.io/futureagi/future-agi:1.52.0", "'1.52.0'"),
                             ("docker.io/futureagi/future-agi:V1.52.0", "'V1.52.0'")):
            with self.subTest(image=image):
                self.assertRejected(
                    self.mutated(lambda m: m["components"]["backend"].__setitem__("image", image)),
                    "^" + re.escape(f"manifest.components.backend.image must be tagged v1.52.0, not {shown}") + "$")

    def test_serving_gpu_needs_the_gpu_suffix(self):
        self.assertRejected(
            self.mutated(lambda m: m["components"]["serving_gpu"].__setitem__(
                "image", "docker.io/futureagi/serving:v1.52.0")),
            re.escape("manifest.components.serving_gpu.image must be tagged v1.52.0-gpu, not 'v1.52.0'"))
        self.assertRejected(
            self.mutated(lambda m: m["components"]["serving"].__setitem__(
                "image", "docker.io/futureagi/serving:v1.52.0-gpu")),
            re.escape("manifest.components.serving.image must be tagged v1.52.0, not 'v1.52.0-gpu'"))

    def test_component_image_must_name_its_own_repository(self):
        cases = (
            ("backend", "docker.io/futureagi/frontend:v1.52.0", "futureagi/future-agi", "futureagi/frontend"),
            ("backend", "ghcr.io/evil/future-agi:v1.52.0", "futureagi/future-agi", "evil/future-agi"),
            ("backend", "docker.io/futureagi/future-agi-extra:v1.52.0", "futureagi/future-agi",
             "futureagi/future-agi-extra"),
            ("backend", "ghcr.io/futureagi/future-agi/sub:v1.52.0", "futureagi/future-agi",
             "futureagi/future-agi/sub"),
            ("backend", "ghcr.io/mirror/futureagi/future-agi:v1.52.0", "futureagi/future-agi",
             "mirror/futureagi/future-agi"),
            ("standalone", "standalone:v1.52.0", "futureagi/standalone", "library/standalone"),
            ("serving_gpu", "docker.io/futureagi/serving-gpu:v1.52.0-gpu", "futureagi/serving",
             "futureagi/serving-gpu"),
            ("simulation_runner", "docker.io/futureagi/simulation-runner:v1.52.0",
             "futureagi/future-agi-simulation-runner", "futureagi/simulation-runner"),
        )
        for name, image, expected, actual in cases:
            with self.subTest(image=image):
                self.assertRejected(
                    self.mutated(lambda m: m["components"][name].__setitem__("image", image)),
                    "^" + re.escape(f"manifest.components.{name}.image must be a {expected} image, not {actual}") + "$")

    def test_unparseable_component_images_are_rejected(self):
        for image in ("docker.io/FutureAGI/future-agi:v1.52.0", "-bad.io/futureagi/future-agi:v1.52.0",
                      "docker.io/futureagi/future-agi:", "docker.io/futureagi/future-agi@sha256:abc"):
            with self.subTest(image=image):
                self.assertRejected(self.mutated(lambda m: m["components"]["backend"].__setitem__("image", image)),
                                    "invalid (repository|registry|image tag|image digest)")
        for image in ("", " docker.io/futureagi/future-agi:v1.52.0", None, 1):
            with self.subTest(image=image):
                self.assertRejected(self.mutated(lambda m: m["components"]["backend"].__setitem__("image", image)),
                                    "manifest.components.backend.image must be a non-empty string")

    def test_component_digest_must_be_sha256(self):
        for digest in ("sha256:" + "a" * 63, "sha256:" + "A" * 64, "sha512:" + "a" * 64, "a" * 64, "", None, 1,
                       " sha256:" + "a" * 64, "sha256:" + "a" * 64 + "\n"):
            with self.subTest(digest=digest):
                self.assertRejected(self.mutated(lambda m: m["components"]["frontend"].__setitem__("digest", digest)),
                                    "^manifest.components.frontend.digest must be sha256:<64 hex>$")

    def test_platforms_must_be_a_non_empty_unique_list_of_linux_platforms(self):
        for platforms in ([], "linux/amd64", ["linux/arm/v7"], ["windows/amd64"], ["linux/amd64", "linux/amd64"],
                          ["linux/arm64", "linux/amd64", "linux/arm64"], [None], ["Linux/amd64"], None,
                          {"linux/amd64": True}, [["linux/amd64"]], [{"os": "linux"}]):
            with self.subTest(platforms=platforms):
                self.assertRejected(
                    self.mutated(lambda m: m["components"]["code_executor"].__setitem__("platforms", platforms)),
                    "^manifest.components.code_executor.platforms must list linux/amd64 and/or linux/arm64$")

    # helm

    def test_helm_chart_must_be_oci(self):
        for chart in ("https://ghcr.io/future-agi/charts/futureagi", "OCI://ghcr.io/future-agi/charts/futureagi",
                      "ghcr.io/future-agi/charts/futureagi", "futureagi"):
            with self.subTest(chart=chart):
                self.assertRejected(self.mutated(lambda m: m["helm"].__setitem__("chart", chart)),
                                    "^manifest.helm.chart must be an untagged oci:// chart reference$")

    def test_helm_chart_must_be_untagged(self):
        for chart in ("oci://ghcr.io/future-agi/charts/futureagi:1.52.0",
                      "oci://ghcr.io/future-agi/charts/futureagi:latest",
                      "oci://ghcr.io/future-agi/charts/futureagi@sha256:" + "a" * 64,
                      "oci://ghcr.io:443/charts/futureagi:1.52.0"):
            with self.subTest(chart=chart):
                self.assertRejected(self.mutated(lambda m: m["helm"].__setitem__("chart", chart)),
                                    "^manifest.helm.chart must be an untagged oci:// chart reference$")

    def test_helm_chart_must_name_a_chart(self):
        self.assertRejected(self.mutated(lambda m: m["helm"].__setitem__("chart", "oci://")),
                            "manifest.helm.chart must be an untagged oci:// chart reference")

    # oci://<registry host[:port]>/<path>: a host, at least one lowercase path segment, nothing else.
    MALFORMED_CHARTS = (
        "oci://ghcr.io", "oci://ghcr.io/", "oci:///charts/futureagi", "oci://ghcr.io//charts/futureagi",
        "oci://ghcr.io/charts//futureagi", CHART + "/", "oci://ghcr.io/Future-AGI/charts/futureagi",
        "oci://-ghcr.io/charts/futureagi", "oci://ghcr-.io/charts/futureagi", "oci://ghcr..io/charts/futureagi",
        "oci://ghcr_io/charts/futureagi", "oci://ghcr.io:123456/charts/futureagi", "oci://ghcr.io:/charts/futureagi",
        "oci://user@ghcr.io/charts/futureagi", CHART + "?tag=1.52.0", CHART + "#x", "oci://ghcr.io/charts futureagi",
        "oci://ghcr.io/charts/-futureagi", "oci://ghcr.io/charts/futureagi_", "oci://ghcr.io/charts/futureagi:",
        "oci://ghcr.io/charts/futureagi@", "oci://ghcr.io/charts/füture", "oci://ghcr.io/charts\\futureagi",
    )
    VALID_CHARTS = (
        CHART, "oci://ghcr.io:443/charts/futureagi", "oci://localhost:5000/futureagi", "oci://registry/charts/futureagi",
        "oci://registry.example.com/a/b/c", "oci://GHCR.IO/future-agi/charts/futureagi",
        "oci://ghcr.io/future_agi/charts.v2/future--agi", "oci://127.0.0.1:5000/charts/futureagi",
    )

    def test_helm_chart_must_name_a_registry_host_and_a_chart_path(self):
        for chart in self.MALFORMED_CHARTS:
            with self.subTest(chart=chart):
                self.assertRejected(self.mutated(lambda m: m["helm"].__setitem__("chart", chart)),
                                    "^manifest.helm.chart must be an untagged oci:// chart reference$")

    def test_helm_chart_hosts_and_nested_paths_are_accepted(self):
        for chart in self.VALID_CHARTS:
            with self.subTest(chart=chart):
                self.assertAccepted(self.mutated(lambda m: m["helm"].__setitem__("chart", chart)))

    def test_helm_chart_must_be_a_trimmed_string(self):
        for chart in ("", " " + CHART, CHART + " ", None, 1):
            with self.subTest(chart=chart):
                self.assertRejected(self.mutated(lambda m: m["helm"].__setitem__("chart", chart)),
                                    "manifest.helm.chart must be a non-empty string")

    def test_helm_version_must_be_the_version_without_v(self):
        for value in ("v1.52.0", "1.51.0", "1.52", "1.52.0-rc.1", " 1.52.0", 1.52, None, ""):
            with self.subTest(value=value):
                self.assertRejected(self.mutated(lambda m: m["helm"].__setitem__("version", value)),
                                    r"^manifest.helm.version must be 1\.52\.0$")
        prerelease = retag(valid_manifest(channel="beta"), "v1.53.0-rc.1")
        self.assertAccepted(prerelease)
        prerelease["helm"]["version"] = "1.53.0"
        self.assertRejected(prerelease, r"manifest.helm.version must be 1\.53\.0-rc\.1")

    def test_helm_digest_must_be_sha256(self):
        for digest in ("sha256:" + "a" * 65, "sha256:" + "Z" * 64, "a" * 64, "", None):
            with self.subTest(digest=digest):
                self.assertRejected(self.mutated(lambda m: m["helm"].__setitem__("digest", digest)),
                                    "^manifest.helm.digest must be sha256:<64 hex>$")

    # deployment

    def test_deployment_files_must_be_a_non_empty_object(self):
        for files in ({}, [], ["docker-compose.yml"], "docker-compose.yml", None, 1):
            with self.subTest(files=files):
                self.assertRejected(self.mutated(lambda m: m["deployment"].__setitem__("files", files)),
                                    "^manifest.deployment.files must list the release's deployment files$")

    def test_deployment_file_paths_must_stay_inside_the_checkout(self):
        for path in ("/etc/passwd", "/docker-compose.yml", "../docker-compose.yml", "deploy/../../x", "a/../b",
                     "..", "deploy\\x.yml", "./docker-compose.yml"):
            with self.subTest(path=path):
                self.assertRejected(self.mutated(lambda m: m["deployment"].__setitem__("files", {path: "a" * 64})),
                                    "^manifest.deployment.files key must be a plain relative path inside the checkout")

    def test_deployment_file_path_must_not_be_the_checkout_itself(self):
        # "." hashes the whole checkout, .env and state included, which can never match a release.
        self.assertRejected(self.mutated(lambda m: m["deployment"].__setitem__("files", {".": "a" * 64})),
                            "manifest.deployment.files key must be a plain relative path")

    def test_deployment_file_paths_must_be_normalized(self):
        # One spelling per file: no empty, '.' or '..' segment anywhere, no trailing slash.
        for path in UNNORMALIZED_PATHS:
            with self.subTest(path=path):
                self.assertRejected(
                    self.mutated(lambda m: m["deployment"].__setitem__("files", {path: "a" * 64})),
                    "^" + re.escape(f"manifest.deployment.files key must be a plain relative path inside the "
                                    f"checkout: {path!r}") + "$")

    def test_names_with_dots_that_are_not_dot_segments_are_accepted(self):
        for path in DOTTED_PATHS:
            with self.subTest(path=path):
                self.assertAccepted(self.mutated(lambda m: m["deployment"].__setitem__("files", {path: "a" * 64})))

    def test_deployment_file_paths_must_be_trimmed_strings(self):
        for path in ("", " docker-compose.yml", "docker-compose.yml ", "   "):
            with self.subTest(path=path):
                self.assertRejected(self.mutated(lambda m: m["deployment"].__setitem__("files", {path: "a" * 64})),
                                    "manifest.deployment.files key must be a non-empty string")

    def test_deployment_file_hashes_must_be_sha256_hex(self):
        for value in ("a" * 63, "a" * 65, "A" * 64, "sha256:" + "a" * 64, "g" * 64, "", None, 1, ["a" * 64],
                      "a" * 64 + "\n"):
            with self.subTest(value=value):
                self.assertRejected(
                    self.mutated(lambda m: m["deployment"]["files"].__setitem__("docker-compose.yml", value)),
                    re.escape("manifest.deployment.files['docker-compose.yml'] must be a sha256 hex digest"))

    # migration

    def test_requires_bootstrap_must_be_boolean(self):
        for value in ("true", 1, 0, None, "yes", [True]):
            with self.subTest(value=value):
                self.assertRejected(self.mutated(lambda m: m["migration"].__setitem__("requires_bootstrap", value)),
                                    "^manifest.migration.requires_bootstrap must be true or false$")

    def test_rollback_must_be_manual_or_unsupported(self):
        for value in ("automatic", "Manual", "", None, ["manual"], True):
            with self.subTest(value=value):
                self.assertRejected(self.mutated(lambda m: m["migration"].__setitem__("rollback", value)),
                                    "^manifest.migration.rollback must be manual or unsupported$")


# ------------------------------------------------------- deployment hashing --

class DeploymentHashTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "checkout"
        self.root.mkdir()

    def write(self, root: Path, relative: str, data: bytes = b"content\n") -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def tree(self, root: Path, files: dict) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        for relative, data in files.items():
            self.write(root, relative, data)
        return root

    def test_file_hash_is_sha256_of_its_bytes(self):
        self.write(self.root, "docker-compose.yml", b"services: {}\n")
        self.assertEqual(hash_path(self.root, "docker-compose.yml"), sha256_hex(b"services: {}\n"))
        self.write(self.root, "deploy/nested.xml", b"<x/>")
        self.assertEqual(hash_path(self.root, "deploy/nested.xml"), sha256_hex(b"<x/>"))

    def missing(self, relative: str) -> str:
        empty = self.base / "empty-checkout"
        empty.mkdir(exist_ok=True)
        return hash_path(empty, relative)

    def test_missing_hash_is_fixed_and_depends_on_the_name(self):
        first = hash_path(self.root, "absent.yml")
        self.assertEqual(first, hash_path(self.root, "absent.yml"))
        self.assertNotEqual(first, hash_path(self.root, "other.yml"))
        self.assertNotEqual(first, hash_path(self.root, "deploy/absent.yml"))
        self.assertNotEqual(first, sha256_hex(b""))
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_missing_hash_does_not_depend_on_the_checkout_location(self):
        other = self.base / "elsewhere"
        other.mkdir()
        self.assertEqual(hash_path(self.root, "a/b.yml"), hash_path(other, "a/b.yml"))

    def test_file_directory_and_missing_hashes_differ(self):
        self.write(self.root, "as-file", b"payload")
        self.write(self.root, "as-dir/payload", b"payload")
        hashes = {hash_path(self.root, "as-file"), hash_path(self.root, "as-dir"), hash_path(self.root, "absent")}
        self.assertEqual(len(hashes), 3)
        # The same name as a file, a directory and missing, in three checkouts.
        file_root = self.tree(self.base / "f", {"x": b"payload"})
        dir_root = self.tree(self.base / "d", {"x/payload": b"payload"})
        missing_root = self.tree(self.base / "m", {})
        self.assertEqual(len({hash_path(file_root, "x"), hash_path(dir_root, "x"), hash_path(missing_root, "x")}), 3)

    def test_empty_file_and_empty_directory_hash_differently(self):
        # A directory replaced by an empty file (or the reverse) is drift.
        self.write(self.root, "as-file", b"")
        (self.root / "as-dir").mkdir()
        self.assertNotEqual(hash_path(self.root, "as-file"), hash_path(self.root, "as-dir"))

    def test_an_empty_directory_is_neither_missing_nor_an_empty_file(self):
        (self.root / "empty").mkdir()
        self.tree(self.root / "bytecode-only", {"__pycache__/x.cpython-39.pyc": b"\x00", "y.pyc": b"\x00"})
        empty = hash_path(self.root, "empty")
        self.assertEqual(hash_path(self.root, "bytecode-only"), empty)
        self.assertNotEqual(empty, sha256_hex(b""))
        self.assertNotEqual(empty, self.missing("empty"))

    def test_directory_hash_format_is_pinned(self):
        # Published manifests record these digests, so the format cannot drift: a type tag, then
        # 'PATH \0 sha256-hex \n' per file in byte order of the path ('-' 0x2d sorts before '/' 0x2f).
        self.tree(self.root / "catalog", {"a/b": b"2", "a-c": b"1", "z": b"", "B.yml": b"3"})
        expected = hashlib.sha256(b"futureagi-deployment-directory-v1\0")
        for name, data in (("B.yml", b"3"), ("a-c", b"1"), ("a/b", b"2"), ("z", b"")):
            expected.update(name.encode("utf-8") + b"\0" + sha256_hex(data).encode("ascii") + b"\n")
        self.assertEqual(hash_path(self.root, "catalog"), expected.hexdigest())

    def test_missing_hash_format_is_pinned(self):
        self.assertEqual(hash_path(self.root, "deploy/absent.yml"),
                         sha256_hex(b"futureagi-deployment-missing-v1\0deploy/absent.yml"))

    def test_a_file_named_like_the_untagged_missing_marker_is_not_missing(self):
        # A file holding 'missing:<name>' once hashed exactly like that name being absent.
        self.write(self.root, "x.yml", b"missing:x.yml")
        self.assertNotEqual(hash_path(self.root, "x.yml"), self.missing("x.yml"))

    def test_only_a_pycache_directory_is_skipped_not_a_file_of_that_name(self):
        self.tree(self.root / "scripts", {"tool.py": b"print(1)\n"})
        before = hash_path(self.root, "scripts")
        self.write(self.root / "scripts", "__pycache__", b"a plain file")
        self.assertNotEqual(hash_path(self.root, "scripts"), before)

    def test_directory_hash_is_stable(self):
        self.tree(self.root / "catalog", {"a.yaml": b"a", "sub/b.yaml": b"b"})
        first = hash_path(self.root, "catalog")
        self.assertEqual(first, hash_path(self.root, "catalog"))
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_directory_hash_is_independent_of_creation_order_and_location(self):
        files = {"z.yaml": b"z", "a.yaml": b"a", "m/n.yaml": b"n", "a-c": b"1", "a/b": b"2", "a.b": b"3"}
        forward = self.tree(self.base / "one" / "catalog", files)
        backward = self.tree(self.base / "two" / "deeper" / "catalog", dict(reversed(list(files.items()))))
        self.assertEqual(hash_path(forward.parent, "catalog"), hash_path(backward.parent, "catalog"))

    def test_directory_hash_is_independent_of_a_pycache_ancestor(self):
        # Only __pycache__ inside the hashed directory is ignored, not one above the checkout.
        files = {"a.yaml": b"a", "sub/b.yaml": b"b"}
        plain = self.tree(self.base / "plain" / "catalog", files)
        cached = self.tree(self.base / "__pycache__" / "checkout" / "catalog", files)
        self.assertEqual(hash_path(cached.parent, "catalog"), hash_path(plain.parent, "catalog"))

    def test_directory_hash_ignores_pycache_and_compiled_files(self):
        self.tree(self.root / "scripts", {"tool.py": b"print(1)\n", "sub/mod.py": b"x = 1\n"})
        before = hash_path(self.root, "scripts")
        for relative in ("__pycache__/tool.cpython-39.pyc", "sub/__pycache__/mod.cpython-314.pyc",
                         "__pycache__/notes.txt", "stray.pyc", "sub/stray.pyo", "sub/deeper/__pycache__/x.py"):
            with self.subTest(relative=relative):
                self.write(self.root / "scripts", relative, b"\x00compiled")
                self.assertEqual(hash_path(self.root, "scripts"), before)

    def test_directory_hash_counts_files_that_only_look_compiled(self):
        self.tree(self.root / "scripts", {"tool.py": b"print(1)\n"})
        before = hash_path(self.root, "scripts")
        for relative in ("pyc", "x.pyc.txt", "pycache/x.py", "__pycache__x.py"):
            with self.subTest(relative=relative):
                self.write(self.root / "scripts", relative, b"real")
                after = hash_path(self.root, "scripts")
                self.assertNotEqual(after, before)
                before = after

    def test_directory_hash_depends_on_content(self):
        self.tree(self.root / "catalog", {"a.yaml": b"a", "sub/b.yaml": b"b"})
        before = hash_path(self.root, "catalog")
        self.write(self.root / "catalog", "sub/b.yaml", b"B")
        self.assertNotEqual(hash_path(self.root, "catalog"), before)

    def test_directory_hash_depends_on_relative_names(self):
        base = hash_path(self.tree(self.base / "base" / "c", {"a.yaml": b"x"}).parent, "c")
        renamed = hash_path(self.tree(self.base / "renamed" / "c", {"b.yaml": b"x"}).parent, "c")
        moved = hash_path(self.tree(self.base / "moved" / "c", {"sub/a.yaml": b"x"}).parent, "c")
        swapped_a = hash_path(self.tree(self.base / "s1" / "c", {"a": b"1", "b": b"2"}).parent, "c")
        swapped_b = hash_path(self.tree(self.base / "s2" / "c", {"a": b"2", "b": b"1"}).parent, "c")
        self.assertEqual(len({base, renamed, moved}), 3)
        self.assertNotEqual(swapped_a, swapped_b)

    def test_directory_hash_does_not_depend_on_the_directory_name(self):
        one = self.tree(self.base / "r1" / "catalog", {"a": b"1"})
        two = self.tree(self.base / "r2" / "renamed", {"a": b"1"})
        self.assertEqual(hash_path(one.parent, "catalog"), hash_path(two.parent, "renamed"))

    def test_adding_or_removing_a_file_changes_the_directory_hash(self):
        self.tree(self.root / "catalog", {"a": b"1"})
        before = hash_path(self.root, "catalog")
        self.write(self.root / "catalog", "b", b"")
        added = hash_path(self.root, "catalog")
        self.assertNotEqual(added, before)
        (self.root / "catalog" / "b").unlink()
        self.assertEqual(hash_path(self.root, "catalog"), before)

    def test_empty_subdirectories_do_not_change_the_hash(self):
        # git does not track empty directories, so a release checkout cannot record them.
        self.tree(self.root / "catalog", {"a": b"1"})
        before = hash_path(self.root, "catalog")
        (self.root / "catalog" / "empty" / "deeper").mkdir(parents=True)
        self.assertEqual(hash_path(self.root, "catalog"), before)

    def test_symlinks_hash_as_their_target_and_dangling_links_as_missing(self):
        target = self.write(self.root, "real.yml", b"real")
        (self.root / "link.yml").symlink_to(target)
        self.assertEqual(hash_path(self.root, "link.yml"), sha256_hex(b"real"))
        (self.root / "dangling.yml").symlink_to(self.root / "nowhere.yml")
        self.assertEqual(hash_path(self.root, "dangling.yml"), self.missing("dangling.yml"))

    def test_deployment_hashes_maps_every_path(self):
        self.write(self.root, "docker-compose.yml", b"x")
        self.tree(self.root / "catalog", {"a": b"1"})
        paths = ["docker-compose.yml", "catalog", "absent.yml"]
        result = deployment_hashes(self.root, paths)
        self.assertEqual(list(result), paths)
        self.assertEqual(result, {path: hash_path(self.root, path) for path in paths})
        self.assertEqual(deployment_hashes(self.root, {"catalog": "ignored"}), {"catalog": hash_path(self.root, "catalog")})
        self.assertEqual(deployment_hashes(self.root, []), {})


class DeploymentFileListTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "deploy" / "update").mkdir(parents=True)
        self.list_file = self.root / DEPLOYMENT_FILE_LIST

    def test_reads_entries_skipping_comments_and_blank_lines(self):
        self.list_file.write_text(
            "# header comment\n\n  docker-compose.yml  \n\t\ndeploy/x.yml # inline comment\n"
            "   # indented comment\nfutureagi/scripts/property_catalog_oss\n.env.example",
            encoding="utf-8")
        self.assertEqual(read_deployment_file_list(self.root),
                         ["docker-compose.yml", "deploy/x.yml", "futureagi/scripts/property_catalog_oss",
                          ".env.example"])

    def test_invalid_entries_are_rejected(self):
        for entry in ("/etc/passwd", "../outside.yml", "deploy/../../x", "a/../b", "..", "deploy\\x.yml",
                      "./docker-compose.yml"):
            with self.subTest(entry=entry):
                self.list_file.write_text(f"docker-compose.yml\n{entry}\n", encoding="utf-8")
                with self.assertRaisesRegex(ManifestError, "^" + re.escape(
                        f"{DEPLOYMENT_FILE_LIST} must be a plain relative path inside the checkout: {entry!r}") + "$"):
                    read_deployment_file_list(self.root)

    def test_checkout_root_is_not_a_deployment_file(self):
        self.list_file.write_text("docker-compose.yml\n.\n", encoding="utf-8")
        with self.assertRaisesRegex(ManifestError, "plain relative path"):
            read_deployment_file_list(self.root)

    def test_entries_must_be_normalized(self):
        for entry in UNNORMALIZED_PATHS:
            with self.subTest(entry=entry):
                self.list_file.write_text(f"docker-compose.yml\n{entry}\n", encoding="utf-8")
                with self.assertRaisesRegex(ManifestError, "^" + re.escape(
                        f"{DEPLOYMENT_FILE_LIST} must be a plain relative path inside the checkout: {entry!r}") + "$"):
                    read_deployment_file_list(self.root)

    def test_names_with_dots_are_entries(self):
        self.list_file.write_text("\n".join(DOTTED_PATHS) + "\n", encoding="utf-8")
        self.assertEqual(read_deployment_file_list(self.root), list(DOTTED_PATHS))

    def test_empty_list_is_rejected(self):
        for text in ("", "\n\n", "# only a comment\n   # and another\n", "   \n\t\n"):
            with self.subTest(text=text):
                self.list_file.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(ManifestError, "^" + re.escape(f"{DEPLOYMENT_FILE_LIST} lists no files")):
                    read_deployment_file_list(self.root)

    def test_missing_or_unreadable_list_is_rejected(self):
        with self.assertRaisesRegex(ManifestError, "^" + re.escape(f"cannot read {DEPLOYMENT_FILE_LIST}: ")):
            read_deployment_file_list(self.root)
        self.list_file.mkdir()
        with self.assertRaisesRegex(ManifestError, "^" + re.escape(f"cannot read {DEPLOYMENT_FILE_LIST}: ")):
            read_deployment_file_list(self.root)

    def test_checked_in_list_is_valid(self):
        entries = read_deployment_file_list(ROOT)
        self.assertIn("docker-compose.yml", entries)
        self.assertEqual(len(entries), len(set(entries)), "duplicate deployment-files.txt entries")
        manifest = valid_manifest()
        manifest["deployment"]["files"] = deployment_hashes(ROOT, entries)
        validate_manifest(manifest)


class UpgradeFloorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "deploy" / "update").mkdir(parents=True)
        self.floor = self.root / UPGRADE_FLOOR_FILE

    def test_reads_the_single_version(self):
        for text in ("v1.40.0\n", "# why\n# more\nv1.40.0\n\n", "  v1.40.0  # inline\n", "v1.40.0"):
            with self.subTest(text=text):
                self.floor.write_text(text, encoding="utf-8")
                self.assertEqual(read_upgrade_floor(self.root), "v1.40.0")

    def test_prerelease_floor_is_a_version(self):
        self.floor.write_text("v1.40.0-rc.1\n", encoding="utf-8")
        self.assertEqual(read_upgrade_floor(self.root), "v1.40.0-rc.1")

    def test_requires_exactly_one_version(self):
        for text in ("", "# nothing\n", "v1.40.0\nv1.41.0\n", "v1.40.0\n# c\nv1.40.0\n"):
            with self.subTest(text=text):
                self.floor.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(ManifestError, "^" + re.escape(
                        f"{UPGRADE_FLOOR_FILE} must contain exactly one version") + "$"):
                    read_upgrade_floor(self.root)

    def test_invalid_version_is_rejected(self):
        for text in ("1.40.0\n", "v1.40\n", "latest\n", "v1.40.0 v1.41.0\n", "v01.40.0\n"):
            with self.subTest(text=text):
                self.floor.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(ManifestError, "invalid version"):
                    read_upgrade_floor(self.root)

    def test_missing_floor_is_rejected(self):
        with self.assertRaisesRegex(ManifestError, "^" + re.escape(f"cannot read {UPGRADE_FLOOR_FILE}: ")):
            read_upgrade_floor(self.root)

    def test_checked_in_floor_is_a_release(self):
        floor = read_upgrade_floor(ROOT)
        self.assertTrue(is_version(floor))
        self.assertFalse(is_prerelease(floor))


# -------------------------------------------------------------- generator CLI --

def component_args(version: str = VERSION, overrides: dict = None, skip=()) -> list:
    args = []
    for name, repository in EXPECTED_REPOSITORIES.items():
        if name in skip:
            continue
        ref = f"{name}=docker.io/{repository}:{tag_for(name, version)}@{COMPONENT_DIGESTS[name]}"
        args += ["--component", (overrides or {}).get(name, ref)]
    return args


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.repo = self.base / "release-checkout"
        (self.repo / "deploy" / "update").mkdir(parents=True)
        (self.repo / DEPLOYMENT_FILE_LIST).write_text(
            "# release deployment files\ndocker-compose.yml\nconfig\nabsent.yml # recorded as missing\n",
            encoding="utf-8")
        (self.repo / UPGRADE_FLOOR_FILE).write_text("# floor\nv1.40.0\n", encoding="utf-8")
        (self.repo / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        (self.repo / "config").mkdir()
        (self.repo / "config" / "a.yml").write_text("a: 1\n", encoding="utf-8")
        # A release is generated from a git checkout of its tag; without one the generator warns (round 4, S51).
        make_git_checkout(self.repo)
        self.workdir = self.base / "cwd"
        self.workdir.mkdir()
        self.env = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "PYTHONHOME")}
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"

    def run_generator(self, *args, version=VERSION, components=None, repo_root=True, base_args=True):
        argv = [sys.executable, str(GENERATOR)]
        if base_args:
            argv += ["--version", version, "--source-sha", SOURCE_SHA, "--published-at", PUBLISHED_AT,
                     "--helm-chart", CHART, "--helm-digest", HELM_DIGEST]
            argv += component_args(version) if components is None else components
        if repo_root:
            argv += ["--repo-root", str(self.repo)]
        argv += list(args)
        return subprocess.run(argv, cwd=self.workdir, env=self.env, capture_output=True, text=True, timeout=60)

    def generate(self, *args, **kwargs) -> dict:
        result = self.run_generator(*args, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def assertGeneratorFails(self, result, message):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        self.assertTrue(result.stderr.startswith("generate_update_manifest: "), result.stderr)
        self.assertRegex(result.stderr, message)

    def test_writes_a_valid_manifest_to_stdout(self):
        manifest = self.generate()
        self.assertEqual(validate_manifest(manifest), manifest)
        self.assertEqual(manifest["schema_version"], 2)
        self.assertFalse(manifest["compatibility"]["automatic_apply"])
        self.assertEqual(manifest["compatibility"]["automatic_rollback"]["strategy"], "never")
        self.assertIsNone(manifest["compatibility"]["validation"])
        self.assertEqual(manifest["product"], "future-agi")
        self.assertEqual(manifest["channel"], "stable")
        self.assertEqual(manifest["version"], VERSION)
        self.assertEqual(manifest["source_sha"], SOURCE_SHA)
        self.assertEqual(manifest["published_at"], PUBLISHED_AT)
        self.assertEqual(manifest["minimum_version"], "v1.40.0")
        self.assertEqual(manifest["release_notes"], f"https://github.com/future-agi/future-agi/releases/tag/{VERSION}")
        self.assertEqual(manifest["signature"], {"bundle": BUNDLE_ASSET})
        self.assertEqual(manifest["helm"], {"chart": CHART, "version": "1.52.0", "digest": HELM_DIGEST})
        self.assertEqual(manifest["migration"], {"requires_bootstrap": True, "rollback": "manual"})
        self.assertEqual(set(manifest["components"]), set(EXPECTED_REPOSITORIES))
        for name, repository in EXPECTED_REPOSITORIES.items():
            with self.subTest(name=name):
                component = manifest["components"][name]
                self.assertEqual(component["image"], f"docker.io/{repository}:{tag_for(name)}")
                self.assertEqual(component["digest"], COMPONENT_DIGESTS[name])

    def test_platforms_per_component(self):
        components = self.generate()["components"]
        for name in EXPECTED_REPOSITORIES:
            with self.subTest(name=name):
                expected = ["linux/amd64", "linux/arm64"] if name in MULTI_ARCH else ["linux/amd64"]
                self.assertEqual(components[name]["platforms"], expected)

    def test_output_is_sorted_indented_json_with_a_trailing_newline(self):
        result = self.run_generator()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, json.dumps(json.loads(result.stdout), sort_keys=True, indent=2) + "\n")

    def test_deployment_files_are_hashed_from_the_repo_root(self):
        files = self.generate()["deployment"]["files"]
        self.assertEqual(list(files), ["absent.yml", "config", "docker-compose.yml"])
        self.assertEqual(files["docker-compose.yml"], sha256_hex(b"services: {}\n"))
        self.assertEqual(files["config"], hash_path(self.repo, "config"))
        empty = self.base / "empty-checkout"
        empty.mkdir()
        self.assertEqual(files["absent.yml"], hash_path(empty, "absent.yml"))

    def test_output_file_bytes_are_deterministic(self):
        first, second = self.base / "first.json", self.base / "second.json"
        for path in (first, second):
            result = self.run_generator("--output", str(path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(first.read_bytes().decode("utf-8"), self.run_generator().stdout)
        self.assertTrue(first.read_bytes().endswith(b"}\n"))

    def test_component_order_does_not_change_the_output(self):
        forward = self.run_generator().stdout
        pairs = component_args()
        reversed_args = [item for pair in reversed(list(zip(pairs[::2], pairs[1::2]))) for item in pair]
        self.assertEqual(self.run_generator(components=reversed_args).stdout, forward)

    def test_default_repo_root_is_the_generators_checkout(self):
        result = self.run_generator(repo_root=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        # An exported source tree is no git checkout: the tracked-file check is skipped with a warning (S51).
        self.assertEqual(result.stderr, "" if (ROOT / ".git").exists() else not_a_git_checkout_warning(ROOT))
        manifest = json.loads(result.stdout)
        self.assertEqual(manifest["minimum_version"], read_upgrade_floor(ROOT))
        entries = read_deployment_file_list(ROOT)
        self.assertEqual(manifest["deployment"]["files"], deployment_hashes(ROOT, entries))

    def test_signature_identity_adds_identity_and_default_issuer(self):
        identity = ("https://github.com/future-agi/future-agi/.github/workflows/"
                    f"release-update-manifest.yml@refs/tags/{VERSION}")
        manifest = self.generate("--signature-identity", identity)
        self.assertEqual(manifest["signature"], {"bundle": BUNDLE_ASSET, "identity": identity, "issuer": ISSUER})
        custom = self.generate("--signature-identity", identity, "--signature-issuer", "https://issuer.example")
        self.assertEqual(custom["signature"]["issuer"], "https://issuer.example")

    def test_empty_signature_issuer_is_rejected(self):
        result = self.run_generator("--signature-identity", "https://id.example", "--signature-issuer", "")
        self.assertGeneratorFails(result, "manifest.signature.issuer must be a non-empty string")

    def test_bundle_name_option(self):
        self.assertEqual(self.generate("--bundle-name", "custom.sigstore.json")["signature"]["bundle"],
                         "custom.sigstore.json")
        self.assertGeneratorFails(self.run_generator("--bundle-name", "dir/x.json"),
                                  "manifest.signature.bundle must be a file name, not a path")

    def test_beta_prerelease(self):
        version = "v1.53.0-rc.1"
        manifest = self.generate("--channel", "beta", version=version, components=component_args(version))
        self.assertEqual(manifest["channel"], "beta")
        self.assertEqual(manifest["helm"]["version"], "1.53.0-rc.1")
        self.assertEqual(manifest["components"]["serving_gpu"]["image"], f"docker.io/futureagi/serving:{version}-gpu")
        self.assertEqual(manifest["release_notes"], f"https://github.com/future-agi/future-agi/releases/tag/{version}")

    def test_prerelease_on_the_default_stable_channel_is_rejected(self):
        version = "v1.53.0-rc.1"
        self.assertGeneratorFails(self.run_generator(version=version, components=component_args(version)),
                                  "stable manifest carries a prerelease version v1.53.0-rc.1")

    def test_unknown_channel_is_an_argument_error(self):
        result = self.run_generator("--channel", "nightly")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)

    def test_every_missing_component_is_reported(self):
        for name in EXPECTED_REPOSITORIES:
            with self.subTest(name=name):
                result = self.run_generator(components=component_args(skip=(name,)))
                self.assertGeneratorFails(result, f"^generate_update_manifest: missing --component for {name}\n$")

    def test_all_missing_components_are_listed(self):
        result = self.run_generator(components=[])
        self.assertGeneratorFails(
            result, "missing --component for " + re.escape(", ".join(sorted(EXPECTED_REPOSITORIES))))

    def test_duplicate_component_is_rejected(self):
        duplicate = component_args() + ["--component", component_args()[1]]
        self.assertGeneratorFails(self.run_generator(components=duplicate),
                                  "^generate_update_manifest: --component standalone given twice\n$")

    def test_malformed_component_is_rejected(self):
        for item in ("standalone", "standalone=docker.io/futureagi/standalone:v1.52.0", "standalone=",
                     "docker.io/futureagi/standalone:v1.52.0@" + COMPONENT_DIGESTS["standalone"]):
            with self.subTest(item=item):
                result = self.run_generator(components=component_args(overrides={"standalone": item}))
                self.assertGeneratorFails(
                    result, re.escape(f"--component must be NAME=IMAGE:TAG@sha256:DIGEST, not {item!r}"))

    def test_unknown_component_name_is_rejected(self):
        extra = component_args() + ["--component", "bogus=docker.io/futureagi/bogus:v1.52.0@" + HELM_DIGEST]
        self.assertGeneratorFails(self.run_generator(components=extra),
                                  r"manifest.components has unknown field\(s\) bogus")

    def test_latest_tag_is_rejected(self):
        item = "standalone=docker.io/futureagi/standalone:latest@" + COMPONENT_DIGESTS["standalone"]
        self.assertGeneratorFails(
            self.run_generator(components=component_args(overrides={"standalone": item})),
            re.escape("manifest.components.standalone.image must be tagged v1.52.0, not 'latest'"))

    def test_untagged_and_wrongly_tagged_components_are_rejected(self):
        cases = {
            "standalone=docker.io/futureagi/standalone@" + COMPONENT_DIGESTS["standalone"]: "not None",
            "standalone=docker.io/futureagi/standalone:v1.51.0@" + COMPONENT_DIGESTS["standalone"]: "not 'v1.51.0'",
            "serving_gpu=docker.io/futureagi/serving:v1.52.0@" + COMPONENT_DIGESTS["serving_gpu"]:
                "serving_gpu.image must be tagged v1.52.0-gpu",
        }
        for item, message in cases.items():
            name = item.split("=", 1)[0]
            with self.subTest(item=item):
                self.assertGeneratorFails(
                    self.run_generator(components=component_args(overrides={name: item})), re.escape(message))

    def test_wrong_repository_is_rejected(self):
        item = "backend=docker.io/futureagi/frontend:v1.52.0@" + COMPONENT_DIGESTS["backend"]
        self.assertGeneratorFails(
            self.run_generator(components=component_args(overrides={"backend": item})),
            re.escape("manifest.components.backend.image must be a futureagi/future-agi image, not futureagi/frontend"))

    def test_bad_component_digest_is_rejected(self):
        item = "frontend=docker.io/futureagi/frontend:v1.52.0@sha256:" + "A" * 64
        self.assertGeneratorFails(self.run_generator(components=component_args(overrides={"frontend": item})),
                                  re.escape("manifest.components.frontend.digest must be sha256:<64 hex>"))

    def test_minimum_version_override(self):
        for minimum in ("v1.30.0", "v1.45.0", VERSION):
            with self.subTest(minimum=minimum):
                self.assertEqual(self.generate("--minimum-version", minimum)["minimum_version"], minimum)

    def test_minimum_version_newer_than_version_is_rejected(self):
        self.assertGeneratorFails(self.run_generator("--minimum-version", "v1.53.0"),
                                  re.escape(f"manifest.minimum_version v1.53.0 is newer than its version {VERSION}"))

    def test_upgrade_floor_newer_than_version_is_rejected(self):
        (self.repo / UPGRADE_FLOOR_FILE).write_text("v2.0.0\n", encoding="utf-8")
        self.assertGeneratorFails(self.run_generator(), "manifest.minimum_version v2.0.0 is newer than its version")

    def test_invalid_minimum_version_override_is_rejected(self):
        self.assertGeneratorFails(self.run_generator("--minimum-version", "1.40.0"), "invalid version")

    def test_minimum_version_override_does_not_need_the_upgrade_floor(self):
        (self.repo / UPGRADE_FLOOR_FILE).unlink()
        self.assertEqual(self.generate("--minimum-version", "v1.41.0")["minimum_version"], "v1.41.0")
        self.assertGeneratorFails(self.run_generator(),
                                  "^" + re.escape(f"generate_update_manifest: cannot read {UPGRADE_FLOOR_FILE}: "))

    def test_invalid_upgrade_floor_is_rejected(self):
        (self.repo / UPGRADE_FLOOR_FILE).write_text("v1.40.0\nv1.41.0\n", encoding="utf-8")
        self.assertGeneratorFails(self.run_generator(), re.escape(f"{UPGRADE_FLOOR_FILE} must contain exactly one version"))

    def test_missing_or_invalid_deployment_file_list_is_rejected(self):
        (self.repo / DEPLOYMENT_FILE_LIST).write_text("../outside\n", encoding="utf-8")
        self.assertGeneratorFails(self.run_generator(), "must be a plain relative path inside the checkout")
        (self.repo / DEPLOYMENT_FILE_LIST).unlink()
        self.assertGeneratorFails(self.run_generator(), re.escape(f"cannot read {DEPLOYMENT_FILE_LIST}"))

    def test_invalid_release_arguments_are_rejected(self):
        cases = (
            (("--source-sha", "abc"), "manifest.source_sha must be a 40-character commit SHA"),
            (("--published-at", "2026-10-19"), "manifest.published_at must be a UTC timestamp"),
            (("--helm-chart", CHART + ":1.52.0"), "manifest.helm.chart must be an untagged oci:// chart reference"),
            (("--helm-chart", "https://ghcr.io/x"), "manifest.helm.chart must be an untagged oci:// chart reference"),
            (("--helm-digest", "sha256:abc"), re.escape("manifest.helm.digest must be sha256:<64 hex>")),
        )
        for args, message in cases:
            with self.subTest(args=args):
                self.assertGeneratorFails(self.run_generator(*args), message)

    def test_version_without_v_is_rejected(self):
        self.assertGeneratorFails(self.run_generator(version="1.52.0", components=component_args("1.52.0")),
                                  "invalid version '1.52.0'")

    def test_failure_writes_no_output_file(self):
        output = self.base / "out.json"
        result = self.run_generator("--output", str(output), components=component_args(skip=("backend",)))
        self.assertGeneratorFails(result, "missing --component for backend")
        self.assertFalse(output.exists())

    def test_missing_required_arguments_are_argument_errors(self):
        for flag in ("--version", "--source-sha", "--published-at", "--helm-chart", "--helm-digest"):
            with self.subTest(flag=flag):
                argv = ["--version", VERSION, "--source-sha", SOURCE_SHA, "--published-at", PUBLISHED_AT,
                        "--helm-chart", CHART, "--helm-digest", HELM_DIGEST]
                index = argv.index(flag)
                del argv[index:index + 2]
                result = self.run_generator(*argv, *component_args(), base_args=False)
                self.assertEqual(result.returncode, 2)
                self.assertIn("required", result.stderr)
                self.assertIn(flag, result.stderr)

    def test_generated_manifest_validates_against_the_schema(self):
        validator = load_schema_validator(self)
        for args, version in (((), VERSION), (("--channel", "beta", "--signature-identity", "https://id.example"),
                                              "v1.53.0-beta.2")):
            with self.subTest(version=version):
                manifest = self.generate(*args, version=version, components=component_args(version))
                self.assertEqual([error.message for error in validator.iter_errors(manifest)], [])

    def test_a_component_with_two_digests_is_a_clean_error(self):
        # Only the last '@' separates the digest: the image part then names a digest of its own.
        ref = f"backend=docker.io/futureagi/future-agi:{VERSION}@{COMPONENT_DIGESTS['backend']}@{HELM_DIGEST}"
        result = self.run_generator(components=component_args(overrides={"backend": ref}))
        self.assertGeneratorFails(result, "^generate_update_manifest: manifest.components.backend.image must not "
                                          "embed a digest; use manifest.components.backend.digest\n$")

    def test_a_deploy_package_on_pythonpath_does_not_shadow_the_checkouts(self):
        # The release workflow runs the generator with whatever python3 it has: the checkout's manifest rules win.
        shadow = self.base / "shadow"
        (shadow / "deploy" / "update").mkdir(parents=True)
        for package in (shadow / "deploy", shadow / "deploy" / "update"):
            (package / "__init__.py").write_text("", encoding="utf-8")
        (shadow / "deploy" / "update" / "manifest.py").write_text("raise ImportError('shadowed')\n", encoding="utf-8")
        self.env["PYTHONPATH"] = str(shadow)
        self.assertEqual(self.generate()["version"], VERSION)

    def test_main_returns_0_after_writing_the_manifest(self):
        spec = importlib.util.spec_from_file_location("generate_update_manifest_main", GENERATOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        output = self.base / "manifest.json"
        argv = ["--version", VERSION, "--source-sha", SOURCE_SHA, "--published-at", PUBLISHED_AT, "--helm-chart",
                CHART, "--helm-digest", HELM_DIGEST, *component_args(), "--repo-root", str(self.repo),
                "--output", str(output)]
        self.assertEqual(module.main(argv), 0)
        self.assertEqual(validate_manifest(json.loads(output.read_text(encoding="utf-8")))["version"], VERSION)

    def test_a_git_that_hangs_is_a_manifest_error(self):
        spec = importlib.util.spec_from_file_location("generate_update_manifest_hang", GENERATOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        hang = subprocess.TimeoutExpired(["git"], 300)
        with mock.patch.object(module.subprocess, "run", side_effect=hang) as run:
            with self.assertRaisesRegex(ManifestError, "^" + re.escape(
                    f"cannot run git to list the files it tracks in {self.repo}: {hang}") + "$"):
                module.tracked_files(self.repo, ["config"])
        self.assertGreater(run.call_args.kwargs["timeout"], 0)


# --------------------------------------------------------------- JSON Schema --

def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def load_schema_validator(test: unittest.TestCase):
    try:
        import jsonschema  # noqa: PLC0415 - optional dependency
    except ImportError:
        test.skipTest("optional 'jsonschema' module is not installed")
    schema = load_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def validator_accepts(manifest) -> bool:
    try:
        validate_manifest(manifest)
    except ManifestError:
        return False
    return True


def _delete(*keys):
    def mutate(m):
        target = m
        for key in keys[:-1]:
            target = target[key]
        del target[keys[-1]]
    return mutate


def _set(*keys_and_value):
    *keys, value = keys_and_value

    def mutate(m):
        target = m
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = copy.deepcopy(value)
    return mutate


def _retag(version, channel="stable"):
    def mutate(m):
        m["channel"] = channel
        retag(m, version)
    return mutate


# Single mutations both the validator and the schema must reject: every
# validator rule JSON Schema expresses and the schema already states.
SCHEMA_COVERED_MUTATIONS = (
    [(f"missing {key}", _delete(key)) for key in TOP_LEVEL_REQUIRED]
    + [(f"missing component {name}", _delete("components", name)) for name in EXPECTED_REPOSITORIES]
    + [
        ("unknown top-level key", _set("extra", 1)),
        ("schema_version 2", _set("schema_version", 2)),
        ("schema_version string", _set("schema_version", "1")),
        ("product", _set("product", "other")),
        ("channel", _set("channel", "nightly")),
        ("version without v", _retag("1.52.0")),
        ("version with leading zero", _retag("v1.052.0")),
        ("source_sha short", _set("source_sha", "a" * 39)),
        ("source_sha uppercase", _set("source_sha", "A" * 40)),
        ("published_at with offset", _set("published_at", "2026-10-19T00:00:00+00:00")),
        ("minimum_version malformed", _set("minimum_version", "1.51.0")),
        ("release_notes http", _set("release_notes", "http://example.com")),
        ("release_notes non-string", _set("release_notes", 1)),
        ("signature not an object", _set("signature", [])),
        ("signature unknown key", _set("signature", "extra", "x")),
        ("signature missing bundle", _delete("signature", "bundle")),
        ("bundle with a path", _set("signature", "bundle", "dir/bundle.json")),
        ("bundle parent", _set("signature", "bundle", "../bundle.json")),
        ("bundle absolute", _set("signature", "bundle", "/bundle.json")),
        ("bundle too long", _set("signature", "bundle", "a" * 202)),
        ("identity empty", _set("signature", "identity", "")),
        ("issuer empty", _set("signature", "issuer", "")),
        ("identity non-string", _set("signature", "identity", 1)),
        ("components not an object", _set("components", [])),
        ("unknown component", _set("components", "serving_tpu", {"image": "a/b:v1.52.0", "digest": HELM_DIGEST})),
        ("component not an object", _set("components", "backend", "docker.io/futureagi/future-agi:v1.52.0")),
        ("component unknown key", _set("components", "backend", "extra", 1)),
        ("component missing image", _delete("components", "backend", "image")),
        ("component missing digest", _delete("components", "backend", "digest")),
        ("image with digest", _set("components", "backend", "image",
                                   "docker.io/futureagi/future-agi:v1.52.0@" + HELM_DIGEST)),
        ("image untagged", _set("components", "backend", "image", "docker.io/futureagi/future-agi")),
        ("image with whitespace", _set("components", "backend", "image", "docker.io/futureagi/future agi:v1.52.0")),
        ("component digest short", _set("components", "backend", "digest", "sha256:abc")),
        ("component digest uppercase", _set("components", "backend", "digest", "sha256:" + "A" * 64)),
        ("platforms empty", _set("components", "backend", "platforms", [])),
        ("platforms duplicate", _set("components", "backend", "platforms", ["linux/amd64", "linux/amd64"])),
        ("platforms invalid", _set("components", "backend", "platforms", ["linux/arm/v7"])),
        ("platforms not a list", _set("components", "backend", "platforms", "linux/amd64")),
        ("helm not an object", _set("helm", "chart")),
        ("helm unknown key", _set("helm", "extra", 1)),
        ("helm missing chart", _delete("helm", "chart")),
        ("helm missing version", _delete("helm", "version")),
        ("helm missing digest", _delete("helm", "digest")),
        ("helm chart not oci", _set("helm", "chart", "https://ghcr.io/future-agi/charts/futureagi")),
        ("helm version with v", _set("helm", "version", "v1.52.0")),
        ("helm digest bad", _set("helm", "digest", "sha256:abc")),
        ("deployment not an object", _set("deployment", [])),
        ("deployment unknown key", _set("deployment", "extra", 1)),
        ("deployment missing files", _delete("deployment", "files")),
        ("deployment files empty", _set("deployment", "files", {})),
        ("deployment files list", _set("deployment", "files", ["docker-compose.yml"])),
        ("deployment hash short", _set("deployment", "files", "docker-compose.yml", "a" * 63)),
        ("deployment hash uppercase", _set("deployment", "files", "docker-compose.yml", "A" * 64)),
        ("deployment hash non-string", _set("deployment", "files", "docker-compose.yml", 1)),
        ("migration not an object", _set("migration", None)),
        ("migration unknown key", _set("migration", "extra", 1)),
        ("migration missing requires_bootstrap", _delete("migration", "requires_bootstrap")),
        ("migration missing rollback", _delete("migration", "rollback")),
        ("requires_bootstrap string", _set("migration", "requires_bootstrap", "true")),
        ("requires_bootstrap integer", _set("migration", "requires_bootstrap", 1)),
        ("rollback unknown", _set("migration", "rollback", "automatic")),
        ("schema_version true", _set("schema_version", True)),
        ("source_sha integer", _set("source_sha", int("1" * 40))),
        ("published_at integer", _set("published_at", 20261019000000)),
        ("published_at with fullwidth digits", _set("published_at", "２０２６-10-19T00:00:00Z")),
        ("version with a non-ASCII digit", _retag("v1.5٢.0")),
        ("minimum_version with a non-ASCII digit", _set("minimum_version", "v0.1٣.0")),
        ("stable beta", _retag("v1.53.0-beta.1")),
        ("stable rc", _retag("v1.53.0-rc.0")),
        ("release_notes scheme only", _set("release_notes", "https://")),
        ("release_notes with a space", _set("release_notes", "https://example.com/release notes")),
        ("identity blank", _set("signature", "identity", "   ")),
        ("issuer padded", _set("signature", "issuer", " " + ISSUER)),
        ("image of another component", _set("components", "backend", "image", "docker.io/futureagi/frontend:v1.52.0")),
        ("image under a path prefix", _set("components", "backend", "image", "evil/futureagi/future-agi:v1.52.0")),
        ("image on an invalid registry", _set("components", "backend", "image",
                                              "bad_host.io/futureagi/future-agi:v1.52.0")),
        ("image tagged latest", _set("components", "backend", "image", "docker.io/futureagi/future-agi:latest")),
        ("serving tagged -gpu", _set("components", "serving", "image", "docker.io/futureagi/serving:v1.52.0-gpu")),
        ("serving_gpu without -gpu", _set("components", "serving_gpu", "image", "docker.io/futureagi/serving:v1.52.0")),
        ("helm chart scheme only", _set("helm", "chart", "oci://")),
        ("helm chart without a path", _set("helm", "chart", "oci://ghcr.io")),
        ("helm chart with a trailing slash", _set("helm", "chart", CHART + "/")),
        ("helm chart with uppercase path", _set("helm", "chart", "oci://ghcr.io/Future-AGI/charts/futureagi")),
        ("helm chart tagged", _set("helm", "chart", CHART + ":1.52.0")),
        ("helm version with a leading zero", _set("helm", "version", "01.52.0")),
        ("helm digest integer", _set("helm", "digest", int("1" * 64))),
        ("deployment path '.'", _set("deployment", "files", {".": "a" * 64})),
        ("deployment path 'a//b'", _set("deployment", "files", {"a//b": "a" * 64})),
        ("deployment path with a trailing slash", _set("deployment", "files", {"deploy/": "a" * 64})),
        ("deployment path 'a/./b'", _set("deployment", "files", {"a/./b": "a" * 64})),
        ("deployment path padded", _set("deployment", "files", {" docker-compose.yml": "a" * 64})),
        ("deployment path empty", _set("deployment", "files", {"": "a" * 64})),
    ]
)

# Manifests the validator accepts; the schema must accept them too.
VALIDATOR_ACCEPTED_VARIANTS = (
    ("as generated", lambda m: None),
    ("no optional fields", lambda m: (m.pop("release_notes"), m["signature"].pop("identity"),
                                      m["signature"].pop("issuer"),
                                      [c.pop("platforms") for c in m["components"].values()])),
    ("beta prerelease", _retag("v1.53.0-beta.1", "beta")),
    ("beta rc", _retag("v1.53.0-rc.4", "beta")),
    ("docker hub short name", _set("components", "backend", "image", "futureagi/future-agi:v1.52.0")),
    ("index.docker.io", _set("components", "backend", "image", "index.docker.io/futureagi/future-agi:v1.52.0")),
    ("registry with port", _set("components", "backend", "image", "localhost:5000/futureagi/future-agi:v1.52.0")),
    ("other registry host", _set("components", "backend", "image", "ghcr.io/futureagi/future-agi:v1.52.0")),
    ("single platform", _set("components", "backend", "platforms", ["linux/arm64"])),
    ("helm chart on a port", _set("helm", "chart", "oci://ghcr.io:443/charts/futureagi")),
    ("rollback unsupported", _set("migration", "rollback", "unsupported")),
    ("requires_bootstrap false", _set("migration", "requires_bootstrap", False)),
    ("nested deployment path", _set("deployment", "files", {"deploy/clickhouse/users.d/x.xml": "d" * 64})),
    ("longest bundle", _set("signature", "bundle", "a" * 201)),
    ("beta channel release", _retag("v1.53.0", "beta")),
    ("serving_gpu on a mirror", _set("components", "serving_gpu", "image",
                                     "mirror.example.com:8443/futureagi/serving:v1.52.0-gpu")),
    ("uppercase mirror host", _set("components", "backend", "image", "MIRROR.example.com/futureagi/future-agi:v1.52.0")),
    ("registry-1.docker.io", _set("components", "backend", "image", "registry-1.docker.io/futureagi/future-agi:v1.52.0")),
    ("localhost without a port", _set("components", "backend", "image", "localhost/futureagi/future-agi:v1.52.0")),
    ("single-label host with a port", _set("components", "backend", "image", "myhost:5000/futureagi/future-agi:v1.52.0")),
    ("helm chart on localhost", _set("helm", "chart", "oci://localhost:5000/charts/futureagi")),
    ("helm chart on a single-label host", _set("helm", "chart", "oci://registry/charts/futureagi")),
    ("helm chart nested path", _set("helm", "chart", "oci://registry.example.com/a/b/c")),
    ("deployment names with dots", _set("deployment", "files", {path: "a" * 64 for path in DOTTED_PATHS})),
    ("identity with inner spaces", _set("signature", "identity", "signed by the release workflow")),
    ("release notes with a query", _set("release_notes", "https://example.com/notes?version=v1.52.0#top")),
)


class SchemaStructureTests(unittest.TestCase):
    """Checks that need no jsonschema: the schema states the validator's contract."""

    def setUp(self):
        self.schema = load_schema()

    def test_schema_is_draft_2020_12(self):
        self.assertEqual(self.schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(self.schema["type"], "object")

    def test_top_level_keys_match_the_validator(self):
        self.assertEqual(set(self.schema["required"]), set(TOP_LEVEL_REQUIRED))
        self.assertEqual(set(self.schema["properties"]), set(TOP_LEVEL_REQUIRED) | {"release_notes", "compatibility"})
        rule = self.schema["allOf"][0]
        self.assertEqual(rule["then"], {"required": ["compatibility"]})
        self.assertEqual(rule["else"], {"not": {"required": ["compatibility"]}})

    def test_components_match_the_validator(self):
        components = self.schema["properties"]["components"]
        self.assertEqual(set(components["required"]), set(EXPECTED_REPOSITORIES))
        self.assertEqual(set(components["properties"]), set(EXPECTED_REPOSITORIES))

    def test_every_object_with_properties_forbids_unknown_keys(self):
        seen = []

        def walk(node, where):
            if isinstance(node, dict):
                if node.get("type") == "object" and "properties" in node:
                    seen.append(where)
                    self.assertIs(node.get("additionalProperties"), False, where)
                for key, child in node.items():
                    walk(child, f"{where}/{key}")
            elif isinstance(node, list):
                for index, child in enumerate(node):
                    walk(child, f"{where}/{index}")

        walk(self.schema, "#")
        for where in ("#", "#/properties/signature", "#/properties/components", "#/properties/helm",
                      "#/properties/deployment", "#/properties/migration", "#/$defs/component"):
            self.assertIn(where, seen)

    def test_constants_and_enums_match_the_module(self):
        properties = self.schema["properties"]
        self.assertEqual(properties["schema_version"], {"type": "integer", "enum": [1, SCHEMA_VERSION]})
        self.assertEqual(properties["product"], {"const": PRODUCT})
        self.assertEqual(properties["channel"]["enum"], list(CHANNELS))
        self.assertEqual(properties["migration"]["properties"]["rollback"]["enum"], ["manual", "unsupported"])
        self.assertEqual(properties["migration"]["properties"]["requires_bootstrap"], {"type": "boolean"})
        platforms = self.schema["$defs"]["component"]["properties"]["platforms"]
        self.assertEqual(set(platforms["items"]["enum"]), {"linux/amd64", "linux/arm64"})
        self.assertTrue(platforms["uniqueItems"])
        self.assertEqual(platforms["minItems"], 1)
        self.assertEqual(set(self.schema["$defs"]["component"]["required"]), {"image", "digest"})
        self.assertEqual(properties["signature"]["required"], ["bundle"])
        self.assertEqual(properties["deployment"]["properties"]["files"]["minProperties"], 1)

    def assertPatternAgrees(self, pattern, candidates, accepts):
        # JSON Schema patterns are unanchored searches; the schema anchors its own.
        compiled = re.compile(pattern)
        for candidate in candidates:
            with self.subTest(pattern=pattern, candidate=candidate):
                self.assertEqual(compiled.search(candidate) is not None, accepts(candidate))

    def test_version_pattern_agrees_with_is_version(self):
        versions = ("v0.0.0", "v1.52.0", "v1.52.0-beta.0", "v1.52.0-rc.12", "v10.20.30", "1.52.0", "v1.52",
                    "v01.0.0", "v1.0.0-beta.01", "v1.0.0-alpha.1", "v1.0.0-rc", "v1.0.0+b", " v1.0.0", "x v1.0.0",
                    "v1.0.0-gpu", "")
        self.assertPatternAgrees(self.schema["$defs"]["version"]["pattern"], versions, is_version)
        self.assertEqual(self.schema["properties"]["version"], {"$ref": "#/$defs/version"})
        self.assertEqual(self.schema["properties"]["minimum_version"], {"$ref": "#/$defs/version"})

    def test_helm_version_pattern_accepts_every_version_without_v(self):
        pattern = re.compile(self.schema["properties"]["helm"]["properties"]["version"]["pattern"])
        for version in ("v0.0.0", "v1.52.0", "v1.52.0-beta.0", "v1.52.0-rc.12"):
            with self.subTest(version=version):
                self.assertIsNotNone(pattern.search(version[1:]))
                self.assertIsNone(pattern.search(version))

    def test_field_patterns_agree_with_the_validator(self):
        properties = self.schema["properties"]
        digest_candidates = ("sha256:" + "a" * 64, "sha256:" + "0123456789abcdef" * 4, "sha256:" + "a" * 63,
                             "sha256:" + "A" * 64, "sha512:" + "a" * 64, "a" * 64, "", "xsha256:" + "a" * 64)
        self.assertPatternAgrees(self.schema["$defs"]["digest"]["pattern"], digest_candidates,
                                 lambda d: validator_accepts(self_mutated(_set("helm", "digest", d))))
        self.assertPatternAgrees(self.schema["$defs"]["digest"]["pattern"], digest_candidates,
                                 lambda d: validator_accepts(self_mutated(_set("components", "backend", "digest", d))))
        self.assertPatternAgrees(properties["source_sha"]["pattern"],
                                 ("a" * 40, "0123456789abcdef" * 2 + "01234567", "a" * 39, "a" * 41, "A" * 40, ""),
                                 lambda s: validator_accepts(self_mutated(_set("source_sha", s))))
        self.assertPatternAgrees(properties["published_at"]["pattern"],
                                 (PUBLISHED_AT, "1999-01-01T23:59:59Z", "2026-10-19", "2026-10-19T00:00:00",
                                  "2026-10-19T00:00:00.1Z", "2026-10-19 00:00:00Z", "x2026-10-19T00:00:00Z"),
                                 lambda s: validator_accepts(self_mutated(_set("published_at", s))))
        self.assertPatternAgrees(properties["signature"]["properties"]["bundle"]["pattern"],
                                 ("b", "a" * 201, "a" * 202, "x.sigstore.json", "dir/x", "../x", ".x", "-x", "a b"),
                                 lambda b: validator_accepts(self_mutated(_set("signature", "bundle", b))))
        self.assertPatternAgrees(properties["deployment"]["properties"]["files"]["additionalProperties"]["pattern"],
                                 ("a" * 64, "0" * 64, "a" * 63, "A" * 64, "sha256:" + "a" * 64, ""),
                                 lambda h: validator_accepts(self_mutated(_set("deployment", "files",
                                                                               {"docker-compose.yml": h}))))

    def test_component_image_pattern_accepts_every_valid_image(self):
        pattern = re.compile(self.schema["$defs"]["component"]["properties"]["image"]["pattern"])
        for image in ("futureagi/standalone:v1.52.0", "docker.io/futureagi/serving:v1.52.0-gpu",
                      "localhost:5000/futureagi/standalone:v1.52.0", "ghcr.io:443/futureagi/standalone:v1.52.0-rc.1"):
            with self.subTest(image=image):
                self.assertIsNotNone(pattern.search(image))
        for image in ("futureagi/standalone", "futureagi/standalone:v1@" + HELM_DIGEST, "a b:v1"):
            with self.subTest(image=image):
                self.assertIsNone(pattern.search(image))

    def test_patterns_spell_ascii_digits_and_are_anchored(self):
        patterns = list(schema_patterns(self.schema))
        self.assertGreater(len(patterns), 20)
        for where, pattern in patterns:
            with self.subTest(where=where):
                self.assertNotIn(r"\d", pattern, "\\d also matches non-ASCII digits; spell [0-9]")
                self.assertTrue(pattern.startswith("^") and pattern.endswith("$"), pattern)

    def test_component_image_patterns_agree_with_the_validator(self):
        # The repository path is pinned per component; only the registry host is free.
        for name in EXPECTED_REPOSITORIES:
            patterns = image_patterns(self.schema, name)
            self.assertGreater(len(patterns), 1, f"no per-component image pattern for {name}")
            for image in image_candidates(name):
                with self.subTest(name=name, image=image):
                    self.assertEqual(all(re.search(p, image) for p in patterns),
                                     validator_accepts(self_mutated(_set("components", name, "image", image))))

    def test_image_tag_equal_to_the_version_is_validator_only(self):
        # A version-shaped tag of another version fits the schema; only validate_manifest compares it.
        image = "docker.io/futureagi/future-agi:v1.53.0"
        self.assertTrue(all(re.search(p, image) for p in image_patterns(self.schema, "backend")))
        self.assertRejected(_set("components", "backend", "image", image), "must be tagged v1.52.0")
        self.assertIn("tagged with exactly the manifest version", self.schema["description"])

    def assertRejected(self, mutate, message):
        with self.assertRaisesRegex(ManifestError, message):
            validate_manifest(self_mutated(mutate))

    def test_helm_chart_pattern_agrees_with_the_validator(self):
        pattern = self.schema["properties"]["helm"]["properties"]["chart"]["pattern"]
        candidates = ManifestFieldTests.MALFORMED_CHARTS + ManifestFieldTests.VALID_CHARTS + (
            "oci://", "https://ghcr.io/future-agi/charts/futureagi", "OCI://ghcr.io/future-agi/charts/futureagi",
            CHART + ":1.52.0", CHART + "@sha256:" + "a" * 64, "oci://ghcr.io:443/charts/futureagi:1.52.0")
        self.assertPatternAgrees(pattern, candidates,
                                 lambda chart: validator_accepts(self_mutated(_set("helm", "chart", chart))))

    def test_deployment_file_name_pattern_agrees_with_the_validator(self):
        files = self.schema["properties"]["deployment"]["properties"]["files"]
        candidates = UNNORMALIZED_PATHS + DOTTED_PATHS + (
            "docker-compose.yml", "deploy/clickhouse/users.d/zz-default-password.xml", "a/ /b", "my file.yml",
            "", " ", " a", "a ", "\ta", "a\t", "a/ ", ".", "..", "./a", "../a", "a/..", "a/../b", "/etc/passwd",
            "a\\b", "\\a", "deploy\\x.yml")
        self.assertPatternAgrees(files["propertyNames"]["pattern"], candidates,
                                 lambda path: validator_accepts(self_mutated(_set("deployment", "files",
                                                                                  {path: "a" * 64}))))

    def test_stable_channel_requires_a_release_version(self):
        self.assertEqual(self.schema["if"], {"properties": {"channel": {"const": "stable"}}, "required": ["channel"]})
        self.assertEqual(self.schema["then"], {"properties": {"version": {"$ref": "#/$defs/release_version"}}})
        self.assertNotIn("else", self.schema)
        versions = ("v0.0.0", "v1.52.0", "v10.20.30", "v1.52.0-beta.0", "v1.52.0-rc.12", "v01.0.0", "1.52.0", "v1.52",
                    "v1.52.0-gpu", "")
        self.assertPatternAgrees(self.schema["$defs"]["release_version"]["pattern"], versions,
                                 lambda v: is_version(v) and not is_prerelease(v))

    def test_signature_strings_and_release_notes_agree_with_the_validator(self):
        signature = self.schema["properties"]["signature"]["properties"]
        trimmed = ("x", ISSUER, "a b", "a\tb", "", " ", "   ", " x", "x ", "\tx", "x\t")
        for key in ("identity", "issuer"):
            self.assertEqual(signature[key], {"$ref": "#/$defs/trimmed"})
            self.assertPatternAgrees(
                self.schema["$defs"]["trimmed"]["pattern"], trimmed,
                lambda value, key=key: validator_accepts(self_mutated(_set("signature", key, value))))
        notes = ("https://example.com", "https://x", f"https://github.com/future-agi/future-agi/releases/tag/{VERSION}",
                 "https://", "http://example.com", "HTTPS://example.com", "https://example.com/a b", "",
                 " https://example.com", "https://example.com ", "javascript:alert(1)", "//example.com")
        self.assertPatternAgrees(self.schema["properties"]["release_notes"]["pattern"], notes,
                                 lambda value: validator_accepts(self_mutated(_set("release_notes", value))))

    def test_helm_version_pattern_agrees_with_versions_without_v(self):
        pattern = self.schema["properties"]["helm"]["properties"]["version"]["pattern"]
        candidates = ("0.0.0", "1.52.0", "1.52.0-beta.0", "1.52.0-rc.12", "01.52.0", "1.052.0", "1.52.0-rc.01",
                      "1.52", "1.52.0-alpha.1", "v1.52.0", "1.5٢.0", "")
        self.assertPatternAgrees(pattern, candidates, lambda value: is_version("v" + value))

    def test_description_names_the_validator_only_rules(self):
        description = self.schema["description"]
        for rule in ("validate_manifest()", "tagged with exactly the manifest version", "serving_gpu",
                     "minimum_version is not newer than version", "helm.version equals the version without its leading v"):
            with self.subTest(rule=rule):
                self.assertIn(rule, description)


def schema_patterns(node, where="#"):
    """Every (location, pattern) in the schema."""

    if isinstance(node, dict):
        for key, child in node.items():
            if key == "pattern" and isinstance(child, str):
                yield where, child
            else:
                yield from schema_patterns(child, f"{where}/{key}")
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from schema_patterns(child, f"{where}/{index}")


def image_patterns(schema: dict, name: str) -> list:
    """Every pattern the schema applies to components.<name>.image."""

    components = schema["properties"]["components"]
    patterns = [schema["$defs"]["component"]["properties"]["image"]["pattern"]]
    for clause in components.get("allOf", []):
        image = clause.get("properties", {}).get(name, {}).get("properties", {}).get("image", {})
        if "pattern" in image:
            patterns.append(image["pattern"])
    return patterns


REGISTRY_PREFIXES = (
    "", "docker.io/", "index.docker.io/", "registry-1.docker.io/", "DOCKER.IO/", "ghcr.io/", "ghcr.io:443/",
    "localhost/", "localhost:5000/", "LOCALHOST:5000/", "127.0.0.1:5000/", "myhost:5000/", "mirror.example.com:8443/",
    "MIRROR.example.com/", "LOCALHOST/", "evil/", "bad_host.io/", "-bad.io/", "bad-.io/", "a..b/", "host:abc/",
    "host:123456/", "/", "docker.io/library/", "docker.io/mirror/",
)


def image_candidates(name: str) -> list:
    """Images for one component: its own repository and others, on good and bad hosts, with good and bad tags."""

    own = EXPECTED_REPOSITORIES[name]
    others = sorted(set(EXPECTED_REPOSITORIES.values()) - {own})
    repositories = [own, own + "-extra", own + "/sub", own.upper(), own.split("/")[1], *others]
    wrong_suffix = VERSION if name == "serving_gpu" else VERSION + "-gpu"
    tags = [":" + tag_for(name), ":" + wrong_suffix, ":latest", ":v1.52", "", f":{tag_for(name)}@{HELM_DIGEST}"]
    candidates = []
    for prefix in REGISTRY_PREFIXES:
        for repository in repositories:
            for tag in tags:
                candidates.append(prefix + repository + tag)
    return candidates


def self_mutated(mutate, version=VERSION, channel="stable"):
    manifest = valid_manifest(version, channel)
    mutate(manifest)
    return manifest


class SchemaParityTests(unittest.TestCase):
    """validate_manifest and release-manifest.schema.json agree (needs jsonschema)."""

    def setUp(self):
        self.validator = load_schema_validator(self)

    def schema_errors(self, manifest):
        return [error.message for error in self.validator.iter_errors(manifest)]

    def assertBothReject(self, manifest):
        self.assertFalse(validator_accepts(manifest), "validate_manifest accepted the mutation")
        self.assertNotEqual(self.schema_errors(manifest), [], "the schema accepted a manifest validate_manifest rejects")

    def test_schema_accepts_the_valid_manifest(self):
        self.assertEqual(self.schema_errors(valid_manifest()), [])

    def test_schema_accepts_every_variant_the_validator_accepts(self):
        for label, mutate in VALIDATOR_ACCEPTED_VARIANTS:
            with self.subTest(label=label):
                manifest = self_mutated(mutate)
                self.assertTrue(validator_accepts(manifest), label)
                self.assertEqual(self.schema_errors(manifest), [])

    def test_schema_rejects_what_the_validator_rejects(self):
        for label, mutate in SCHEMA_COVERED_MUTATIONS:
            with self.subTest(label=label):
                self.assertBothReject(self_mutated(mutate))

    def test_schema_rejects_prerelease_on_the_stable_channel(self):
        # Expressible with if/then on channel.
        for version in ("v1.53.0-beta.1", "v1.53.0-rc.1"):
            with self.subTest(version=version):
                self.assertBothReject(self_mutated(_retag(version)))

    def test_schema_rejects_component_images_from_other_repositories(self):
        # The repository path is fixed per component (a per-component pattern); only the host is free.
        for image in ("docker.io/evil/future-agi:v1.52.0", "docker.io/futureagi/frontend:v1.52.0",
                      "ghcr.io/futureagi/future-agi-extra:v1.52.0", "docker.io/futureagi/Future-agi:v1.52.0"):
            with self.subTest(image=image):
                self.assertBothReject(self_mutated(_set("components", "backend", "image", image)))

    def test_schema_rejects_tagged_helm_charts(self):
        for chart in (CHART + ":1.52.0", CHART + "@sha256:" + "a" * 64):
            with self.subTest(chart=chart):
                self.assertBothReject(self_mutated(_set("helm", "chart", chart)))

    def test_schema_rejects_deployment_file_paths_outside_the_checkout(self):
        # Expressible with propertyNames.
        for path in ("/etc/passwd", "../x", "a/../b", "a\\b", "./docker-compose.yml"):
            with self.subTest(path=path):
                self.assertBothReject(self_mutated(_set("deployment", "files", {path: "a" * 64})))

    def test_schema_rejects_untrimmed_signature_strings_and_release_notes(self):
        for label, mutate in (("identity blank", _set("signature", "identity", "   ")),
                              ("identity padded", _set("signature", "identity", " https://id")),
                              ("issuer padded", _set("signature", "issuer", ISSUER + " ")),
                              ("release_notes padded", _set("release_notes", "https://example.com "))):
            with self.subTest(label=label):
                self.assertBothReject(self_mutated(mutate))

    def test_schema_and_validator_both_reject_non_integer_schema_version_and_non_string_sha(self):
        for label, mutate in (("schema_version true", _set("schema_version", True)),
                              ("source_sha integer", _set("source_sha", int("1" * 40)))):
            with self.subTest(label=label):
                self.assertBothReject(self_mutated(mutate))

    def assertAgree(self, manifest):
        self.assertEqual(self.schema_errors(manifest) == [], validator_accepts(manifest))

    def test_schema_and_validator_agree_on_component_images(self):
        for name in ("backend", "serving", "serving_gpu"):
            for image in image_candidates(name):
                with self.subTest(name=name, image=image):
                    self.assertAgree(self_mutated(_set("components", name, "image", image)))

    def test_schema_and_validator_agree_on_helm_charts(self):
        for chart in ManifestFieldTests.MALFORMED_CHARTS + ManifestFieldTests.VALID_CHARTS:
            with self.subTest(chart=chart):
                self.assertAgree(self_mutated(_set("helm", "chart", chart)))

    def test_schema_and_validator_agree_on_deployment_paths(self):
        for path in UNNORMALIZED_PATHS + DOTTED_PATHS + ("a/ /b", ".", "..", "../a", "a\\b", " a", "a "):
            with self.subTest(path=path):
                self.assertAgree(self_mutated(_set("deployment", "files", {path: "a" * 64})))

    def test_only_the_cross_field_rules_separate_the_schema_from_the_validator(self):
        # Each of these fits the schema; validate_manifest alone compares fields with each other.
        for label, mutate in (("image tag of another version", _set("components", "backend", "image",
                                                                    "docker.io/futureagi/future-agi:v1.53.0")),
                              ("minimum newer than version", _set("minimum_version", "v1.53.0")),
                              ("helm version of another release", _set("helm", "version", "1.53.0"))):
            with self.subTest(label=label):
                manifest = self_mutated(mutate)
                self.assertEqual(self.schema_errors(manifest), [])
                self.assertFalse(validator_accepts(manifest))


# ------------------------------------------ dotfiles in hashed directories --

# Files an operator's tools leave in a checkout that git ignores, so a release checkout never has them
# and `git checkout` neither shows nor removes them (finding 35).
DOT_JUNK = (".DS_Store", "._schema.sql", ".schema.sql.swp", ".bootstrap.sh.swo", ".#schema.sql",
            "sub/.DS_Store", "sub/deeper/.schema.sql.swp", ".idea/workspace.xml", ".vscode/settings.json",
            "sub/.git/HEAD", "sub/.cache/deeper/plain.sql", ".hidden/visible-name.yml", "...")


class DirectoryHashSkipsDotfilesTests(unittest.TestCase):
    """A directory entry's digest leaves out every name starting with '.' below it, at any depth."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.files = {"schema.sql": b"CREATE TABLE t (x UInt8);\n", "bootstrap.sh": b"#!/bin/sh\n",
                      "sub/catalog.yml": b"a: 1\n", "sub/deeper/b.yml": b"b: 2\n"}
        self.clean = self.tree(self.base / "release" / "catalog", self.files)
        self.release_digest = hash_path(self.clean.parent, "catalog")

    def write(self, root: Path, relative: str, data: bytes = b"junk\n") -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def tree(self, root: Path, files: dict) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        for relative, data in files.items():
            self.write(root, relative, data)
        return root

    def host(self, name: str = "host") -> Path:
        return self.tree(self.base / name / "catalog", self.files)

    def test_a_top_level_ds_store_does_not_change_the_digest(self):
        host = self.host()
        self.write(host, ".DS_Store", b"\x00\x00\x00\x01Bud1")
        self.assertEqual(hash_path(host.parent, "catalog"), self.release_digest)

    def test_nested_dotfiles_do_not_change_the_digest(self):
        for relative in ("sub/.DS_Store", "sub/deeper/.schema.sql.swp", "sub/deeper/.#b.yml", "sub/._catalog.yml"):
            with self.subTest(relative=relative):
                host = self.host(relative.replace("/", "_"))
                self.write(host, relative)
                self.assertEqual(hash_path(host.parent, "catalog"), self.release_digest)

    def test_a_dot_directory_and_everything_below_it_is_skipped(self):
        for relative in (".idea/workspace.xml", ".git/objects/ab/cdef", "sub/.cache/deeper/plain.sql",
                         ".hidden/schema.sql", "sub/deeper/.vscode/settings.json"):
            with self.subTest(relative=relative):
                host = self.host(relative.replace("/", "_"))
                self.write(host, relative)
                self.assertEqual(hash_path(host.parent, "catalog"), self.release_digest)

    def test_all_junk_together_hashes_like_the_clean_release_checkout(self):
        host = self.host()
        for relative in DOT_JUNK:
            self.write(host, relative)
        self.assertEqual(hash_path(host.parent, "catalog"), self.release_digest)

    def test_a_directory_holding_only_dotfiles_hashes_like_an_empty_directory(self):
        empty = self.base / "empty" / "catalog"
        empty.mkdir(parents=True)
        junk_only = self.tree(self.base / "junk-only" / "catalog", {relative: b"x" for relative in DOT_JUNK})
        self.assertEqual(hash_path(junk_only.parent, "catalog"), hash_path(empty.parent, "catalog"))

    def test_a_real_file_change_is_still_detected_next_to_dotfiles(self):
        host = self.host()
        for relative in DOT_JUNK:
            self.write(host, relative)
        self.assertEqual(hash_path(host.parent, "catalog"), self.release_digest)
        for label, change in (("edited", lambda: self.write(host, "sub/catalog.yml", b"a: 2\n")),
                              ("added", lambda: self.write(host, "extra.sql", b"")),
                              ("removed", lambda: (host / "bootstrap.sh").unlink())):
            with self.subTest(change=label):
                change()
                self.assertNotEqual(hash_path(host.parent, "catalog"), self.release_digest)
                # Put the release's copy back: the digest returns, junk and all.
                self.tree(host, self.files)
                if (host / "extra.sql").exists():
                    (host / "extra.sql").unlink()
                self.assertEqual(hash_path(host.parent, "catalog"), self.release_digest)

    def test_names_with_a_dot_that_is_not_the_first_character_still_count(self):
        # Only a leading dot marks a hidden name; the skip must not swallow ordinary files.
        host = self.host()
        self.write(host, ".DS_Store")
        self.assertEqual(hash_path(host.parent, "catalog"), self.release_digest)
        before = self.release_digest
        for relative in ("a.b", "x.", "DS_Store", "schema.sql.swp", "dir.d/x.yml", "sub/v1.2/x", "_hidden", "-dash"):
            with self.subTest(relative=relative):
                self.write(host, relative, b"real")
                after = hash_path(host.parent, "catalog")
                self.assertNotEqual(after, before)
                before = after

    def test_only_names_below_the_hashed_directory_are_considered(self):
        # A dot directory above the entry (the checkout under ~/.local, or an entry such as futureagi/.ci)
        # is part of where the entry lives, not junk inside it.
        files = {"a.xml": b"<a/>", "sub/b.xml": b"<b/>"}
        plain = self.tree(self.base / "plain" / "checkout" / "ci", files)
        dotted = self.tree(self.base / ".local" / "checkout" / ".ci", files)
        self.write(dotted, ".DS_Store")
        empty = self.base / "empty" / "ci"
        empty.mkdir(parents=True)
        self.assertEqual(hash_path(dotted.parent, ".ci"), hash_path(plain.parent, "ci"))
        self.assertNotEqual(hash_path(dotted.parent, ".ci"), hash_path(empty.parent, "ci"))

    def test_a_listed_dotfile_entry_is_still_hashed_as_a_file(self):
        # The skip applies inside directories only: '.env.example' as an entry is a deployment file.
        root = self.tree(self.base / "files", {".env.example": b"KEY=value\n", ".dir/.inner": b"x"})
        self.assertEqual(hash_path(root, ".env.example"), sha256_hex(b"KEY=value\n"))
        self.assertEqual(hash_path(root, ".dir/.inner"), sha256_hex(b"x"))
        self.write(root / "dir", ".DS_Store")
        self.assertEqual(hash_path(root, "dir"), hash_path(self.tree(self.base / "e" / "dir", {}).parent, "dir"))

    def test_the_pinned_directory_format_leaves_dotfiles_out(self):
        host = self.tree(self.base / "pinned" / "catalog", {"a/b": b"2", "a-c": b"1", "z": b"", "B.yml": b"3"})
        for relative in DOT_JUNK:
            self.write(host, relative)
        expected = hashlib.sha256(b"futureagi-deployment-directory-v1\0")
        for name, data in (("B.yml", b"3"), ("a-c", b"1"), ("a/b", b"2"), ("z", b"")):
            expected.update(name.encode("utf-8") + b"\0" + sha256_hex(data).encode("ascii") + b"\n")
        self.assertEqual(hash_path(host.parent, "catalog"), expected.hexdigest())


class GeneratorSkipsDotfilesTests(unittest.TestCase):
    """The release generator and the host hash with the same function, so they skip the same files."""

    setUp = GeneratorTests.setUp
    run_generator = GeneratorTests.run_generator
    generate = GeneratorTests.generate

    def test_generator_and_host_agree_when_either_side_has_dotfiles(self):
        clean = self.generate()["deployment"]["files"]["config"]
        # The host checkout has Finder and editor leftovers the release checkout never had.
        host = self.base / "host"
        (host / "config" / ".idea").mkdir(parents=True)
        (host / "config" / "a.yml").write_text("a: 1\n", encoding="utf-8")
        (host / "config" / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
        (host / "config" / ".a.yml.swp").write_bytes(b"b0VIM")
        (host / "config" / ".idea" / "workspace.xml").write_text("<x/>", encoding="utf-8")
        self.assertEqual(hash_path(host, "config"), clean)
        # And a release checkout with leftovers records the clean digest too.
        (self.repo / "config" / ".DS_Store").write_bytes(b"junk")
        self.assertEqual(self.generate()["deployment"]["files"]["config"], clean)
        # A real difference is still drift.
        (host / "config" / "a.yml").write_text("a: 2\n", encoding="utf-8")
        self.assertNotEqual(hash_path(host, "config"), clean)


# ----------------------------------- editor and OS litter in hashed directories --

# Files editors and operating systems leave next to the files an operator opened, which git commonly ignores
# (this repository's .gitignore has '*~'), so a release checkout never has them and `git checkout` neither shows
# nor removes them (round 3, R13). Names that start with '.' are covered by DOT_JUNK above.
EDITOR_BACKUPS = ("schema.sql~", "bootstrap.sh~", "notes.txt~", "~", "sub/catalog.yml~", "sub/deeper/b.yml~")
EMACS_AUTOSAVES = ("#schema.sql#", "#bootstrap.sh#", "##", "#new file#", "sub/#catalog.yml#", "sub/deeper/#b.yml#")
OS_LITTER = ("Thumbs.db", "desktop.ini", "sub/Thumbs.db", "sub/desktop.ini", "sub/deeper/thumbs.db",
             "sub/deeper/Desktop.ini", "sub/deeper/more/THUMBS.DB", "sub/deeper/more/DESKTOP.INI")
LITTER_DIRECTORIES = ("backup~/schema.sql", "sub/old~/deeper/x.yml", "#autosave#/plain.yml", "sub/#tmp#/a/b.yml")
LITTER = EDITOR_BACKUPS + EMACS_AUTOSAVES + OS_LITTER + LITTER_DIRECTORIES


class DirectoryHashSkipsEditorAndOsLitterTests(unittest.TestCase):
    """A directory entry's digest also leaves out 'name~', '#name#', Thumbs.db and desktop.ini, at any depth."""

    setUp = DirectoryHashSkipsDotfilesTests.setUp
    write = DirectoryHashSkipsDotfilesTests.write
    tree = DirectoryHashSkipsDotfilesTests.tree
    host = DirectoryHashSkipsDotfilesTests.host

    def assertSkipped(self, names):
        for relative in names:
            with self.subTest(relative=relative):
                host = self.host(relative.replace("/", "_").replace("#", "H").replace("~", "T"))
                self.write(host, relative)
                self.assertEqual(hash_path(host.parent, "catalog"), self.release_digest)

    def test_editor_backups_ending_in_a_tilde_do_not_change_the_digest(self):
        self.assertSkipped(EDITOR_BACKUPS)

    def test_a_backup_holding_the_files_own_bytes_does_not_change_the_digest(self):
        # What Emacs and vim actually leave: a copy of the previous content next to the file.
        host = self.host()
        for relative, data in self.files.items():
            self.write(host, relative + "~", data)
        self.assertEqual(hash_path(host.parent, "catalog"), self.release_digest)

    def test_emacs_autosaves_between_hashes_do_not_change_the_digest(self):
        self.assertSkipped(EMACS_AUTOSAVES)

    def test_thumbs_db_and_desktop_ini_do_not_change_the_digest_at_any_depth_in_either_case(self):
        self.assertSkipped(OS_LITTER)

    def test_a_litter_named_directory_and_everything_below_it_is_skipped(self):
        self.assertSkipped(LITTER_DIRECTORIES)

    def test_all_litter_and_dotfiles_together_hash_like_the_clean_release_checkout(self):
        host = self.host()
        for relative in LITTER + DOT_JUNK:
            self.write(host, relative)
        self.assertEqual(hash_path(host.parent, "catalog"), self.release_digest)

    def test_a_directory_holding_only_litter_hashes_like_an_empty_directory(self):
        empty = self.base / "empty" / "catalog"
        empty.mkdir(parents=True)
        junk_only = self.tree(self.base / "junk-only" / "catalog", {relative: b"x" for relative in LITTER})
        self.assertEqual(hash_path(junk_only.parent, "catalog"), hash_path(empty.parent, "catalog"))

    def test_a_real_file_change_is_still_detected_next_to_litter(self):
        host = self.host()
        for relative in LITTER:
            self.write(host, relative)
        # The backups hold the release's bytes, as an editor would leave them.
        for relative, data in self.files.items():
            self.write(host, relative + "~", data)
        self.assertEqual(hash_path(host.parent, "catalog"), self.release_digest)
        for label, change in (("edited", lambda: self.write(host, "schema.sql", b"CREATE TABLE t (x UInt16);\n")),
                              ("edited nested", lambda: self.write(host, "sub/deeper/b.yml", b"b: 3\n")),
                              ("added", lambda: self.write(host, "extra.sql", b"")),
                              ("added next to an autosave", lambda: self.write(host, "sub/catalog-2.yml", b"c\n")),
                              ("removed", lambda: (host / "bootstrap.sh").unlink()),
                              ("removed, backup left", lambda: (host / "sub" / "catalog.yml").unlink())):
            with self.subTest(change=label):
                change()
                self.assertNotEqual(hash_path(host.parent, "catalog"), self.release_digest)
                # Put the release's copy back: the digest returns, litter and all.
                self.tree(host, self.files)
                for extra in ("extra.sql", "sub/catalog-2.yml"):
                    if (host / extra).exists():
                        (host / extra).unlink()
                self.assertEqual(hash_path(host.parent, "catalog"), self.release_digest)

    def test_names_that_only_resemble_litter_still_count(self):
        # The skip must not swallow ordinary files: only a trailing '~', a name both starting and ending with
        # '#', and exactly Thumbs.db or desktop.ini are litter.
        host = self.host()
        for relative in ("schema.sql~", "#schema.sql#", "Thumbs.db", "desktop.ini"):
            self.write(host, relative)
        self.assertEqual(hash_path(host.parent, "catalog"), self.release_digest)
        before = self.release_digest
        for relative in ("~schema.sql", "a~b", "sub/~x.yml", "#schema.sql", "schema.sql#", "#", "a#b#", "x#y",
                         "sub/#dir/x.yml", "sub/dir#/x.yml", "sub/a~b/x.yml", "Thumbs", "Thumbs.db.bak",
                         "my-Thumbs.db", "Thumbs.dbx", "desktop", "desktop.ini.txt", "old-desktop.ini",
                         "sub/Thumbs.db.d/x.yml"):
            with self.subTest(relative=relative):
                self.write(host, relative, b"real")
                after = hash_path(host.parent, "catalog")
                self.assertNotEqual(after, before)
                before = after

    def test_only_names_below_the_hashed_directory_are_considered(self):
        # An entry named like litter, or one below such a directory, is where the files live, not litter.
        files = {"a.xml": b"<a/>", "sub/b.xml": b"<b/>"}
        plain = self.tree(self.base / "plain" / "checkout" / "ci", files)
        empty = self.base / "empty" / "ci"
        empty.mkdir(parents=True)
        for parent, entry in (("old~/checkout", "ci"), ("checkout", "ci~"), ("checkout", "#ci#"),
                              ("#parent#", "Thumbs.db"), ("checkout", "desktop.ini")):
            with self.subTest(entry=f"{parent}/{entry}"):
                directory = self.tree(self.base / "litter" / parent / entry, files)
                self.write(directory, "Thumbs.db")
                self.write(directory, "a.xml~")
                self.assertEqual(hash_path(directory.parent, entry), hash_path(plain.parent, "ci"))
                self.assertNotEqual(hash_path(directory.parent, entry), hash_path(empty.parent, "ci"))

    def test_a_listed_litter_named_entry_is_still_hashed_as_a_file(self):
        # The skip applies inside directories only: a file listed by name is a deployment file.
        files = {"notes~": b"n\n", "#x#": b"x\n", "Thumbs.db": b"t", "d/desktop.ini": b"d"}
        root = self.tree(self.base / "files", files)
        for entry, data in files.items():
            with self.subTest(entry=entry):
                self.assertEqual(hash_path(root, entry), sha256_hex(data))
        self.assertEqual(hash_path(root, "d"), hash_path(self.tree(self.base / "e" / "d", {}).parent, "d"))

    def test_the_pinned_directory_format_leaves_litter_out(self):
        host = self.tree(self.base / "pinned" / "catalog", {"a/b": b"2", "a-c": b"1", "z": b"", "B.yml": b"3"})
        for relative in LITTER + DOT_JUNK:
            self.write(host, relative)
        expected = hashlib.sha256(b"futureagi-deployment-directory-v1\0")
        for name, data in (("B.yml", b"3"), ("a-c", b"1"), ("a/b", b"2"), ("z", b"")):
            expected.update(name.encode("utf-8") + b"\0" + sha256_hex(data).encode("ascii") + b"\n")
        self.assertEqual(hash_path(host.parent, "catalog"), expected.hexdigest())

    def test_this_repositorys_listed_directories_do_not_drift_over_litter(self):
        # The report's repro: a copy of futureagi/scripts/property_catalog_oss with 'bootstrap_clickhouse.sh~'
        # or Thumbs.db in it read as drift. Every directory the real deployment-file list names is checked.
        directories = [entry for entry in read_deployment_file_list(ROOT) if (ROOT / entry).is_dir()]
        self.assertIn("futureagi/scripts/property_catalog_oss", directories)
        for entry in directories:
            with self.subTest(entry=entry):
                copy_root = self.base / "repo-copy" / entry.replace("/", "_")
                shutil.copytree(ROOT / entry, copy_root / "entry",
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".*", "*~", "#*#",
                                                              "Thumbs.db", "desktop.ini"))
                clean = hash_path(copy_root, "entry")
                files = sorted(path for path in (copy_root / "entry").rglob("*") if path.is_file())
                self.assertTrue(files, entry)
                for path in files:
                    path.with_name(path.name + "~").write_bytes(path.read_bytes())
                    path.with_name("#" + path.name + "#").write_bytes(b"autosave")
                    for name in ("Thumbs.db", "desktop.ini", ".DS_Store"):
                        (path.parent / name).write_bytes(b"litter")
                self.assertEqual(hash_path(copy_root, "entry"), clean)
                files[0].write_bytes(files[0].read_bytes() + b"\n-- changed\n")
                self.assertNotEqual(hash_path(copy_root, "entry"), clean)


class GeneratorSkipsEditorAndOsLitterTests(unittest.TestCase):
    """The release generator and the host skip the same editor and OS litter, because they share hash_path."""

    setUp = GeneratorTests.setUp
    run_generator = GeneratorTests.run_generator
    generate = GeneratorTests.generate

    def test_generator_and_host_agree_when_either_side_has_litter(self):
        clean = self.generate()["deployment"]["files"]["config"]
        # The host checkout has editor and Windows leftovers the release checkout never had.
        host = self.base / "host"
        (host / "config" / "old~").mkdir(parents=True)
        (host / "config" / "a.yml").write_text("a: 1\n", encoding="utf-8")
        (host / "config" / "a.yml~").write_text("a: 0\n", encoding="utf-8")
        (host / "config" / "#a.yml#").write_text("a: 9\n", encoding="utf-8")
        (host / "config" / "Thumbs.db").write_bytes(b"\xd0\xcf\x11\xe0")
        (host / "config" / "desktop.ini").write_text("[.ShellClassInfo]\n", encoding="utf-8")
        (host / "config" / "old~" / "a.yml").write_text("a: 0\n", encoding="utf-8")
        self.assertEqual(hash_path(host, "config"), clean)
        # And a release checkout with leftovers records the clean digest too.
        (self.repo / "config" / "a.yml~").write_text("a: 0\n", encoding="utf-8")
        (self.repo / "config" / "#a.yml#").write_text("a: 9\n", encoding="utf-8")
        (self.repo / "config" / "Desktop.ini").write_text("[.ShellClassInfo]\n", encoding="utf-8")
        self.assertEqual(self.generate()["deployment"]["files"]["config"], clean)
        # A real difference is still drift.
        (host / "config" / "a.yml").write_text("a: 2\n", encoding="utf-8")
        self.assertNotEqual(hash_path(host, "config"), clean)


# ------------------------- git-tracked files a directory digest would leave out (round 4, S51) --

def git_env() -> dict:
    """The environment for git in these tests: nothing steering it at another repository or the system config."""

    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], env=git_env(), capture_output=True, text=True,
                          timeout=60, check=True)


def make_git_checkout(root: Path) -> None:
    """Make `root` a git checkout whose index holds the files in it, as a release checkout does."""

    if shutil.which("git") is None:
        raise unittest.SkipTest("git is not installed")
    run_git(root, "-c", "init.defaultBranch=main", "init", "-q")
    run_git(root, "add", "-A")


def not_a_git_checkout_warning(root: Path) -> str:
    return (f"generate_update_manifest: warning: {root} is not a git checkout, so the check that every file git "
            f"tracks below a directory listed in {DEPLOYMENT_FILE_LIST} is part of its digest was skipped\n")


def track(root: Path, *paths: str) -> None:
    """Have git track `paths` (whatever any ignore file says), as `git add -f` in a release commit would."""

    run_git(root, "--literal-pathspecs", "add", "-f", "--", *paths)


def untrack(root: Path, *paths: str) -> None:
    """Have git stop tracking `paths`, leaving them in the checkout."""

    run_git(root, "--literal-pathspecs", "rm", "-q", "--cached", "--", *paths)


# Paths below a listed directory 'catalog' that its digest skips, one per rule.
SKIPPED_BELOW_CATALOG = (
    "catalog/.env", "catalog/sub/.hidden.yml", "catalog/.cfg/x.yml", "catalog/schema.sql~", "catalog/old~/a.yml",
    "catalog/#schema.sql#", "catalog/#tmp#/b.yml", "catalog/Thumbs.db", "catalog/sub/Desktop.ini",
    "catalog/__pycache__/m.cpython-39.pyc", "catalog/__pycache__/plain.txt", "catalog/m.pyc", "catalog/sub/m.pyo",
)
# Paths below 'catalog' its digest keeps, some of them only resembling skipped names.
KEPT_BELOW_CATALOG = (
    "catalog/schema.sql", "catalog/sub/deeper/b.yml", "catalog/a.b", "catalog/~x", "catalog/a~b", "catalog/a#b#",
    "catalog/#x", "catalog/Thumbs.db.bak", "catalog/my-desktop.ini", "catalog/sub/v1.2/x", "catalog/m.py",
    "catalog/pyc", "catalog/x.pyc.txt",
)


class LeftOutOfDigestsTests(unittest.TestCase):
    """left_out_of_digests names the files below a listed directory that no entry's digest covers."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.entries = ["docker-compose.yml", ".env.example", "catalog"]
        for relative in ("docker-compose.yml", ".env.example") + SKIPPED_BELOW_CATALOG + KEPT_BELOW_CATALOG:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(relative.encode("utf-8"))

    def test_every_file_the_directory_digest_skips_is_reported(self):
        for relative in SKIPPED_BELOW_CATALOG:
            with self.subTest(relative=relative):
                self.assertEqual(left_out_of_digests(self.root, self.entries, [relative]), [relative])

    def test_files_the_directory_digest_keeps_are_not_reported(self):
        self.assertEqual(left_out_of_digests(self.root, self.entries, KEPT_BELOW_CATALOG), [])

    def test_files_outside_every_listed_directory_are_not_reported(self):
        # Names above or beside a listed directory are not inside its digest; listed files are hashed by name.
        outside = (".env", ".env.example", ".gitignore", ".github/workflows/x.yml", "other/x~", "other/#x#",
                   "catalog-old/.env", "catalogue/Thumbs.db", "docker-compose.yml", "docker-compose.yml~")
        self.assertEqual(left_out_of_digests(self.root, self.entries, outside), [])

    def test_a_skipped_file_listed_on_its_own_line_is_covered(self):
        entries = self.entries + ["catalog/.env", "catalog/old~/a.yml"]
        self.assertEqual(left_out_of_digests(self.root, entries, SKIPPED_BELOW_CATALOG),
                         sorted(set(SKIPPED_BELOW_CATALOG) - {"catalog/.env", "catalog/old~/a.yml"}))

    def test_a_listed_directory_below_a_skipped_name_covers_what_its_own_digest_keeps(self):
        # 'catalog/.cfg' as an entry of its own is hashed as a directory: '.cfg' is where it lives, not litter.
        entries = self.entries + ["catalog/.cfg"]
        (self.root / "catalog" / ".cfg" / "x~").write_bytes(b"backup")
        files = ["catalog/.cfg/x.yml", "catalog/.cfg/x~", "catalog/.env"]
        self.assertEqual(left_out_of_digests(self.root, entries, files), ["catalog/.cfg/x~", "catalog/.env"])

    def test_only_entries_that_are_directories_in_the_checkout_count(self):
        # A listed file has nothing below it, and an entry missing from the checkout is recorded as missing.
        self.assertEqual(left_out_of_digests(self.root, ["docker-compose.yml", "absent"],
                                             ["docker-compose.yml", "absent/.env", "catalog/.env"]), [])

    def test_the_report_is_sorted_and_names_each_file_once(self):
        files = list(reversed(SKIPPED_BELOW_CATALOG)) * 2 + list(KEPT_BELOW_CATALOG)
        self.assertEqual(left_out_of_digests(self.root, self.entries, files), sorted(SKIPPED_BELOW_CATALOG))

    def test_it_agrees_with_what_hash_path_leaves_out(self):
        # A reported file is one whose edits do not change the directory's digest; an unreported one's do.
        files = SKIPPED_BELOW_CATALOG + KEPT_BELOW_CATALOG
        reported = set(left_out_of_digests(self.root, self.entries, files))
        for relative in files:
            with self.subTest(relative=relative):
                before = hash_path(self.root, "catalog")
                path = self.root / relative
                path.write_bytes(path.read_bytes() + b"\nedited\n")
                self.assertEqual(hash_path(self.root, "catalog") == before, relative in reported)


class GeneratorRefusesTrackedFilesItsDigestSkipsTests(unittest.TestCase):
    """The generator refuses a release in which git tracks a file below a listed directory that its digest skips:
    it would be bind-mounted into containers without being part of the signed digest, so a host would never
    report a local edit to it (round 4, S51)."""

    setUp = GeneratorTests.setUp
    run_generator = GeneratorTests.run_generator
    generate = GeneratorTests.generate
    assertGeneratorFails = GeneratorTests.assertGeneratorFails

    SKIPPED = ("config/.env", "config/.hidden/x.yml", "config/a.yml~", "config/old~/b.yml", "config/#a.yml#",
               "config/Thumbs.db", "config/sub/desktop.ini", "config/__pycache__/m.cpython-39.pyc", "config/m.pyc",
               "config/m.pyo")

    def write(self, relative: str, data: bytes = b"tracked\n") -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def refusal(self, *paths: str) -> str:
        return (f"generate_update_manifest: git tracks {len(paths)} file(s) below a directory listed in "
                f"{DEPLOYMENT_FILE_LIST} that its digest would leave out, so bin/update would never report a local "
                f"edit to them: {', '.join(paths)}. A directory's digest skips names that start with '.', end with "
                "'~' or start and end with '#', Thumbs.db, desktop.ini, __pycache__ and .pyc/.pyo files; rename "
                f"each of these files, or list it on its own line in {DEPLOYMENT_FILE_LIST}.\n")

    def test_a_clean_git_checkout_generates_without_a_warning(self):
        manifest = self.generate()
        self.assertEqual(manifest["deployment"]["files"]["config"], hash_path(self.repo, "config"))

    def test_each_tracked_file_the_digest_skips_is_refused_and_nothing_is_written(self):
        output = self.base / "out.json"
        for relative in self.SKIPPED:
            with self.subTest(relative=relative):
                self.write(relative)
                track(self.repo, relative)
                result = self.run_generator("--output", str(output))
                self.assertGeneratorFails(result, "^" + re.escape(self.refusal(relative)) + "$")
                self.assertFalse(output.exists())
                untrack(self.repo, relative)
                # Once git no longer tracks it, the same file in the checkout is litter the digest skips.
                self.assertEqual(self.run_generator().returncode, 0)

    def test_every_such_file_is_named_in_one_message_in_order(self):
        for relative in self.SKIPPED:
            self.write(relative)
        track(self.repo, *self.SKIPPED)
        self.assertGeneratorFails(self.run_generator(), "^" + re.escape(self.refusal(*sorted(self.SKIPPED))) + "$")

    def test_a_force_added_file_the_repositorys_gitignore_ignores_is_refused(self):
        # This repository's .gitignore has '*~', so only 'git add -f' tracks one; the guard still sees it.
        self.write(".gitignore", b"*~\n")
        self.write("config/bootstrap.sh~", b"#!/bin/sh\n")
        run_git(self.repo, "add", "-A")
        self.assertEqual(self.generate()["deployment"]["files"]["config"], hash_path(self.repo, "config"))
        track(self.repo, "config/bootstrap.sh~")
        self.assertGeneratorFails(self.run_generator(), re.escape("edit to them: config/bootstrap.sh~. "))

    def test_untracked_litter_in_the_release_checkout_is_not_refused(self):
        clean = self.generate()["deployment"]["files"]["config"]
        for relative in self.SKIPPED:
            self.write(relative)
        self.assertEqual(self.generate()["deployment"]["files"]["config"], clean)

    def test_tracked_litter_outside_the_listed_directories_is_not_refused(self):
        outside = (".gitignore", ".github/workflows/x.yml", "other/x~", "config-old/.env", "configs/#x#",
                   "deploy/update/.keep")
        for relative in outside:
            self.write(relative)
        track(self.repo, *outside)
        self.generate()

    def test_a_tracked_file_listed_on_its_own_line_is_accepted_and_hashed(self):
        self.write("config/.env", b"KEY=value\n")
        track(self.repo, "config/.env")
        clean = hash_path(self.repo, "config")
        (self.repo / DEPLOYMENT_FILE_LIST).write_text("docker-compose.yml\nconfig\nconfig/.env\n", encoding="utf-8")
        files = self.generate()["deployment"]["files"]
        self.assertEqual(files["config/.env"], sha256_hex(b"KEY=value\n"))
        self.assertEqual(files["config"], clean)

    def test_a_listed_directory_name_reaches_git_as_a_literal_path(self):
        # ':catalog' is pathspec magic for 'catalog' and 'c*' a glob, unless git takes the names literally.
        for name in (":catalog", "c*"):
            with self.subTest(name=name):
                (self.repo / DEPLOYMENT_FILE_LIST).write_text(f"docker-compose.yml\n{name}\n", encoding="utf-8")
                self.write(f"{name}/a.yml")
                self.write(f"{name}/.env")
                track(self.repo, f"{name}/a.yml", f"{name}/.env")
                self.assertGeneratorFails(self.run_generator(), "^" + re.escape(self.refusal(f"{name}/.env")) + "$")
                untrack(self.repo, f"{name}/.env")
                self.generate()

    def test_git_environment_variables_do_not_point_the_check_at_another_repository(self):
        # Inside a git hook GIT_DIR, GIT_WORK_TREE and GIT_INDEX_FILE name the hook's repository.
        other = self.base / "other"
        (other / "config").mkdir(parents=True)
        (other / "config" / "a.yml").write_text("a: 1\n", encoding="utf-8")
        make_git_checkout(other)
        steer = {"GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other),
                 "GIT_INDEX_FILE": str(other / ".git" / "index")}
        self.write("config/.env")
        track(self.repo, "config/.env")
        self.env.update(steer)
        self.assertGeneratorFails(self.run_generator(), "^" + re.escape(self.refusal("config/.env")) + "$")
        # And the other way round: the release checkout is clean, the steered-to repository is not.
        untrack(self.repo, "config/.env")
        (other / "config" / ".env").write_text("x\n", encoding="utf-8")
        track(other, "config/.env")
        self.generate()

    def test_not_a_git_checkout_warns_and_still_generates(self):
        with_git = self.run_generator()
        self.assertEqual((with_git.returncode, with_git.stderr), (0, ""))
        shutil.rmtree(self.repo / ".git")
        self.write("config/.env")
        result = self.run_generator()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, not_a_git_checkout_warning(self.repo.resolve()))
        self.assertEqual(result.stdout, with_git.stdout)

    def test_no_warning_without_a_listed_directory(self):
        shutil.rmtree(self.repo / ".git")
        (self.repo / DEPLOYMENT_FILE_LIST).write_text("docker-compose.yml\nconfig/a.yml\n", encoding="utf-8")
        self.assertEqual(set(self.generate()["deployment"]["files"]), {"docker-compose.yml", "config/a.yml"})

    def test_a_git_checkout_git_cannot_read_is_refused(self):
        shutil.rmtree(self.repo / ".git")
        (self.repo / ".git").write_text(f"gitdir: {self.base / 'gone'}\n", encoding="utf-8")
        self.assertGeneratorFails(self.run_generator(), "^" + re.escape(
            f"generate_update_manifest: git ls-files failed in {self.repo.resolve()}: fatal: "))

    def test_a_git_checkout_without_git_on_the_path_is_refused(self):
        empty = self.base / "empty-path"
        empty.mkdir()
        self.env["PATH"] = str(empty)
        self.assertGeneratorFails(self.run_generator(), "^" + re.escape(
            f"generate_update_manifest: cannot run git to list the files it tracks in {self.repo.resolve()}: "))

    def test_build_refuses_too(self):
        # Callers that import the generator (deploy/tests/test_update_plan.py) get the same guard.
        spec = importlib.util.spec_from_file_location("generate_update_manifest_for_s51", GENERATOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        args = module.argparse.Namespace(
            version=VERSION, channel="stable", source_sha=SOURCE_SHA, published_at=PUBLISHED_AT, minimum_version=None,
            component=component_args()[1::2], helm_chart=CHART, helm_digest=HELM_DIGEST, bundle_name=BUNDLE_ASSET,
            signature_identity=None, signature_issuer=ISSUER, repo_root=str(self.repo), output=None)
        self.assertEqual(module.build(args)["deployment"]["files"]["config"], hash_path(self.repo, "config"))
        self.write("config/#a.yml#")
        track(self.repo, "config/#a.yml#")
        with self.assertRaisesRegex(ManifestError, "^" + re.escape(
                self.refusal("config/#a.yml#")[len("generate_update_manifest: "):-1]) + "$"):
            module.build(args)

    def test_this_repositorys_tracked_deployment_files_are_all_part_of_a_digest(self):
        # test_this_repositorys_listed_directories_do_not_drift_over_litter copies the listed directories without
        # skipped names, so it cannot see a tracked one; git can.
        if shutil.which("git") is None or not (ROOT / ".git").exists():
            self.skipTest("not a git checkout")
        entries = read_deployment_file_list(ROOT)
        directories = [entry for entry in entries if (ROOT / entry).is_dir()]
        self.assertIn("futureagi/scripts/property_catalog_oss", directories)
        listed = run_git(ROOT, "--literal-pathspecs", "ls-files", "-z", "--", *directories).stdout.split("\0")
        files = [path for path in listed if path]
        for directory in directories:
            with self.subTest(directory=directory):
                self.assertTrue([path for path in files if path.startswith(directory + "/")], directory)
        self.assertEqual(left_out_of_digests(ROOT, entries, files), [])



# ------------------------------------------------- round 6: version numbers --

def round6_versions(digits: str):
    """`digits` as each number of a version in turn: major, minor, patch, then the prerelease number."""

    return (f"v{digits}.1.0", f"v1.{digits}.0", f"v1.0.{digits}", f"v1.0.0-beta.{digits}", f"v1.0.0-rc.{digits}")


class TrustRound6VersionNumberLengthTests(ManifestCase):
    """U38: a version number of absurdly many digits is a ManifestError, never a traceback.

    int() refuses a string of more than 4300 digits on Python 3.11+ with a plain
    ValueError, which the CLI does not catch; every number of a version is
    capped at 18 digits, so is_version, parse_version and the validator agree.
    """

    TOO_LONG = ("1" * 19, "9" * 19, "1" * 4301, "7" * 5000)

    def test_a_number_of_more_than_18_digits_is_no_version(self):
        for digits in self.TOO_LONG:
            for version in round6_versions(digits):
                with self.subTest(version=version[:30], length=len(version)):
                    self.assertFalse(is_version(version))
                    with self.assertRaises(ManifestError) as caught:
                        parse_version(version)
                    message = str(caught.exception)
                    self.assertIn("expected vMAJOR.MINOR.PATCH without leading zeros, each number at most 18 digits "
                                  "long", message)
                    self.assertLess(len(message), 200, "the version is shortened, not printed in full")
                    with self.assertRaises(ManifestError):
                        is_prerelease(version)

    def test_18_digits_are_still_a_version_and_sort_numerically(self):
        for version in round6_versions("9" * 18):
            with self.subTest(version=version):
                self.assertTrue(is_version(version))
                self.assertGreater(parse_version(version), parse_version("v1.0.0-beta.0"))
        self.assertEqual(parse_version("v" + "9" * 18 + ".0.0"), (10 ** 18 - 1, 0, 0, 2, 0))
        self.assertGreater(parse_version("v" + "1" + "0" * 17 + ".0.0"), parse_version("v" + "9" * 17 + ".0.0"))

    def test_the_manifest_validator_refuses_it_with_a_manifest_error(self):
        for digits in self.TOO_LONG:
            with self.subTest(field="version", length=len(digits)):
                version = f"v{digits}.1.0"
                self.assertRejected(retag(valid_manifest(), version),
                                    r"invalid version .*each number at most 18 digits long")
            with self.subTest(field="minimum_version", length=len(digits)):
                manifest = valid_manifest()
                manifest["minimum_version"] = f"v1.0.0-rc.{digits}"
                self.assertRejected(manifest, r"invalid version .*each number at most 18 digits long")

    def test_the_upgrade_floor_refuses_it_with_a_manifest_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "deploy" / "update").mkdir(parents=True)
            for digits in self.TOO_LONG:
                with self.subTest(length=len(digits)):
                    (root / UPGRADE_FLOOR_FILE).write_text(f"v1.{digits}.0\n", encoding="utf-8")
                    with self.assertRaisesRegex(ManifestError, "each number at most 18 digits long"):
                        read_upgrade_floor(root)

    def test_the_generator_exits_2_without_a_traceback(self):
        harness = GeneratorTests("test_writes_a_valid_manifest_to_stdout")
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        for digits in ("1" * 19, "7" * 5000):
            with self.subTest(length=len(digits)):
                result = harness.run_generator(version=f"v{digits}.1.0")
                harness.assertGeneratorFails(result, r"invalid version .*each number at most 18 digits long")



class IntegratorRound6SchemaVersionLengthTests(unittest.TestCase):
    """U38 on the schema's side: release-manifest.schema.json caps each version number at 18 digits as
    validate_manifest does, so the two still agree (needs jsonschema)."""

    def setUp(self):
        self.validator = load_schema_validator(self)

    def assertAgree(self, manifest, accepted):
        errors = [error.message for error in self.validator.iter_errors(manifest)]
        self.assertEqual(validator_accepts(manifest), accepted)
        self.assertEqual(errors == [], accepted, errors)

    def test_schema_and_validator_agree_on_the_length_of_a_version_number(self):
        for digits, accepted in (("9" * 18, True), ("1" * 19, False), ("7" * 5000, False)):
            for version in round6_versions(digits):
                channel = "beta" if "-" in version else "stable"

                def mutate(manifest, version=version, channel=channel):
                    _retag(version, channel)(manifest)
                    manifest["minimum_version"] = "v0.0.0"  # never newer than the version

                with self.subTest(field="version", version=version[:30], length=len(version)):
                    self.assertAgree(self_mutated(mutate, channel=channel), accepted)
            with self.subTest(field="minimum_version", length=len(digits)):
                self.assertAgree(self_mutated(_set("minimum_version", f"v0.0.0-rc.{digits}")), accepted)

    def test_the_schema_caps_every_version_number_it_matches(self):
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        self.assertNotIn("[1-9][0-9]*", schema)
        # v2 adds the exact predecessor version (three numbers + prerelease).
        # Four additional groups belong to the separate closed v2 predecessor branch.
        self.assertEqual(schema.count("(0|[1-9][0-9]{0,17})"), 55)


# ------------------------------------------------ hardening: small corners --

class HardeningVersionMessageTests(unittest.TestCase):
    """An invalid version is quoted whole up to 64 characters, longer ones as their first 32 and last 8."""

    def assertMessage(self, value, message):
        with self.assertRaises(ManifestError) as caught:
            parse_version(value)
        self.assertEqual(str(caught.exception), message)

    def test_a_64_character_value_is_quoted_whole(self):
        value = "v" + "0123456789abcdef" * 3 + "0123456789abcde"
        self.assertEqual(len(value), 64)
        self.assertMessage(value, f"invalid version {value!r}: expected vMAJOR.MINOR.PATCH")

    def test_a_65_character_value_is_quoted_as_its_first_32_and_last_8_characters(self):
        value = "v" + "0123456789abcdef" * 4
        self.assertEqual(len(value), 65)
        self.assertMessage(value, "invalid version 'v0123456789abcdef0123456789abcde...89abcdef': "
                                  "expected vMAJOR.MINOR.PATCH")

    def test_a_long_number_is_shortened_the_same_way(self):
        value = "v1." + "1234567890" * 7 + ".0"
        self.assertMessage(value, f"invalid version {value[:32] + '...' + value[-8:]!r}: expected vMAJOR.MINOR.PATCH "
                                  f"without leading zeros, each number at most 18 digits long")


class HardeningImageRefTests(unittest.TestCase):
    def test_one_name_with_a_dot_or_named_localhost_is_a_docker_hub_library_image(self):
        # Only a first component followed by another one names a registry, as in Docker.
        cases = {
            "foo.bar": ImageRef("docker.io", "library/foo.bar", None, None),
            "foo.bar:v1": ImageRef("docker.io", "library/foo.bar", "v1", None),
            "localhost": ImageRef("docker.io", "library/localhost", None, None),
            "localhost:v1": ImageRef("docker.io", "library/localhost", "v1", None),
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(parse_image_ref(value), expected)


class HardeningManifestFieldTests(ManifestCase):
    def test_an_invalid_beta_version_is_reported_before_the_fields_after_it(self):
        def mutate(manifest):
            manifest["version"] = "v1.52"
            manifest["source_sha"] = "not-a-sha"

        self.assertRejected(self.mutated(mutate, channel="beta"), r"^invalid version 'v1\.52': expected vMAJOR")

    def test_one_platform_outside_linux_amd64_and_arm64_refuses_the_whole_list(self):
        for platforms in (["linux/amd64", "linux/s390x"], ["windows/amd64", "linux/arm64"],
                          ["linux/amd64", "linux/arm64", "linux/arm/v7"]):
            with self.subTest(platforms=platforms):
                self.assertRejected(
                    self.mutated(lambda m: m["components"]["backend"].__setitem__("platforms", platforms)),
                    "^manifest.components.backend.platforms must list linux/amd64 and/or linux/arm64$")


class HardeningLeftOutOfDigestsTests(unittest.TestCase):
    def test_a_tracked_path_that_is_the_listed_directory_itself_is_not_reported(self):
        # git ls-files names a submodule (or a symlink git tracks) by its own path: it lies below no directory.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "catalog" / "sub").mkdir(parents=True)
            (root / "catalog" / "sub" / ".hidden").write_text("x", encoding="utf-8")
            self.assertEqual(left_out_of_digests(root, ["catalog"], ["catalog"]), [])
            self.assertEqual(left_out_of_digests(root, ["catalog"], ["catalog", "catalog/sub/.hidden"]),
                             ["catalog/sub/.hidden"])


class GeneratorV2Tests(unittest.TestCase):
    """Machine proof inputs are explicitly synthetic, never production evidence."""

    setUp = GeneratorTests.setUp
    run_generator = GeneratorTests.run_generator
    generate = GeneratorTests.generate
    assertGeneratorFails = GeneratorTests.assertGeneratorFails

    def evidence(self):
        from deploy.tests.test_update_compatibility import bind_proof, synthetic_release
        from deploy.update.compatibility import inputs_hash
        generated = self.generate()
        target, _, proof, _ = synthetic_release()
        target["deployment"] = generated["deployment"]
        target["minimum_version"] = generated["minimum_version"]
        proof["deployment"] = copy.deepcopy(generated["deployment"])
        proof["predecessors"][0]["predecessor_deployment"] = copy.deepcopy(generated["deployment"])
        # This fixture's predecessor manifest is not fetched by the generator;
        # the host later verifies its exact hashes/components independently.
        raw = bind_proof(target, proof)
        policy = {"schema_version": 1, "migration": target["migration"],
                  "compatibility": copy.deepcopy(target["compatibility"])}
        policy["compatibility"]["validation"] = None
        policy_path, proof_path = self.base / "policy.json", self.base / "proof.json"
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        proof_path.write_bytes(raw)
        return policy_path, proof_path, policy, proof, inputs_hash(target)

    def test_explicit_schema1_preserves_legacy_manual_generation(self):
        manifest = self.generate("--schema-version", "1")
        self.assertEqual(manifest["schema_version"], 1)
        self.assertNotIn("compatibility", manifest)
        self.assertEqual(manifest["migration"], {"requires_bootstrap": True, "rollback": "manual"})

    def test_patch_minor_major_and_beta_defaults_are_never_certification(self):
        for version in ("v1.52.1", "v1.53.0", "v2.0.0", "v1.53.0-beta.1"):
            manifest = self.generate("--channel", "beta", version=version)
            compatibility = manifest["compatibility"]
            self.assertFalse(compatibility["automatic_apply"])
            self.assertTrue(compatibility["requires_operator_initialization"])
            self.assertEqual(compatibility["automatic_rollback"], {"strategy": "never"})
            self.assertEqual(compatibility["predecessors"], [])
            self.assertNotIn("state_domains", compatibility)
            self.assertEqual(compatibility["contract_version"], 2)
            self.assertEqual(compatibility["qualification_policy"], "standalone-cold-restore-v1")
            self.assertEqual(compatibility["runtime_activation_contract"], "standalone-compose-direct-v1")
            self.assertIsNone(compatibility["validation"])

    def test_affirmative_policy_requires_independent_matching_proof(self):
        policy_path, _, _, _, _ = self.evidence()
        self.assertGeneratorFails(self.run_generator("--release-policy", str(policy_path)), "uncertified_release")

    def test_matching_synthetic_proof_is_pinned_and_inputs_are_deterministic(self):
        policy_path, proof_path, _, _, expected_hash = self.evidence()
        args = ("--release-policy", str(policy_path), "--compatibility-proof", str(proof_path), "--proof-run-id", "42")
        first, second = self.run_generator(*args), self.run_generator(*args)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        result = json.loads(first.stdout)
        self.assertTrue(result["compatibility"]["automatic_apply"])
        self.assertEqual(result["compatibility"]["validation"]["inputs_hash"], expected_hash)
        self.assertEqual(result["compatibility"]["validation"]["sha256"], sha256_hex(proof_path.read_bytes()))

    def test_tampered_failing_and_cross_release_proofs_refused(self):
        policy_path, proof_path, _, original, _ = self.evidence()
        for key, value in (("tag", "v9.0.0"), ("source_sha", "f" * 40), ("repository", "attacker/fork"),
                           ("run_id", 43), ("inputs_hash", "f" * 64), ("predecessors", [])):
            proof = copy.deepcopy(original)
            proof[key] = value
            proof_path.write_text(json.dumps(proof), encoding="utf-8")
            with self.subTest(key=key):
                self.assertGeneratorFails(self.run_generator("--release-policy", str(policy_path),
                    "--compatibility-proof", str(proof_path), "--proof-run-id", "42"), "uncertified_release")
        original["predecessors"][0]["suites"]["no_migrations"] = "skipped"
        proof_path.write_text(json.dumps(original), encoding="utf-8")
        self.assertGeneratorFails(self.run_generator("--release-policy", str(policy_path),
            "--compatibility-proof", str(proof_path), "--proof-run-id", "42"), "uncertified_release")

    def test_changed_component_after_proof_never_gets_certified(self):
        policy_path, proof_path, _, _, _ = self.evidence()
        args = component_args()
        index = next(i for i, item in enumerate(args) if item.startswith("standalone="))
        args[index] = args[index].split("@", 1)[0] + "@sha256:" + "f" * 64
        result = self.run_generator("--release-policy", str(policy_path), "--compatibility-proof", str(proof_path),
                                    "--proof-run-id", "42", components=args)
        self.assertGeneratorFails(result, "uncertified_release")

    def test_proof_run_id_required_and_legacy_rejects_certificate_inputs(self):
        policy_path, proof_path, _, _, _ = self.evidence()
        self.assertGeneratorFails(self.run_generator("--release-policy", str(policy_path),
            "--compatibility-proof", str(proof_path)), "canonical --proof-run-id")
        self.assertGeneratorFails(self.run_generator("--proof-run-id", "42"), "requires --compatibility-proof")
        self.assertGeneratorFails(self.run_generator("--schema-version", "1", "--release-policy", str(policy_path)),
                                  "schema 1 cannot carry release certification")

    def test_unreadable_unknown_and_duplicate_policy_fields_are_sanitized(self):
        policy_path = self.base / "policy.json"
        self.assertGeneratorFails(self.run_generator("--release-policy", str(policy_path)), "unreadable")
        for raw in ('{"schema_version":1,"schema_version":1}', '{"schema_version":2}',
                    '{"command":"SECRET /private/backup"}'):
            policy_path.write_text(raw, encoding="utf-8")
            result = self.run_generator("--release-policy", str(policy_path))
            self.assertGeneratorFails(result, "uncertified_release|unsupported release policy")
            self.assertNotIn("SECRET", result.stderr)


if __name__ == "__main__":
    unittest.main()
