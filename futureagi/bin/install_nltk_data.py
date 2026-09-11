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
    "taggers/averaged_perceptron_tagger_eng": (
        "taggers/averaged_perceptron_tagger_eng.zip",
        "6025f530624335c67d6547d44757b357b4e79bae030a0383e9887a92c1718f0b",
    ),
    # NLTK 3.8.x resolves ``pos_tag`` through the legacy resource name while
    # NLTK 3.9+ resolves the language-specific ``*_eng`` package above.
    "taggers/averaged_perceptron_tagger": (
        "taggers/averaged_perceptron_tagger.zip",
        "e1f13cf2532daadfd6f3bc481a49859f0b8ea6432ccdcd83e6a49a5f19008de9",
    ),
    "corpora/wordnet": (
        "corpora/wordnet.zip",
        "cbda5ea6eef7f36a97a43d4a75f85e07fccbb4f23657d27b4ccbc93e2646ab59",
    ),
    "corpora/omw-1.4": (
        "corpora/omw-1.4.zip",
        "3b941e664852f3297b6040236626065796a2aaf7d7f9eec8779a3beaa1096c2d",
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

        archive_path = NLTK_DATA_ROOT / package_path
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        # NLTK's downloader status checks the original archive size/checksum.
        # Keep the pinned archive alongside the extracted tree so a runtime
        # ``nltk.download`` check cannot misclassify baked data as missing.
        archive_path.write_bytes(payload)

        resource_group = resource_name.split("/", 1)[0]
        destination = NLTK_DATA_ROOT / resource_group
        destination.mkdir(parents=True, exist_ok=True)
        _safe_extract(payload, destination)

    import nltk
    from nltk.corpus import stopwords, wordnet
    from nltk.stem import WordNetLemmatizer

    # Verify exactly what the clean image will contain. Do not allow a host's
    # pre-existing NLTK directories to hide a missing archive.
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
    if not nltk.pos_tag(["Future"])[0][1]:
        raise RuntimeError("NLTK part-of-speech tagger verification failed")
    if WordNetLemmatizer().lemmatize("cars", "n") != "car":
        raise RuntimeError("NLTK WordNet verification failed")
    if not wordnet.synsets("chien", lang="fra"):
        raise RuntimeError("NLTK multilingual WordNet verification failed")


if __name__ == "__main__":
    install()
