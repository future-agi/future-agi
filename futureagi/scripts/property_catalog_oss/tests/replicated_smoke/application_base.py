"""Read-only registry inspection; never pull/build/start an image or expose auth."""

import hashlib
import io
import json
import tarfile

BASE_REPOSITORY = "futureagi/future-agi-base"
BASE_INDEX = "sha256:a43699e7c8889e5299109767579ecd18bf7f99afc92522a9706da765ced44de2"
BASE_IMAGE = BASE_REPOSITORY + "@" + BASE_INDEX
BASE_ARM64 = "sha256:64f079092e184bb8818e97fd068ed213cf614aca45cbeb78f9233d2dd8721355"
REQUIRED_IMPORTS = (
    "django",
    "rest_framework",
    "clickhouse_driver",
    "psycopg",
    "requests",
)


def verified(raw, digest):
    if "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("registry content digest mismatch")
    return raw


def inspect_registry():
    """Inspect immutable arm64 config and its tiny requirements COPY layer only.

    This is not an installed-package/import proof. The runner must perform that
    proof before opening any fixture database; no missing dependency is installed
    implicitly. In particular, do not fetch the multi-GB dependency layer here.
    """
    import requests

    with requests.Session() as session:
        session.trust_env = False

        def get(url, *, limit=65536, headers=None):
            with session.get(url, headers=headers, timeout=(5, 15), stream=True) as r:
                r.raise_for_status()
                chunks, size = [], 0
                for chunk in r.iter_content(16384):
                    size += len(chunk)
                    if size > limit:
                        raise ValueError("registry observation exceeds byte bound")
                    chunks.append(chunk)
                return b"".join(chunks)

        token = json.loads(
            get(
                "https://auth.docker.io/token?service=registry.docker.io&scope=repository:"
                + BASE_REPOSITORY
                + ":pull"
            )
        )["token"]
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json",
        }
        origin = "https://registry-1.docker.io/v2/" + BASE_REPOSITORY

        def content(kind, digest, limit=65536):
            return verified(
                get(origin + "/" + kind + "/" + digest, limit=limit, headers=headers),
                digest,
            )

        index = json.loads(content("manifests", BASE_INDEX))
        members = [
            m
            for m in index["manifests"]
            if m["platform"] == {"architecture": "arm64", "os": "linux"}
        ]
        if len(members) != 1 or members[0]["digest"] != BASE_ARM64:
            raise ValueError("pinned index arm64 member changed")
        manifest = json.loads(content("manifests", BASE_ARM64))
        config = json.loads(content("blobs", manifest["config"]["digest"]))
        if (config["os"], config["architecture"]) != ("linux", "arm64"):
            raise ValueError("base is not the pinned Linux arm64 runtime")
        layers = iter(manifest["layers"])
        requirement_layers = []
        for entry in config["history"]:
            if entry.get("empty_layer"):
                continue
            layer = next(layers)
            created = entry.get("created_by", "")
            if created.startswith("COPY ") and "requirements.txt" in created:
                requirement_layers.append(layer)
        if len(requirement_layers) != 1 or requirement_layers[0]["size"] > 131072:
            raise ValueError("base has no bounded exact requirements COPY layer")
        layer = requirement_layers[0]
        raw = content("blobs", layer["digest"], 131072)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            members = [
                m
                for m in archive.getmembers()
                if m.name == "app/backend/requirements.txt" and m.isfile()
            ]
            if len(members) != 1 or members[0].size > 65536:
                raise ValueError("requirements archive path/size differs")
            requirements = archive.extractfile(members[0]).read(65537)
        return {
            "image": BASE_IMAGE,
            "platform": "linux/arm64",
            "platform_digest": BASE_ARM64,
            "config_digest": manifest["config"]["digest"],
            "compressed_layer_bytes": sum(
                layer["size"] for layer in manifest["layers"]
            ),
            "layer_count": len(manifest["layers"]),
            "unpacked_layer_bytes": None,
            "unpacked_size_status": "not supplied by the inspected OCI manifest/config; no layer pull performed",
            "declared_volumes": sorted(config["config"].get("Volumes") or {}),
            "working_directory": config["config"].get("WorkingDir"),
            "requirements_sha256": hashlib.sha256(requirements).hexdigest(),
            "requirements": requirements.decode(),
            "runtime_import_check": "pending; requires separately authorized runner",
        }


if __name__ == "__main__":
    evidence = inspect_registry()
    requirements = evidence.pop("requirements")
    evidence["critical_requirements"] = [
        line
        for line in requirements.splitlines()
        if line.casefold().startswith(
            (
                "django==",
                "djangorestframework==",
                "clickhouse-driver==",
                "psycopg",
                "requests==",
            )
        )
    ]
    print(json.dumps(evidence, indent=2))
