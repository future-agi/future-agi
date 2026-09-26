#!/usr/bin/env python3
"""Build-time smoke test of the runtime stage of futureagi/Dockerfile.oss.

Runs against the RUNTIME system libraries, after site-packages was trimmed
and stripped in the builder and before the application source is copied, so
it stays cached across source-only changes. It writes nothing
(PYTHONDONTWRITEBYTECODE=1). Reads the build args it checks from the
environment, as docker/image-variant.sh resolved them: EXTRAS, FFMPEG_FLAVOR,
NLTK_DATA_PROFILE, SLIM_SITE_PACKAGES, WITH_GIT, WITH_UV.
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

MODULES = (
    "django",
    "rest_framework",
    "granian",
    "celery",
    "channels_redis",
    "temporalio.worker",
    "litellm",
    "openai",
    "anthropic",
    "google.genai",
    "google.cloud.storage",
    "google.cloud.pubsub_v1",
    "googleapiclient.discovery",
    "pandas",
    "numpy",
    "scipy.stats",
    "scipy.spatial.distance",
    "sklearn.metrics",
    "sklearn.feature_extraction.text",
    "nltk",
    "pydub",
    "pypdf",
    "docx",
    "pdfplumber",
    "openpyxl",
    "striprtf.striprtf",
    "langchain_text_splitters",
    "zstandard",
    "lz4.frame",
    "psycopg",
    "clickhouse_connect",
    "clickhouse_driver",
    "xmlsec",
    "saml2",
    "grpc",
    "tiktoken",
    "pydantic_core",
    "orjson",
    "uvloop",
    "humanize",
)
# What each optional dependency group (EXTRAS) must make importable; the
# standard variant installs the first six, the groups that were base
# dependencies before the image-size split (pyproject.toml).
EXTRA_MODULES = {
    "sandbox": ("daytona", "e2b", "httpx_ws"),
    "billing": ("stripe",),
    "ops": ("flower",),
    "gcp": ("vertexai", "traceai_vertexai"),
    "langchain": ("langchain", "langchain_community"),
    "rabbitmq": ("channels_rabbitmq",),
}
# What bin/install_nltk_data.py installs in the `minimal` profile.
NLTK_RESOURCES = (
    "corpora/stopwords/",
    "tokenizers/punkt_tab/english/",
    "taggers/averaged_perceptron_tagger_eng/",
    "corpora/wordnet/",
)
# What only the `full` profile adds.
NLTK_FULL_RESOURCES = (
    "corpora/omw-1.4/",
    "tokenizers/punkt/",
    "taggers/averaged_perceptron_tagger/",
)


def flag(name: str) -> bool:
    return os.environ.get(name, "false").strip().lower() == "true"


def main() -> None:
    failures = []
    extras = [g for g in os.environ.get("EXTRAS", "").split(",") if g]
    modules = [*MODULES, *(m for g in extras for m in EXTRA_MODULES.get(g, ()))]
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - report every failure
            failures.append(f"import {name}: {type(exc).__name__}: {exc}")

    from googleapiclient.discovery_cache import get_static_doc

    # The two APIs the app calls; SLIM_SITE_PACKAGES=0 keeps every document.
    apis = [("servicecontrol", "v1"), ("cloudcommerceprocurement", "v1")]
    if os.environ.get("SLIM_SITE_PACKAGES", "1") == "0":
        apis.append(("drive", "v3"))
    for api, version in apis:
        if not get_static_doc(api, version):
            failures.append(
                f"googleapiclient discovery document {api}.{version} missing"
            )

    import nltk

    resources = NLTK_RESOURCES
    if os.environ.get("NLTK_DATA_PROFILE", "minimal") == "full":
        resources += NLTK_FULL_RESOURCES
    for resource in resources:
        try:
            nltk.data.find(resource)
        except LookupError:
            failures.append(f"NLTK resource {resource} missing")

    if (shutil.which("uv") is not None) != flag("WITH_UV"):
        failures.append(
            f"uv on PATH: {shutil.which('uv')!r}, WITH_UV={flag('WITH_UV')}"
        )
    if (shutil.which("git") is not None) != flag("WITH_GIT"):
        failures.append(
            f"git on PATH: {shutil.which('git')!r}, WITH_GIT={flag('WITH_GIT')}"
        )

    flavor = os.environ.get("FFMPEG_FLAVOR", "minimal")
    if flavor == "none":
        if shutil.which("ffmpeg"):
            failures.append("FFMPEG_FLAVOR=none but ffmpeg is on PATH")
    else:
        for tool in ("ffmpeg", "ffprobe"):
            proc = subprocess.run(
                [tool, "-hide_banner", "-version"], capture_output=True, check=False
            )
            if proc.returncode:
                failures.append(f"{tool} -version: {proc.stderr.decode()[-500:]}")
        encoders = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, check=False
        ).stdout.decode()
        if " libmp3lame " not in encoders:
            failures.append("ffmpeg has no libmp3lame encoder")

    if failures:
        sys.exit("runtime smoke test FAILED:\n  " + "\n  ".join(failures))
    print("runtime smoke test: OK")


if __name__ == "__main__":
    main()
