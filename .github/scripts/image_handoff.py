"""Qualify Docker 28.5.2's single-image save/load format, not arbitrary OCI tars."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

RECIPE = "backend-standard"
PLATFORM = "linux/amd64"
ALIAS = "futureagi/future-agi:e2e-ci"
SERVICE = "handoff-backend"
OCI = "application/vnd.oci.image."
BLOB = "blobs/sha256/"
SHA256 = r"[a-f0-9]{64}"
JSON_LIMIT = 2 * 1024 * 1024


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(stream):
    return hashlib.file_digest(stream, "sha256").hexdigest()


def file_hash(path):
    with path.open("rb") as stream:
        return digest(stream)


def only(items, message):
    require(isinstance(items, list) and len(items) == 1, message)
    return items[0]


def binding():
    env = os.environ
    sha, tree = subprocess.check_output(["git", "rev-parse", "HEAD", "HEAD^{tree}"], text=True).splitlines()
    require(sha == env["GITHUB_SHA"], "checkout does not match candidate")
    attempt = env.get("DONOR_ATTEMPT", env["GITHUB_RUN_ATTEMPT"])
    require(0 < int(attempt) <= int(env["GITHUB_RUN_ATTEMPT"]), "invalid donor attempt")
    return dict(repository=env["GITHUB_REPOSITORY"], run=env["GITHUB_RUN_ID"], attempt=attempt,
                sha=sha, tree=tree, recipe=RECIPE, platform=PLATFORM)


def artifact_name(source):
    return "e2e-image-v1-{run}-{attempt}-{sha}-linux-amd64-{recipe}".format(**source)


def image_identity(info):
    require(info["Os"] == "linux" and info["Architecture"] == "amd64" and not info.get("Variant"), "wrong platform")
    config, layers = info["Id"], info["RootFS"]["Layers"]
    require(info["RootFS"]["Type"] == "layers" and layers and all(
        re.fullmatch("sha256:" + SHA256, value) for value in [config, *layers]), "invalid image identity")
    return dict(config=config, diff_ids=layers, platform=PLATFORM)


def inspect(ref, kind):
    return only(json.loads(subprocess.check_output(["docker", kind, "inspect", ref])), f"missing or ambiguous {kind}")


def check_archive(path, image):
    """Verify both views: OCI descriptors and the legacy manifest docker load consumes."""
    with tarfile.open(path, "r:") as tar:
        files = {}
        for member in tar:
            name = member.name.removeprefix("./")
            if member.isdir() and name in {"", ".", "blobs", "blobs/sha256"}:
                continue
            require(member.isfile() and name not in files and (
                name in {"oci-layout", "index.json", "repositories", "manifest.json"} or
                re.fullmatch(BLOB + SHA256, name)), "unexpected or duplicate archive member")
            files[name] = member

        def read_json(name):
            require(name in files and files[name].size <= JSON_LIMIT, "missing or oversized image metadata")
            return json.load(tar.extractfile(files[name]))

        verified = set()

        def verify_blob(desc, media):
            dgst = desc["digest"]
            require(desc["mediaType"] == OCI + media and re.fullmatch("sha256:" + SHA256, dgst), "bad descriptor")
            name = BLOB + dgst[7:]
            require(name in files and files[name].size == desc["size"], "descriptor size mismatch")
            if name not in verified:
                require(digest(tar.extractfile(files[name])) == dgst[7:], "descriptor digest mismatch")
                verified.add(name)
            return name

        require(read_json("oci-layout") == {"imageLayoutVersion": "1.0.0"}, "not an OCI layout")
        index = read_json("index.json")
        require(index["schemaVersion"] == 2 and index["mediaType"] == OCI + "index.v1+json", "invalid OCI index")
        desc = only(index["manifests"], "expected exactly one platform manifest")
        if "platform" in desc:
            platform = desc["platform"]
            require(platform["os"] == "linux" and platform["architecture"] == "amd64" and not platform.get("variant"),
                    "wrong descriptor platform")
        require(desc["annotations"]["io.containerd.image.name"] == "docker.io/" + ALIAS, "wrong OCI alias")
        manifest = read_json(verify_blob(desc, "manifest.v1+json"))
        require(manifest["schemaVersion"] == 2 and manifest["mediaType"] == desc["mediaType"], "invalid OCI manifest")
        config_path = verify_blob(manifest["config"], "config.v1+json")
        cfg = read_json(config_path)
        require(manifest["config"]["digest"] == image["config"], "export changed config bytes")
        require((cfg["os"], cfg["architecture"]) == ("linux", "amd64") and not cfg.get("variant"), "wrong OCI platform")
        require(cfg["rootfs"] == {"type": "layers", "diff_ids": image["diff_ids"]}, "config diff IDs differ")
        # Classic Docker exports uncompressed layer tars, so each blob digest IS its diff ID.
        require([d["digest"] for d in manifest["layers"]] == image["diff_ids"], "layer order or digest differs")
        layer_paths = [verify_blob(d, "layer.v1.tar") for d in manifest["layers"]]
        legacy = only(read_json("manifest.json"), "expected one Docker manifest")
        require(legacy["RepoTags"] == [ALIAS] and legacy["Config"] == config_path and legacy["Layers"] == layer_paths,
                "Docker load view disagrees with OCI manifest")
        repo, tag = ALIAS.split(":")
        require(read_json("repositories") == {repo: {tag: image["diff_ids"][-1][7:]}}, "wrong legacy repository")
        # Moby also emits one V1 compatibility JSON per layer. Allow only that metadata, never extra payloads.
        extras = {n for n in files if n.startswith(BLOB)} - verified
        require(len(extras) == len(layer_paths), "unexpected extra blobs")
        for name in extras:
            meta = read_json(name)
            require(meta.get("os") == "linux" and re.fullmatch(SHA256, meta.get("id", ""))
                    and digest(tar.extractfile(files[name])) == name[len(BLOB):], "invalid V1 metadata blob")
        return desc["digest"]


def check_artifact(meta, source, artifact_id, zip_hash):
    require(re.fullmatch(SHA256, zip_hash) and meta["digest"] == "sha256:" + zip_hash, "wrong ZIP digest")
    require(str(meta["id"]) == artifact_id and meta["name"] == artifact_name(source)
            and str(meta["workflow_run"]["id"]) == source["run"] and not meta["expired"], "wrong run artifact")


def check_set(record, source, archive_hash, manifest_digest, config_digest):
    require(record.get("version") == 1, "unknown donor record version")
    require(record["source"] == source, "wrong candidate, donor attempt or recipe")
    require(record["archive_sha256"] == archive_hash and record["manifest"] == manifest_digest
            and record["image"]["config"] == config_digest and record["image"]["platform"] == PLATFORM,
            "mixed donor set")


def check_service(image, expected, service):
    require(image == expected, "imported alias does not resolve to donor image")
    require(service and service["Name"] == "/" + SERVICE and service["Config"]["Image"] == ALIAS, "missing probe service")
    require(service["Image"] == expected["config"], "container ran another config")
    require(service["State"]["Status"] == "exited" and service["State"]["ExitCode"] == 0, "container probe failed")


def export_image(directory):
    source = binding()
    archive = directory / "image.tar"
    info = inspect(ALIAS, "image")
    image = image_identity(info)  # capture BEFORE export
    require(info["Size"] < shutil.disk_usage(directory).free, "not enough room to stage native archive")
    started = time.monotonic()
    subprocess.run(["docker", "image", "save", "--platform", PLATFORM, "-o", archive, ALIAS], check=True)
    saved = time.monotonic()
    manifest = check_archive(archive, image)
    archive_hash = file_hash(archive)
    record = dict(version=1, source=source, image=image, manifest=manifest, archive_sha256=archive_hash,
                  archive_bytes=archive.stat().st_size, save_seconds=round(saved - started, 2),
                  donor_verify_seconds=round(time.monotonic() - saved, 2))
    (directory / "donor.json").write_text(json.dumps(record))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as out:
        for k, v in dict(name=artifact_name(source), attempt=source["attempt"], archive_sha256=archive_hash,
                         manifest_digest=manifest, config_digest=image["config"]).items():
            print(f"{k}={v}", file=out)
    print(json.dumps(record))


def receive(directory, zip_path):
    env = os.environ
    source = binding()
    started = time.monotonic()
    check_artifact(json.loads((directory / "artifact.json").read_text()), source, env["ARTIFACT_ID"], env["ZIP_DIGEST"])
    require(file_hash(zip_path) == env["ZIP_DIGEST"], "downloaded ZIP digest mismatch")
    archive = directory / "image.tar"
    with zipfile.ZipFile(zip_path) as z:
        require(sorted(z.namelist()) == ["donor.json", "image.tar"], "unexpected ZIP members")
        require(z.getinfo("donor.json").file_size <= JSON_LIMIT, "oversized donor record")
        record = json.loads(z.read("donor.json"))
        check_set(record, source, env["ARCHIVE_SHA256"], env["MANIFEST_DIGEST"], env["CONFIG_DIGEST"])
        size = z.getinfo("image.tar").file_size
        require(size == record["archive_bytes"] and size < shutil.disk_usage(directory).free, "archive does not fit")
        with z.open("image.tar") as src, archive.open("xb") as dest:
            shutil.copyfileobj(src, dest, length=1024 * 1024)
    zip_size = zip_path.stat().st_size
    zip_path.unlink()
    require(file_hash(archive) == env["ARCHIVE_SHA256"], "inner archive SHA-256 mismatch")
    require(check_archive(archive, record["image"]) == env["MANIFEST_DIGEST"], "wrong platform manifest")
    require(not subprocess.check_output(["docker", "image", "ls", "--quiet", ALIAS]).strip(),
            "expected a clean consumer, alias already exists")
    verified = time.monotonic()
    subprocess.run(["docker", "image", "load", "--platform", PLATFORM, "-i", archive], check=True)
    loaded = time.monotonic()
    archive.unlink()
    image = image_identity(inspect(ALIAS, "image"))
    require(image == record["image"], "imported alias does not resolve to donor image")
    # A successful one-shot, with no database, network, mounts or fallback pull. Check the
    # standard (not slim) variant's build-time marker AND a file only the E2E wrapper adds.
    subprocess.run(["docker", "run", "--pull=never", "--platform", PLATFORM, "--network=none", "--read-only",
                    "--no-healthcheck", "--name", SERVICE, "--entrypoint", "/bin/sh", ALIAS, "-ec",
                    'test "$(cat /usr/local/lib/futureagi/ffmpeg-flavor)" = debian; '
                    'test -s /app/backend/tfc/settings/e2e.py; test "$FUTURE_AGI_TELEMETRY_DISABLED" = true'], check=True)
    service = inspect(SERVICE, "container")
    check_service(image, record["image"], service)
    receipt = dict(**record, artifact_id=env["ARTIFACT_ID"], zip_sha256=env["ZIP_DIGEST"], zip_bytes=zip_size,
                   consumer=env["CONSUMER"], consumer_attempt=env["GITHUB_RUN_ATTEMPT"], imported=image,
                   service=SERVICE, alias=ALIAS, container_id=service["Id"], container_image=service["Image"],
                   exit_code=service["State"]["ExitCode"], consumer_verify_seconds=round(verified - started, 2),
                   load_seconds=round(loaded - verified, 2))
    (directory / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    if sys.argv[1] == "export":
        export_image(Path(sys.argv[2]))
    elif sys.argv[1] == "receive":
        receive(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        sys.exit("expected export or receive")
