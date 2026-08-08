#!/usr/bin/env python3
"""Install the NLTK data required by backend startup, with pinned checksums."""

from __future__ import annotations

import hashlib
import io
import os
import urllib.request
import zipfile
from pathlib import Path

NLTK_DATA_REVISION = "550b6625bcef1f2abff2ff770a5a0d272c9c6b2a"
NLTK_DATA_ROOT = Path(os.environ.get("NLTK_DATA", "/usr/local/share/nltk_data"))
PACKAGES = {
    "corpora/stopwords": (
        "corpora/stopwords.zip",
        "48c0e52d8b52546e827f53761fb30300c0ab94f70660d28bd65ba0a86270946b",
    ),
    "tokenizers/punkt": (
        "tokenizers/punkt.zip",
        "51c3078994aeaf650bfc8e028be4fb42b4a0d177d41c012b6a983979653660ec",
    ),
    "tokenizers/punkt_tab": (
        "tokenizers/punkt_tab.zip",
        "e57f64187974277726a3417ca6f181ec5403676c717672eef6a748a7b20e0106",
    ),
}


def _download(package_path: str) -> bytes:
    url = (
        "https://raw.githubusercontent.com/nltk/nltk_data/"
        f"{NLTK_DATA_REVISION}/packages/{package_path}"
    )
    request = urllib.request.Request(
        url, headers={"User-Agent": "futureagi-image-build"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        return response.read()


def _safe_extract(payload: bytes, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if destination not in target.parents and target != destination:
                raise ValueError(f"Unsafe NLTK archive member: {member.filename}")
        archive.extractall(destination)


def install() -> None:
    for resource_name, (package_path, expected_sha256) in PACKAGES.items():
        payload = _download(package_path)
        actual_sha256 = hashlib.sha256(payload).hexdigest()
        if actual_sha256 != expected_sha256:
            raise RuntimeError(
                f"Checksum mismatch for {package_path}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )

        resource_group = resource_name.split("/", 1)[0]
        destination = NLTK_DATA_ROOT / resource_group
        destination.mkdir(parents=True, exist_ok=True)
        _safe_extract(payload, destination)

    import nltk
    from nltk.corpus import stopwords

    # Verify exactly what the clean image will contain.  Do not allow a
    # developer/CI host's pre-existing NLTK directories to hide a missing
    # archive (NLTK 3.9+ requires ``punkt_tab`` in addition to ``punkt``).
    nltk.data.path[:] = [str(NLTK_DATA_ROOT)]
    if not stopwords.words("english"):
        raise RuntimeError("NLTK English stopwords corpus is empty")
    if nltk.word_tokenize("Future AGI image verification") != [
        "Future",
        "AGI",
        "image",
        "verification",
    ]:
        raise RuntimeError("NLTK punkt tokenizer verification failed")


if __name__ == "__main__":
    install()
