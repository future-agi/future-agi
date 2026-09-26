#!/usr/bin/env python3
"""Compressed-size budgets for the Future AGI images, checked before a tag moves.

Sizes are the compressed layer bytes in the registry manifest, which is what
`docker pull` downloads. PostHog's hobby stack went from 489 MB to 2,370 MB
(and from 11 to 38 services) with no such gate; this is the gate.

  # One freshly pushed single-architecture image, before any tag points at
  # it (build-image-multiarch.yml on release, platform-ci.yml on PRs):
  scripts/image_size_budget.py check --image futureagi/platform --arch amd64 \\
      --ref docker.io/futureagi/platform@sha256:<digest> \\
      --baseline docker.io/futureagi/platform:latest \\
      --max-growth-percent 10 --default-install

  # Every budgeted image of a published release, both architectures:
  scripts/image_size_budget.py report --tag v1.42.0

`check` fails when the image is over its budget in image_size_budget.json
(an image without an entry fails too, so a new image cannot skip the gate);
when it grew more than --max-growth-percent over --baseline (a pull request
that carries the $WAIVER_LABEL label is let through with a warning); or, with
--default-install, when the layers a fresh standalone install downloads (this
image plus the other images of docker-compose.yml, each layer counted once)
are over the default-install budget. A deliberate increase changes the budget
file in the same pull request, where review sees it. It also prints the upgrade delta: the
bytes of layers --baseline does not have, which is what an existing install
downloads to upgrade. A registry build cache keeps that delta small; a delta
close to the whole image means the cache missed.

Manifests are read with `docker buildx imagetools inspect --raw` (uses the
runner's registry logins; works for a local registry), or anonymously from
Docker Hub and ghcr.io when docker is not installed. MB = 10^6 bytes.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

BUDGETS = Path(__file__).with_name("image_size_budget.json")
MANIFEST_TYPES = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)

# ---------------------------------------------------------------- references


def parse_ref(ref: str) -> tuple[str, str, str]:
    """(registry, repository, tag or digest) of an image reference."""
    name, _, digest = ref.partition("@")
    first, _, rest = name.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        registry, path = first, rest
    else:
        registry, path = "docker.io", name
    tag = "latest"
    if ":" in path.rsplit("/", 1)[-1]:
        path, tag = path.rsplit(":", 1)
    if registry == "docker.io" and "/" not in path:
        path = f"library/{path}"
    return registry, path, digest or tag


def repository(ref: str) -> str:
    """The repository of a reference as it is written in the budget file."""
    registry, path, _ = parse_ref(ref)
    if registry != "docker.io":
        return f"{registry}/{path}"
    return path.removeprefix("library/")


# ----------------------------------------------------------------- manifests


class DockerRegistry:
    """Reads through `docker buildx imagetools`: the runner's logins, any registry."""

    @staticmethod
    def _imagetools(*args: str) -> dict:
        proc = subprocess.run(
            ["docker", "buildx", "imagetools", "inspect", *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode:
            raise LookupError(
                (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}"
            )
        return json.loads(proc.stdout)

    def manifest(self, ref: str) -> dict:
        return self._imagetools("--raw", ref)

    def architecture(self, ref: str, manifest: dict) -> str:
        return self._imagetools("--format", "{{json .Image}}", ref).get(
            "architecture", ""
        )


class HttpRegistry:
    """Anonymous reads from Docker Hub and ghcr.io, for a laptop without docker."""

    def _get(self, ref: str, kind: str, reference: str) -> dict:
        registry, path, _ = parse_ref(ref)
        if registry == "docker.io":
            base = "https://registry-1.docker.io"
            auth = (
                "https://auth.docker.io/token?service=registry.docker.io"
                f"&scope=repository:{path}:pull"
            )
        elif registry == "ghcr.io":
            base = "https://ghcr.io"
            auth = f"https://ghcr.io/token?scope=repository:{path}:pull"
        else:
            raise LookupError(
                f"{registry}: only Docker Hub and ghcr.io are readable without docker"
            )
        try:
            token = _http_json(auth)["token"]
            return _http_json(f"{base}/v2/{path}/{kind}/{reference}", token)
        except urllib.error.HTTPError as exc:
            # Docker Hub answers 401, not 404, for a repository it does not have.
            raise LookupError(f"{ref}: HTTP {exc.code}") from None

    def manifest(self, ref: str) -> dict:
        return self._get(ref, "manifests", parse_ref(ref)[2])

    def architecture(self, ref: str, manifest: dict) -> str:
        return self._get(ref, "blobs", manifest["config"]["digest"]).get(
            "architecture", ""
        )


def _http_json(url: str, token: str | None = None) -> dict:
    request = urllib.request.Request(url, headers={"Accept": MANIFEST_TYPES})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        return json.load(response)


def default_registry() -> DockerRegistry | HttpRegistry:
    return DockerRegistry() if shutil.which("docker") else HttpRegistry()


def layers(
    ref: str, arch: str, registry, built_for_arch: bool = False
) -> dict[str, int]:
    """Layer digest -> compressed size of the linux/<arch> image of ref.

    built_for_arch: ref is the single-architecture image a build leg just
    pushed for <arch> (push by digest), so its config need not be read.
    """
    doc = registry.manifest(ref)
    if "manifests" in doc:
        match = [
            entry["digest"]
            for entry in doc["manifests"]
            if entry.get("platform", {}).get("os") == "linux"
            and entry["platform"].get("architecture") == arch
        ]
        if not match:
            raise LookupError(f"{ref} has no linux/{arch} image")
        host, path, _ = parse_ref(ref)
        doc = registry.manifest(f"{host}/{path}@{match[0]}")
    elif not built_for_arch:
        # A single-architecture tag (older releases were amd64 only).
        actual = registry.architecture(ref, doc)
        if actual != arch:
            raise LookupError(f"{ref} is a linux/{actual or 'unknown'} image only")
    if "layers" not in doc:
        raise LookupError(f"{ref}: not an image manifest")
    return {layer["digest"]: int(layer["size"]) for layer in doc["layers"]}


def mb(layer_sizes: dict[str, int]) -> float:
    return sum(layer_sizes.values()) / 1e6


# ------------------------------------------------------------------- budgets


def load_budgets(path: Path = BUDGETS) -> dict:
    return json.loads(path.read_text())


def find_entry(cfg: dict, image: str, tag_suffix: str = "") -> dict:
    for entry in cfg["images"]:
        if entry["image"] == image and entry.get("tag_suffix", "") == tag_suffix:
            return entry
    raise KeyError(
        f"no budget for {image}{' (' + tag_suffix + ')' if tag_suffix else ''} "
        f"in {BUDGETS.name}; add one before publishing it"
    )


def arches_of(cfg: dict, entry: dict) -> list[str]:
    return list(entry.get("arches", cfg["arches"]))


def budget_for(entry: dict, arch: str) -> float:
    budget = entry["budget_mb"]
    if isinstance(budget, dict):
        return float(budget[arch])
    return float(budget)


def default_install_mb(
    cfg: dict,
    arch: str,
    registry,
    app_ref: str,
    app_layers: dict[str, int] | None = None,
) -> float:
    """Distinct compressed layers a fresh standalone install pulls on linux/<arch>.

    app_ref is the app image being measured (default_install.app); the other
    images of docker-compose.yml's default services come from the budget file.
    A layer shared by two images is downloaded, and counted, once.
    """
    combined = dict(
        app_layers if app_layers is not None else layers(app_ref, arch, registry)
    )
    for ref in cfg["default_install"]["with"]:
        combined.update(layers(ref, arch, registry))
    return mb(combined)


# ------------------------------------------------------------------- output


def annotate(level: str, message: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::{level}::{message}")
    else:
        print(f"{level.upper()}: {message}")


def summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def waived(label: str) -> bool:
    """True when the pull request of this run carries the waiver label."""
    pr = os.environ.get("PR_NUMBER")
    if not pr or not label or not os.environ.get("GH_TOKEN"):
        return False
    request = urllib.request.Request(
        f"{os.environ.get('GITHUB_API_URL', 'https://api.github.com')}/repos/"
        f"{os.environ['GITHUB_REPOSITORY']}/issues/{pr}/labels?per_page=100",
        headers={
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            return label in {entry["name"] for entry in json.load(response)}
    except Exception as exc:  # noqa: BLE001 - a label lookup must not crash the gate
        annotate("warning", f"could not read the PR labels: {exc}")
        return False


# ------------------------------------------------------------------ commands


def check(args: argparse.Namespace, cfg: dict, registry) -> int:
    name = f"{args.image}{args.tag_suffix}"
    try:
        entry = find_entry(cfg, args.image, args.tag_suffix)
    except KeyError as exc:
        annotate("error", str(exc))
        return 0 if args.report_only else 1
    budget = budget_for(entry, args.arch)
    try:
        new = layers(args.ref, args.arch, registry, built_for_arch=True)
    except LookupError as exc:
        annotate(
            "warning" if args.report_only else "error",
            f"cannot measure {args.ref}: {exc}",
        )
        return 0 if args.report_only else 1
    size = mb(new)
    failures: list[str] = []  # fail the check
    warnings: list[str] = []  # reported only
    rows = [
        f"| {name} | linux/{args.arch} | {size:,.1f} | {budget:,.0f} | {'OVER' if size > budget else 'ok'} |"
    ]
    print(
        f"{name} linux/{args.arch}: {size:,.1f} MB compressed (budget {budget:,.0f} MB)"
    )
    if size > budget:
        (failures if entry.get("enforce", True) else warnings).append(
            f"{name} linux/{args.arch} is {size:,.1f} MB compressed, over its "
            f"{budget:,.0f} MB budget (scripts/image_size_budget.json)"
        )

    if args.baseline:
        try:
            old = layers(args.baseline, args.arch, registry)
        except LookupError as exc:
            annotate(
                "notice",
                f"no baseline, growth and upgrade delta skipped: {args.baseline} ({exc})",
            )
        else:
            growth = (size / mb(old) - 1) * 100 if old else 0.0
            delta = sum(s for d, s in new.items() if d not in old) / 1e6
            print(
                f"  vs {args.baseline}: {mb(old):,.1f} MB, {growth:+.1f}%; "
                f"an upgrade from it downloads {delta:,.1f} MB"
            )
            rows.append(
                f"| ↳ vs `{args.baseline}` | | {mb(old):,.1f} | {growth:+.1f}% | "
                f"upgrade downloads {delta:,.1f} MB |"
            )
            limit = args.max_growth_percent
            if limit > 0 and growth > limit:
                message = (
                    f"{name} linux/{args.arch} grew {growth:+.1f}% over "
                    f"{args.baseline} (limit +{limit:g}%)"
                )
                if waived(args.waiver_label):
                    warnings.append(
                        f"{message}; waived by the {args.waiver_label} label"
                    )
                else:
                    failures.append(message)

    if args.default_install:
        install = cfg["default_install"]
        if args.image != install["app"]:
            annotate(
                "error",
                f"--default-install measures {install['app']}, not {args.image}",
            )
            return 1
        install_budget = float(install["budget_mb"])
        try:
            total = default_install_mb(cfg, args.arch, registry, args.ref, new)
        except LookupError as exc:
            failures.append(
                f"cannot measure the standalone install on linux/{args.arch}: {exc}"
            )
        else:
            print(
                f"standalone install linux/{args.arch}: {total:,.1f} MB (budget {install_budget:,.0f} MB)"
            )
            rows.append(
                f"| standalone install ({', '.join([args.image, *install['with']])}) | "
                f"linux/{args.arch} | {total:,.1f} | {install_budget:,.0f} | "
                f"{'OVER' if total > install_budget else 'ok'} |"
            )
            if total > install_budget:
                failures.append(
                    f"a fresh standalone install downloads {total:,.1f} MB on linux/{args.arch}, "
                    f"over its {install_budget:,.0f} MB budget"
                )

    summary(
        "| image | arch | MB compressed | budget | |\n|---|---|---|---|---|\n"
        + "\n".join(rows)
        + "\n\n"
    )
    if args.report_only:
        warnings, failures = warnings + failures, []
    for message in warnings:
        annotate("warning", message)
    for message in failures:
        annotate("error", message)
    return 1 if failures else 0


def report(args: argparse.Namespace, cfg: dict, registry) -> int:
    rows: list[str] = []
    failures = 0
    app = cfg["default_install"]["app"]
    for entry in cfg["images"]:
        ref = f"{entry['image']}:{args.tag}{entry.get('tag_suffix', '')}"
        for arch in arches_of(cfg, entry):
            try:
                size = mb(layers(ref, arch, registry))
            except LookupError as exc:
                rows.append(f"| {ref} | linux/{arch} | not published | | |")
                print(f"{ref} linux/{arch}: {exc}")
                continue
            budget = budget_for(entry, arch)
            over = size > budget
            failures += over and entry.get("enforce", True)
            rows.append(
                f"| {ref} | linux/{arch} | {size:,.1f} | {budget:,.0f} | {'OVER' if over else 'ok'} |"
            )
            print(
                f"{ref:<52} linux/{arch}  {size:8.1f} MB  budget {budget:6.0f}  {'OVER' if over else 'ok'}"
            )
    budget = float(cfg["default_install"]["budget_mb"])
    for arch in cfg["arches"]:
        try:
            total = default_install_mb(cfg, arch, registry, f"{app}:{args.tag}")
        except LookupError as exc:
            print(f"standalone install linux/{arch}: {exc}")
            continue
        over = total > budget
        failures += over
        rows.append(
            f"| standalone install | linux/{arch} | {total:,.1f} | {budget:,.0f} | {'OVER' if over else 'ok'} |"
        )
        print(
            f"{'standalone install (distinct layers)':<52} linux/{arch}  {total:8.1f} MB  budget {budget:6.0f}"
        )
    summary(
        f"### Image sizes at {args.tag} (compressed)\n\n"
        "| image | arch | MB | budget | |\n|---|---|---|---|---|\n"
        + "\n".join(rows)
        + "\n\n"
    )
    return 1 if failures and args.enforce else 0


def main(argv: list[str] | None = None, registry=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--budgets", type=Path, default=BUDGETS)
    commands = parser.add_subparsers(dest="command", required=True)

    one = commands.add_parser(
        "check", help="check one pushed image for one architecture"
    )
    one.add_argument(
        "--image", required=True, help="repository, e.g. futureagi/platform"
    )
    one.add_argument(
        "--tag-suffix", default="", help="variant suffix; pass it as --tag-suffix=-gpu"
    )
    one.add_argument(
        "--ref", required=True, help="the image to measure (tag or @digest)"
    )
    one.add_argument("--arch", required=True, choices=("amd64", "arm64"))
    one.add_argument("--baseline", default="", help="published image to compare with")
    one.add_argument(
        "--max-growth-percent", type=float, default=0.0, help="0 = no limit"
    )
    one.add_argument("--waiver-label", default=os.environ.get("WAIVER_LABEL", ""))
    one.add_argument(
        "--default-install",
        action="store_true",
        help="also check the standalone install's total download",
    )
    one.add_argument("--report-only", action="store_true", help="print, never fail")

    every = commands.add_parser("report", help="sizes of every budgeted image at a tag")
    every.add_argument("--tag", required=True)
    every.add_argument(
        "--enforce", action="store_true", help="exit 1 when anything is over"
    )

    args = parser.parse_args(argv)
    cfg = load_budgets(args.budgets)
    registry = registry or default_registry()
    if args.command == "check":
        return check(args, cfg, registry)
    return report(args, cfg, registry)


if __name__ == "__main__":
    sys.exit(main())
