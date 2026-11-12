#!/usr/bin/env python3
"""Prepare an authenticated, digest-pinned Helm release without cluster access.

Needs Python 3.9+, PyYAML, Helm 4, ORAS and cosign. This verifies publisher
identity and artifact integrity, NOT migration compatibility or backup readiness.
It never invokes kubectl, installs a release, edits Git, or changes credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import yaml

REPOSITORY = "ghcr.io/future-agi/charts/futureagi"
ISSUER = "https://token.actions.githubusercontent.com"
WORKFLOW = "https://github.com/future-agi/future-agi/.github/workflows/helm-release.yml"
VERSION = re.compile(
    r"(?:0|[1-9][0-9]{0,17})\.(?:0|[1-9][0-9]{0,17})\.(?:0|[1-9][0-9]{0,17})(?:-(?:beta|rc)\.(?:0|[1-9][0-9]{0,17}))?\Z"
)
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
COMPONENT_REPOSITORIES = {
    "backend": "futureagi/future-agi",
    "frontend": "futureagi/frontend",
    "fiCollector": "futureagi/fi-collector",
    "agentccGateway": "futureagi/agentcc-gateway",
    "serving": "futureagi/serving",
    "codeExecutor": "futureagi/code-executor",
}
COMPONENTS = tuple(COMPONENT_REPOSITORIES)
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024


class Refused(Exception):
    """A prerequisite or authentication check did not establish a safe result."""


class UniqueSafeLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise Refused("chart metadata has a non-string or duplicate mapping key")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)


def require(condition, reason):
    if not condition:
        raise Refused(reason)


def command(argv):
    """Bound tool runtime/output and do not echo credential-bearing diagnostics."""
    try:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            result = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                timeout=300,
                check=False,
            )
            require(
                result.returncode == 0,
                f"{argv[0]} failed (exit {result.returncode}); no release prepared",
            )
            require(
                stdout.tell() <= 8 * 1024 * 1024, f"{argv[0]} output exceeds the limit"
            )
            stdout.seek(0)
            return stdout.read().decode("utf-8")
    except FileNotFoundError as exc:
        raise Refused(f"required host tool is missing: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise Refused(f"{argv[0]} exceeded its 300-second deadline") from exc


def metadata(archive, version):
    """Read only bounded metadata from the verified archive; never extract it."""
    require(archive.stat().st_size <= MAX_ARCHIVE_BYTES, "chart archive is too large")
    wanted = {"futureagi/Chart.yaml": None, "futureagi/values.yaml": None}
    seen = set()
    expanded = 0
    with tarfile.open(archive, "r:gz") as package:
        for index, member in enumerate(package):
            require(index < 10000, "chart has too many archive members")
            path = PurePosixPath(member.name)
            require(
                not path.is_absolute()
                and ".." not in path.parts
                and "\\" not in member.name
                and "\x00" not in member.name
                and path.parts
                and path.parts[0] == "futureagi",
                "chart has an unsafe archive path",
            )
            canonical = str(path)
            require(canonical not in seen, "chart has duplicate archive paths")
            seen.add(canonical)
            require(
                member.isfile() or member.isdir(), "chart has a link or special file"
            )
            expanded += member.size
            require(
                expanded <= MAX_EXPANDED_BYTES, "chart expanded size exceeds the limit"
            )
            if canonical in wanted:
                require(
                    member.isfile() and 0 < member.size <= MAX_METADATA_BYTES,
                    "chart metadata size/type is invalid",
                )
                stream = package.extractfile(member)
                require(stream is not None, "chart metadata is unreadable")
                with stream:
                    raw = stream.read(MAX_METADATA_BYTES + 1)
                require(len(raw) == member.size, "chart metadata is truncated")
                wanted[canonical] = yaml.load(
                    raw.decode("utf-8"), Loader=UniqueSafeLoader
                )
    chart, values = wanted.values()
    require(
        isinstance(chart, dict) and isinstance(values, dict),
        "chart metadata is missing",
    )
    require(
        chart.get("name") == "futureagi"
        and chart.get("type") == "application"
        and chart.get("version") == version
        and chart.get("appVersion") == "v" + version,
        "chart name/version/appVersion does not match the requested release",
    )
    require(not chart.get("dependencies"), "unexpected chart dependencies")
    images = values.get("image", {})
    require(
        isinstance(images, dict)
        and images.get("pinDigests") is True
        and images.get("tag", "") == "",
        "published chart disables its default release pins",
    )
    digests = images.get("digests", {})
    require(
        isinstance(digests, dict)
        and set(digests) == set(COMPONENTS)
        and all(isinstance(v, str) and DIGEST.fullmatch(v) for v in digests.values()),
        "published chart is missing exact component digests",
    )
    # _helpers.tpl gives component overrides precedence; a stamped digest is
    # ineffective if a different tag/repository is selected. Report only pins
    # that actually govern the published defaults, not unused configuration.
    for component, repository in COMPONENT_REPOSITORIES.items():
        settings = values.get(component)
        require(
            isinstance(settings, dict) and isinstance(settings.get("image"), dict),
            f"published chart is missing {component} image settings",
        )
        image = settings["image"]
        require(
            image.get("repository") == repository
            and image.get("tag", "") in ("", chart["appVersion"])
            and image.get("digest", "") in ("", digests[component]),
            f"published {component} defaults override the authenticated release pin",
        )
    return chart, dict(digests)


def prepare(version, destination, run=command):
    require(
        isinstance(version, str) and VERSION.fullmatch(version),
        "version must be exact X.Y.Z, X.Y.Z-beta.N or X.Y.Z-rc.N (without v)",
    )
    destination = Path(destination).absolute()
    # Never overwrite an operator's existing chart, values or evidence.
    require(
        not destination.exists() and not destination.is_symlink(),
        "destination must be a new directory",
    )
    require(destination.parent.is_dir(), "destination parent must already exist")
    helm = run(["helm", "version", "--short"]).strip()
    require(
        re.match(r"^v4\.[0-9]+\.[0-9]+(?:[+\s-]|$)", helm),
        "preparation requires Helm 4 for OCI digest pulls",
    )
    digest = run(["oras", "resolve", REPOSITORY + ":" + version]).strip()
    require(DIGEST.fullmatch(digest), "registry did not return one sha256 digest")
    identity = WORKFLOW + "@refs/tags/v" + version
    ref = REPOSITORY + "@" + digest
    signed = json.loads(
        run(
            [
                "cosign",
                "verify",
                "--new-bundle-format=false",
                ref,
                "--certificate-identity",
                identity,
                "--certificate-oidc-issuer",
                ISSUER,
            ]
        )
    )
    require(
        isinstance(signed, list)
        and signed
        and all(
            isinstance(row, dict)
            and isinstance(row.get("critical"), dict)
            and isinstance(row["critical"].get("image"), dict)
            and row["critical"]["image"].get("docker-manifest-digest") == digest
            for row in signed
        ),
        "verified signature payload does not bind the chart digest",
    )
    # Download to a private temporary sibling. Do not leave a success-shaped
    # destination behind on any verification, metadata or download failure.
    with tempfile.TemporaryDirectory(
        prefix=".futureagi-release-", dir=destination.parent
    ) as tmp:
        work = Path(tmp)
        run(["helm", "pull", "oci://" + ref, "--destination", str(work)])
        files = list(work.iterdir())
        require(
            len(files) == 1
            and stat.S_ISREG(files[0].lstat().st_mode)
            and files[0].name.endswith(".tgz"),
            "expected exactly one regular chart archive",
        )
        chart, digests = metadata(files[0], version)
        chart_name = "futureagi-" + version + ".tgz"
        chart_sha = hashlib.sha256(files[0].read_bytes()).hexdigest()
        receipt = {
            "schema_version": 1,
            "kind": "verified_helm_release",
            "repository": REPOSITORY,
            "version": version,
            "app_version": chart["appVersion"],
            "oci_digest": digest,
            "chart_file": chart_name,
            "chart_sha256": chart_sha,
            "signer_identity": identity,
            "oidc_issuer": ISSUER,
            "component_digests": digests,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "migration_compatibility_qualified": False,
            "cluster_modified": False,
            "argocd_source": {
                "repoURL": "oci://" + REPOSITORY,
                "path": ".",
                "targetRevision": digest,
            },
            "flux_source": {
                "url": "oci://" + REPOSITORY,
                "ref": {"digest": digest},
                "verify": {
                    "provider": "cosign",
                    "matchOIDCIdentity": [
                        {
                            "issuer": "^" + re.escape(ISSUER) + "$",
                            "subject": "^" + re.escape(identity) + "$",
                        }
                    ],
                },
            },
        }
        output = work / "prepared"
        output.mkdir(mode=0o700)
        os.replace(files[0], output / chart_name)
        os.chmod(output / chart_name, 0o600)
        receipt_path = output / "release-lock.json"
        with receipt_path.open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.chmod(receipt_path, 0o600)
        # mkdir is the final existence check; rename must never replace an
        # existing directory supplied by another invocation.
        destination.mkdir(mode=0o700)
        for name in (chart_name, "release-lock.json"):
            os.replace(output / name, destination / name)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", required=True, help="Exact chart version, without v"
    )
    parser.add_argument(
        "--destination", type=Path, required=True, help="New private output directory"
    )
    args = parser.parse_args(argv)
    try:
        receipt = prepare(args.version, args.destination)
    except (
        Refused,
        OSError,
        ValueError,
        UnicodeError,
        tarfile.TarError,
        yaml.YAMLError,
    ) as exc:
        print(f"release preparation refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, indent=2, sort_keys=True))
    print(
        "Publisher identity verified; migration compatibility and backups still require operator review. No cluster action performed.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
