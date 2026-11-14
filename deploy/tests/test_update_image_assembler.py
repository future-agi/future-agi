"""Daemon-free image assembly, including a genuinely empty base-image cache."""

import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from deploy.tests.update_e2e import harness  # noqa: E402


class ImageAssemblerTests(unittest.TestCase):
    def setUp(self):
        self.cached = False
        self.pull_code = 0
        self.calls = []
        self.layer = b"synthetic base layer, never sent to a daemon"
        self.diff_id = "sha256:" + hashlib.sha256(self.layer).hexdigest()
        self.base_config = {
            "architecture": "arm64", "os": "linux", "variant": "v8",
            "rootfs": {"type": "layers", "diff_ids": [self.diff_id]},
            "config": {"Env": ["PATH=/bin:/usr/bin"]},
        }
        for patcher in (mock.patch.dict(os.environ, {"DOCKER_CONTEXT": "assembler-test-context"}),
                        mock.patch.object(harness.subprocess, "run", side_effect=self.docker)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def docker(self, args, **kwargs):
        # All commands must inherit the same selected daemon, not silently use
        # a default context or a replacement environment on the pull path.
        self.assertIsNone(kwargs["env"])
        self.assertEqual(os.environ["DOCKER_CONTEXT"], "assembler-test-context")
        self.calls.append(args)
        code, stderr = 0, ""
        if args == ["docker", "image", "inspect", harness.BASE_IMAGE]:
            code = 0 if self.cached else 1
            stderr = "" if self.cached else "No such image: " + harness.BASE_IMAGE
        elif args == ["docker", "pull", harness.BASE_IMAGE]:
            code = self.pull_code
            stderr = "registry unavailable: synthetic pull cause" if code else ""
            self.cached = not code
        elif args[:3] == ["docker", "save", harness.BASE_IMAGE]:
            if not self.cached:
                code, stderr = 1, "Error response from daemon: reference does not exist"
            else:
                self.assertEqual(args[3], "-o")
                with tarfile.open(args[4], "w") as archive:
                    for name, data in {
                        "manifest.json": json.dumps([{"Config": "config.json", "Layers": ["layer.tar"]}]).encode(),
                        "config.json": json.dumps(self.base_config).encode(),
                        "layer.tar": self.layer,
                    }.items():
                        entry = tarfile.TarInfo(name)
                        entry.size = len(data)
                        archive.addfile(entry, io.BytesIO(data))
        else:
            self.fail("unexpected Docker command: " + repr(args))
        return subprocess.CompletedProcess(args, code, "", stderr)

    def assert_base(self, actual):
        layers, diff_ids, platform, env = actual
        self.assertEqual([gzip.decompress(layer) for layer in layers], [self.layer])
        self.assertEqual(diff_ids, [self.diff_id])
        self.assertEqual(platform, {"architecture": "arm64", "os": "linux", "variant": "v8"})
        self.assertEqual(env, ["PATH=/bin:/usr/bin"])

    def test_missing_base_is_pulled_before_save_on_the_selected_daemon(self):
        self.assert_base(harness.ImageAssembler._base_image())
        self.assertEqual([args[:3] for args in self.calls], [
            ["docker", "image", "inspect"], ["docker", "pull", "busybox:1.37"],
            ["docker", "save", "busybox:1.37"],
        ])

    def test_cached_base_is_saved_without_pull_or_cache_mutation(self):
        self.cached = True
        self.assert_base(harness.ImageAssembler._base_image())
        self.assertEqual([args[:3] for args in self.calls], [
            ["docker", "image", "inspect"], ["docker", "save", "busybox:1.37"],
        ])

    def test_failed_pull_preserves_cause_and_never_saves(self):
        self.pull_code = 17
        with self.assertRaises(harness.CommandFailed) as caught:
            harness.ImageAssembler._base_image()
        self.assertEqual(caught.exception.result.args, ["docker", "pull", "busybox:1.37"])
        self.assertEqual(caught.exception.result.code, 17)
        self.assertIn("registry unavailable: synthetic pull cause", str(caught.exception))
        self.assertEqual(len(self.calls), 2)

    def test_assembled_image_preserves_fakeapp_content_and_metadata(self):
        registry = mock.Mock(prefix="fagi-upd-e2e-assembler-")
        assembler = harness.ImageAssembler(registry)
        assembler.push("test-tag", "v1.2.3", "ok", "test-flavor")
        blobs = [call.args[0] for call in registry.upload_blob.call_args_list]
        base, app, raw_config = blobs
        self.assertEqual(gzip.decompress(base), self.layer)
        with tarfile.open(fileobj=io.BytesIO(gzip.decompress(app))) as archive:
            self.assertEqual(archive.getnames(), ["start.sh"])
            self.assertEqual(archive.extractfile("start.sh").read(), (harness.FAKEAPP_CONTEXT / "start.sh").read_bytes())
            self.assertEqual(archive.getmember("start.sh").mode, 0o755)
        config = json.loads(raw_config)
        self.assertEqual(config["rootfs"]["diff_ids"], [self.diff_id, "sha256:" + hashlib.sha256(gzip.decompress(app)).hexdigest()])
        self.assertEqual({key: config[key] for key in ("architecture", "os", "variant")}, assembler.platform)
        runtime = config["config"]
        self.assertEqual(runtime["Env"], ["PATH=/bin:/usr/bin", "VERSION=v1.2.3", "MODE=ok",
                                          "SLOW_SECONDS=" + str(harness.SLOW_SECONDS), "FLAVOR=test-flavor"])
        self.assertEqual(runtime["Entrypoint"], ["/bin/sh", "/start.sh"])
        self.assertIsNone(runtime["Cmd"])
        self.assertEqual(runtime["ExposedPorts"], {"3000/tcp": {}, "8000/tcp": {}})
        self.assertEqual(runtime["Labels"]["org.opencontainers.image.version"], "v1.2.3")
        self.assertEqual(runtime["Labels"]["org.opencontainers.image.revision"], "test-flavor")
        self.assertEqual(runtime["Healthcheck"], {
            "Test": ["CMD-SHELL", "wget -q -O /dev/null http://127.0.0.1:8000/health/ || exit 1"],
            "Interval": 2_000_000_000, "Timeout": 2_000_000_000, "StartPeriod": 5_000_000_000, "Retries": 3,
        })
        tag, raw_manifest, media_type = registry.put_manifest.call_args.args
        self.assertEqual(tag, "test-tag")
        manifest = json.loads(raw_manifest)
        self.assertEqual(media_type, manifest["mediaType"])
        for descriptor, blob in zip([manifest["config"], *manifest["layers"]], [raw_config, base, app]):
            self.assertEqual(descriptor["digest"], "sha256:" + hashlib.sha256(blob).hexdigest())
            self.assertEqual(descriptor["size"], len(blob))
        dockerfile = (harness.FAKEAPP_CONTEXT / "Dockerfile").read_text()
        self.assertIn("FROM " + harness.BASE_IMAGE + "\n", dockerfile)
        self.assertIn('ENTRYPOINT ["/bin/sh", "/start.sh"]', dockerfile)


class ReleaseImageAssemblyTests(unittest.TestCase):
    """Static build-DAG regressions, not qualification of a product image."""

    def setUp(self):
        from deploy.tests.test_update_release_static import load

        workflows = ROOT / ".github" / "workflows"
        self.workflow = load(workflows / "build-image-multiarch.yml")
        self.release = load(workflows / "release-images.yml")
        self.steps = self.workflow["jobs"]["build"]["steps"]

    def step(self, name):
        matches = [step for step in self.steps if step.get("name") == name]
        self.assertEqual(len(matches), 1, name)
        return matches[0]

    def test_the_single_build_digest_is_checked_and_exported_without_a_rebuild(self):
        builds = [step for step in self.steps
                  if step.get("uses", "").startswith("docker/build-push-action@")]
        self.assertEqual(len(builds), 1)
        build = builds[0]
        self.assertEqual(build["id"], "build")
        self.assertEqual(build["with"]["context"], "${{ inputs.context }}")
        self.assertEqual(build["with"]["file"], "${{ inputs.dockerfile }}")
        digest = "${{ steps.build.outputs.digest }}"
        image = "docker.io/${{ inputs.image }}@" + digest
        labels = self.step("OCI labels (linux/${{ matrix.arch }})")
        verify = self.step("Verify the image on linux/${{ matrix.arch }}")
        budget = self.step("Size budget (linux/${{ matrix.arch }})")
        export = self.step("Export digest")
        self.assertEqual(labels["env"]["IMAGE"], image)
        self.assertEqual(verify["env"]["IMAGE"], image)
        self.assertEqual(budget["env"]["NEW_REF"], image)
        self.assertEqual(export["env"]["DIGEST"], digest)
        ordered = [build, labels, verify, budget, export]
        positions = [self.steps.index(step) for step in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertTrue(all("continue-on-error" not in step for step in ordered))

    def test_native_legs_and_multiarch_publication_still_use_the_build_digests(self):
        inputs = self.workflow["on"]["workflow_call"]["inputs"]
        self.assertEqual(json.loads(inputs["arches"]["default"]), ["amd64", "arm64"])
        self.assertNotIn("standalone-descriptors", inputs)
        build = self.workflow["jobs"]["build"]
        self.assertEqual(build["runs-on"],
                         "${{ matrix.arch == 'arm64' && 'ubuntu-24.04-arm' || 'ubuntu-latest' }}")
        self.assertEqual(build["strategy"]["matrix"]["arch"], "${{ fromJSON(inputs.arches) }}")
        exports = [step for step in self.steps
                   if step.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(len(exports), 1)
        self.assertEqual(exports[0]["with"]["name"],
                         "digests_${{ inputs.cache-scope }}${{ inputs.tag-suffix }}_${{ matrix.arch }}")
        self.assertEqual(exports[0]["with"]["if-no-files-found"], "error")
        publish = self.workflow["jobs"]["publish"]
        self.assertEqual(publish["needs"], ["prepare", "build"])
        self.assertEqual(publish["outputs"]["digest"], "${{ steps.digest.outputs.digest }}")
        self.assertEqual(self.workflow["on"]["workflow_call"]["outputs"]["digest"]["value"],
                         "${{ jobs.publish.outputs.digest }}")

    def test_release_standalone_keeps_pinned_components_and_binary_and_size_checks(self):
        jobs = self.release["jobs"]
        standalone = jobs["standalone"]
        self.assertEqual(standalone["needs"], ["guard", "standalone-inputs"])
        self.assertEqual(standalone["uses"], "./.github/workflows/build-image-multiarch.yml")
        inputs = standalone["with"]
        self.assertNotIn("standalone-descriptors", inputs)
        self.assertEqual(inputs["context"], "./deploy/standalone")
        self.assertEqual(inputs["dockerfile"], "./deploy/standalone/Dockerfile")
        self.assertEqual(inputs["build-args"], "${{ needs.standalone-inputs.outputs.build-args }}")
        self.assertEqual(inputs["verify-command"], "/opt/futureagi/bin/verify-binaries")
        self.assertEqual(inputs["standalone-install-total"], "true")
        self.assertIn("STANDALONE_SIZE_BUDGET_PERCENT", inputs["size-budget-percent"])
        pin = next(step for step in jobs["standalone-inputs"]["steps"] if step.get("id") == "pin")
        for arg in ("BACKEND_IMAGE", "FRONTEND_IMAGE", "FI_COLLECTOR_IMAGE", "AGENTCC_GATEWAY_IMAGE"):
            self.assertIn("pin " + arg + " ", pin["run"])
        self.assertIn("%s=docker.io/%s:%s@%s", pin["run"])

    def test_standalone_context_keeps_only_runtime_copies_and_normal_startup(self):
        directory = ROOT / "deploy" / "standalone"
        dockerfile = (directory / "Dockerfile").read_text()
        copies = [line.split()[1] for line in dockerfile.splitlines()
                  if line.startswith("COPY ") and not line.startswith("COPY --from=")]
        self.assertEqual(copies, ["nginx.conf", "starting.html", "supervisord.conf", "bin/"])
        self.assertTrue(all((directory / source).exists() for source in copies))
        self.assertIn('ENTRYPOINT ["/opt/futureagi/bin/start"]', dockerfile)
        self.assertIn("HEALTHCHECK --interval=15s --timeout=10s --start-period=3600s --retries=5", dockerfile)
        self.assertIn("/opt/futureagi/bin/bootstrap.py /opt/futureagi/bin/verify-binaries", dockerfile)
        for label in ("version", "revision", "created"):
            self.assertIn("org.opencontainers.image." + label + "=", dockerfile)


if __name__ == "__main__":
    unittest.main()
