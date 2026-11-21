"""Read build inputs as data at one local Git commit. Never source a tagged script."""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", "-C", ROOT, *args], text=True).rstrip("\n")


def one(pattern, text):
    matches = re.findall(pattern, text, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError(f"expected exactly one match for {pattern}")
    return matches[0]


def release_jobs(workflow):
    import yaml  # Only automatic release lookup requires PyYAML on the host.

    class UniqueLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            mapping = super().construct_mapping(node, deep)
            if len(mapping) != len(node.value):
                raise ValueError("duplicate workflow key")
            return mapping

    try:
        return yaml.load(workflow, Loader=UniqueLoader)["jobs"]
    except yaml.YAMLError as exc:
        raise ValueError("unrecognized release workflow") from exc


def release_variant(workflow, suffix, default):
    job = release_jobs(workflow)["backend" + suffix]
    inputs = job["with"]
    if (
        job["uses"] != "./.github/workflows/build-image-multiarch.yml"
        or inputs["image"] != "futureagi/future-agi"
        or inputs["context"] != "./futureagi"
        or inputs["dockerfile"] != "./futureagi/Dockerfile.oss"
        or inputs["ref"] != "${{ github.ref }}"
        or inputs["version"] != "${{ needs.guard.outputs.version }}"
        or inputs.get("tag-suffix", "") != suffix
    ):
        raise ValueError("unrecognized backend build")
    args = inputs.get("build-args", "").strip()
    if not args:
        return default
    match = re.fullmatch(r"IMAGE_VARIANT=(standard|slim)", args)
    if not match:
        raise ValueError("unrecognized release build arguments")
    return match[1]


def pre_variant_debian(workflow, docker, suffix):
    """Recognize the unsuffixed matrix build and its unconditional runtime install."""
    jobs = release_jobs(workflow)
    job = jobs["build"]
    matrix = job["strategy"]["matrix"]
    rows = [
        row
        for row in matrix["include"]
        if row["name"] == "backend" or row["image"] == "futureagi/future-agi"
    ]
    inputs = job["with"]
    if (
        suffix
        or "backend" in jobs
        or set(matrix) != {"include"}
        or len(rows) != 1
        or job["uses"] != "./.github/workflows/build-image.yml"
        or set(inputs)
        - {
            "image",
            "context",
            "dockerfile",
            "version",
            "ref",
            "platforms",
            "push-latest",
            "free-disk",
            "cache-scope",
        }
        or inputs["version"] != "${{ needs.guard.outputs.version }}"
        or inputs["ref"] != "${{ github.ref }}"
        or rows[0]["name"] != "backend"
    ):
        raise ValueError("unrecognized pre-variant release build")
    for key, expected in [
        ("image", "futureagi/future-agi"),
        ("context", "./futureagi"),
        ("dockerfile", "./futureagi/Dockerfile.oss"),
    ]:
        if rows[0][key] != expected or inputs[key] != "${{ matrix." + key + " }}":
            raise ValueError("unrecognized pre-variant backend input")

    instructions = "\n".join(
        line for line in docker.splitlines() if not line.lstrip().startswith("#")
    )
    instructions = instructions.replace("\\\n", " ")
    stages = re.split(r"^FROM ", instructions, flags=re.M)
    if (
        len(stages) not in (2, 3)
        or not stages[-1].startswith("python:3.11-slim-bookworm\n")
        or re.search(r"IMAGE_VARIANT|FFMPEG_FLAVOR", instructions)
    ):
        raise ValueError("unrecognized pre-variant runtime stage")
    packages = one(
        r"^RUN apt-get update && apt-get install -y --no-install-recommends "
        r"([a-z0-9+ .-]+)&& rm -rf /var/lib/apt/lists/\* +&& apt-get clean$",
        stages[-1],
    )
    if "ffmpeg" not in packages.split() or instructions.lower().count("ffmpeg") != 1:
        raise ValueError("ambiguous pre-variant ffmpeg installation")


def contract(version, image, variant, flavor):
    explicit = bool(variant or flavor)
    if explicit:
        if variant not in ("standard", "slim") or flavor not in ("minimal", "debian"):
            raise ValueError("supply both OSS_VARIANT and EXPECTED_FFMPEG_FLAVOR")
        ref = os.getenv(
            "BUILD_SOURCE_REVISION", os.getenv("OSS_BUILD_REVISION", "HEAD")
        )
        if os.getenv("OSS_BUILD_REVISION", ref) != ref:
            raise ValueError("conflicting build revisions")
        if ref != "HEAD" and not re.fullmatch("[0-9a-f]{40}", ref):
            raise ValueError("expected a full build commit")
    else:
        if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?", version):
            raise ValueError("expected a local release tag")
        base = "futureagi/future-agi:" + version
        if image not in (base, base + "-slim") or any(
            key in os.environ for key in ("BUILD_SOURCE_REVISION", "OSS_BUILD_REVISION")
        ):
            raise ValueError("supply explicit expectations for custom images")
        ref = "refs/tags/" + version
    # A shallow checkout may lack this tag. Fetch only that tag into a disposable
    # object store, never into the checkout's refs, and never consult latest.
    if (
        not explicit
        and subprocess.run(
            ["git", "-C", ROOT, "show-ref", "--verify", "--quiet", ref],
            check=False,
        ).returncode
        == 1
    ):
        with tempfile.TemporaryDirectory() as tmp:
            url = git("remote", "get-url", "origin")
            subprocess.run(["git", "init", "--bare", "--quiet", tmp], check=True)
            fetch = subprocess.run(
                [
                    "git",
                    "-C",
                    tmp,
                    "fetch",
                    "--quiet",
                    "--no-tags",
                    "--depth=1",
                    "--",
                    url,
                    ref,
                ],
                stderr=subprocess.PIPE,
                check=False,
            )
            if fetch.returncode:
                raise ValueError(f"cannot fetch {ref} from origin")
            return from_source(
                tmp, "FETCH_HEAD", explicit, image[len(base) :], variant, flavor
            )
    return from_source(
        ROOT, ref, explicit, "" if explicit else image[len(base) :], variant, flavor
    )


def from_source(repo, ref, explicit, suffix, variant, flavor):
    revision = git("-C", repo, "rev-parse", "--verify", ref + "^{commit}")

    # Explicit build inputs request the full boundary. Automatic release lookup
    # only knows the media contract unless it resolves to this exact checkout.
    scope = "FULL" if explicit or revision == git("rev-parse", "HEAD") else "MEDIA_ONLY"

    def source(path):
        return git("-C", repo, "cat-file", "blob", revision + ":" + path)

    docker = source("futureagi/Dockerfile.oss")
    if not explicit:
        workflow = source(".github/workflows/release-images.yml")
        if "IMAGE_VARIANT" not in docker:
            pre_variant_debian(workflow, docker, suffix)
            return "standard", "debian", "-", "-", "-", scope
        default = one(r"^ARG IMAGE_VARIANT=(standard|slim)$", docker)
        variant = release_variant(workflow, suffix, default)
        if (
            'ARG FFMPEG_FLAVOR=""' not in docker.splitlines()
            or "FROM ffmpeg-${FFMPEG_FLAVOR:-${IMAGE_VARIANT}} AS ffmpeg"
            not in docker.splitlines()
        ):
            raise ValueError("unrecognized ffmpeg stage selection")
        stage = one(r"^FROM (\S+) AS ffmpeg-" + variant + "$", docker)
        if stage not in ("ffmpeg-minimal", "ffmpeg-debian"):
            raise ValueError("unrecognized ffmpeg stage")
        flavor = stage.removeprefix("ffmpeg-")
        resolver = source("futureagi/docker/image-variant.sh")
        arm = one(r"^    " + variant + r"\)\n([\s\S]*?)^        ;;$", resolver)
        resolved = one(r'^        : "\$\{FFMPEG_FLAVOR:=(\w+)\}"$', arm)
        if resolved != flavor:
            raise ValueError("runtime package selection disagrees with build stage")
    if flavor == "debian":
        return variant, flavor, "-", "-", "-", scope
    config = source("futureagi/docker/ffmpeg/configure-minimal.sh")

    configure = config.partition("if ! ./configure ")[2].partition('>"$LOG"')[0]
    if "--disable-everything" not in configure.split():
        raise ValueError("unrecognized minimal configure invocation")

    def codecs(name):
        kind = name.lower().removesuffix("s")
        if f'--enable-{kind}="$(csv "${name}")"' not in configure:
            raise ValueError("unrecognized configure option for " + name)
        # The sole dynamic addition in the supported recipes is the native PCM
        # decoder family. Never expand shell syntax to discover these names.
        assignments = re.findall("^" + name + "=.*$", config, re.MULTILINE)
        extra = '''DECODERS="$DECODERS $(list decoder | grep '^pcm_' | grep -v '_at$' | tr '\\n' ' ')"'''
        if assignments[1:] != ([extra] if name == "DECODERS" else []):
            raise ValueError("unrecognized " + name + " assignments")
        names = one("^" + name + r'="([a-z0-9_\s]+)"$', config).split()
        if name == "DECODERS":
            # Bounded native PCM contract: WAV, A-law WAV, AIFF and AU samples
            # in docker/ffmpeg/selftest.py. The full list is source-generated.
            names.extend(("pcm_s16le", "pcm_alaw", "pcm_s16be", "pcm_mulaw"))
        return ",".join(names)

    return (
        variant,
        flavor,
        one(r"^ARG FFMPEG_VERSION=([\d.]+)$", docker),
        codecs("ENCODERS"),
        codecs("DECODERS"),
        scope,
    )


if __name__ == "__main__":
    try:
        print(*contract(*sys.argv[1:]))
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        ImportError,
        subprocess.CalledProcessError,
    ) as exc:
        sys.exit(f"Unknown build expectation: {exc}")
