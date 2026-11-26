"""Synthetic save/ZIP fixtures for the proof verifier; no daemon, DB or network."""

import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import image_handoff as proof  # noqa: E402


def data(value):
    return json.dumps(value).encode()


class HandoffTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.tar = self.dir / "image.tar"
        self.files = {"oci-layout": data({"imageLayoutVersion": "1.0.0"})}
        self.layers = [self.blob("layer.v1.tar", layer) for layer in (b"base", b"wrapper")]
        ids = [d["digest"] for d in self.layers]
        self.cfg = self.blob("config.v1+json", data({"os": "linux", "architecture": "amd64",
                                                    "rootfs": {"type": "layers", "diff_ids": ids}}))
        self.manifest = self.blob("manifest.v1+json", data({"schemaVersion": 2, "mediaType": proof.OCI + "manifest.v1+json",
                                                           "config": self.cfg, "layers": self.layers}))
        self.manifest["annotations"] = {"io.containerd.image.name": "docker.io/" + proof.ALIAS}
        self.files["index.json"] = data({"schemaVersion": 2, "mediaType": proof.OCI + "index.v1+json",
                                         "manifests": [self.manifest]})
        self.files["manifest.json"] = data([{"Config": self.path(self.cfg), "RepoTags": [proof.ALIAS],
                                             "Layers": [self.path(d) for d in self.layers]}])
        repo, tag = proof.ALIAS.split(":")
        self.files["repositories"] = data({repo: {tag: ids[-1][7:]}})
        for layer in ids:
            self.blob("", data({"id": layer[7:], "os": "linux"}))
        self.source = dict(repository="example/repo", run="17", attempt="1", sha="a" * 40, tree="b" * 40,
                           recipe=proof.RECIPE, platform=proof.PLATFORM)
        self.image = dict(config=self.cfg["digest"], diff_ids=ids, platform=proof.PLATFORM)
        self.record = dict(version=1, source=self.source, image=self.image, manifest=self.manifest["digest"], archive_sha256="c" * 64)
        self.service = {"Name": "/" + proof.SERVICE, "Config": {"Image": proof.ALIAS}, "Image": self.image["config"],
                        "State": {"Status": "exited", "ExitCode": 0}}

    @staticmethod
    def path(desc):
        return proof.BLOB + desc["digest"][7:]

    def blob(self, media, payload):
        desc = dict(mediaType=proof.OCI + media, digest="sha256:" + hashlib.sha256(payload).hexdigest(), size=len(payload))
        self.files[self.path(desc)] = payload
        return desc

    def write_tar(self):
        with tarfile.open(self.tar, "w:") as tar:
            for name, contents in self.files.items():
                info = tarfile.TarInfo(name)
                info.size = len(contents)
                tar.addfile(info, io.BytesIO(contents))

    def check_record(self, record):
        proof.check_set(record, self.source, self.record["archive_sha256"], self.manifest["digest"], self.image["config"])

    def test_one_coherent_image(self):
        self.write_tar()
        self.assertEqual(proof.check_archive(self.tar, self.image), self.manifest["digest"])
        self.check_record(self.record)
        proof.check_service(self.image, self.image, self.service)

    def test_wrong_sha_variant_platform_or_attempt(self):
        for field, value in (("sha", "d" * 40), ("recipe", "backend-slim"),
                             ("platform", "linux/arm64"), ("attempt", "2")):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "wrong candidate"):
                self.check_record(dict(self.record, source={**self.source, field: value}))

    def test_wrong_actual_platform(self):
        with self.assertRaisesRegex(ValueError, "wrong platform"):
            proof.image_identity({"Os": "linux", "Architecture": "arm64"})

    def test_descriptor_digest_mismatch(self):
        self.files[self.path(self.layers[0])] = b"0000"  # same size, different bytes
        self.write_tar()
        with self.assertRaisesRegex(ValueError, "descriptor digest mismatch"):
            proof.check_archive(self.tar, self.image)

    def test_wrong_descriptor_platform(self):
        index = json.loads(self.files["index.json"])
        index["manifests"][0]["platform"] = {"os": "linux", "architecture": "arm64"}
        self.files["index.json"] = data(index)
        self.write_tar()
        with self.assertRaisesRegex(ValueError, "wrong descriptor platform"):
            proof.check_archive(self.tar, self.image)

    def test_descriptor_size_mismatch(self):
        self.files[self.path(self.layers[0])] += b"0"
        self.write_tar()
        with self.assertRaisesRegex(ValueError, "descriptor size mismatch"):
            proof.check_archive(self.tar, self.image)

    def test_docker_load_view_cannot_swap_layers(self):
        rows = json.loads(self.files["manifest.json"])
        rows[0]["Layers"].reverse()
        self.files["manifest.json"] = data(rows)
        self.write_tar()
        with self.assertRaisesRegex(ValueError, "Docker load view"):
            proof.check_archive(self.tar, self.image)

    def test_stale_alias_absent_service_or_stale_container(self):
        stale = "sha256:" + "f" * 64
        for image, service, error in [(dict(self.image, config=stale), self.service, "alias"),
                                      (self.image, None, "missing probe service"),
                                      (self.image, dict(self.service, Image=stale), "another config")]:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                proof.check_service(image, self.image, service)

    def test_mixed_donor_set(self):
        with self.assertRaisesRegex(ValueError, "mixed donor set"):
            self.check_record(dict(self.record, manifest="sha256:" + "0" * 64))

    def stage_download(self, *, zip_hash=None, archive_hash=None):
        self.write_tar()
        archive_hash = archive_hash or proof.file_hash(self.tar)
        record = dict(self.record, archive_sha256=archive_hash, archive_bytes=self.tar.stat().st_size)
        zpath = self.dir / "download.zip"
        with zipfile.ZipFile(zpath, "w") as z:
            z.write(self.tar, "image.tar")
            z.writestr("donor.json", data(record))
        self.tar.unlink()
        zip_hash = zip_hash or proof.file_hash(zpath)
        meta = dict(id=5, name=proof.artifact_name(self.source), expired=False,
                    digest="sha256:" + zip_hash, workflow_run={"id": 17})
        (self.dir / "artifact.json").write_bytes(data(meta))
        env = dict(ARTIFACT_ID="5", ZIP_DIGEST=zip_hash, ARCHIVE_SHA256=archive_hash,
                   MANIFEST_DIGEST=self.manifest["digest"], CONFIG_DIGEST=self.image["config"])
        return zpath, env

    def test_wrong_zip_or_inner_sha_fails_before_import(self):
        for field, error in [("zip_hash", "downloaded ZIP"), ("archive_hash", "inner archive")]:
            zpath, env = self.stage_download(**{field: "0" * 64})
            with self.subTest(error=error), patch.dict(proof.os.environ, env), patch.object(proof, "binding", return_value=self.source), \
                    patch.object(proof.subprocess, "run", side_effect=AssertionError("unexpected import")), self.assertRaisesRegex(ValueError, error):
                proof.receive(self.dir, zpath)

    def test_existing_donor_alias_fails_before_load_or_run(self):
        # Even an alias pointing at the right config could hide a no-op load.
        zpath, env = self.stage_download()
        with (
            patch.dict(proof.os.environ, env),
            patch.object(proof, "binding", return_value=self.source),
            patch.object(proof.subprocess, "check_output", return_value=self.image["config"].encode()) as ls,
            patch.object(proof.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "alias already exists"):
                proof.receive(self.dir, zpath)
            ls.assert_called_once_with(["docker", "image", "ls", "--quiet", proof.ALIAS])
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
