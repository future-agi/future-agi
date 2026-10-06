#!/usr/bin/env python3
"""Install the NLTK data the backend loads, with pinned checksums.

NLTK_DATA_PROFILE selects what is installed:

  minimal  (futureagi/Dockerfile.oss) only the resources the backend loads on
           the pinned nltk>=3.9, extracted, without keeping the archives:
           ~41 MB instead of the ~253 MB unpacked / ~119 MB gzip `full` layer.

             corpora/stopwords                       stopwords.words("english")
             tokenizers/punkt_tab (english only)     nltk.word_tokenize
             taggers/averaged_perceptron_tagger_eng  nltk.pos_tag
             corpora/wordnet                         WordNetLemmatizer, METEOR

           Callers: model_hub/utils/utils.py (AnnotationCorpusBuilder),
           ee/agenthub/trace_scanner/compress.py, evaluations/engine/
           preprocessing.py (METEOR); tfc/utils/nltk_data.py lists the same
           resources. Left out: omw-1.4 (only wordnet lang != "eng"; no caller
           passes lang=), the other punkt_tab languages, and the legacy pickled
           punkt / averaged_perceptron_tagger (nltk>=3.9 never loads them; they
           are added automatically when the installed nltk is older).

  full     (default; the cloud Dockerfiles that do not set a profile) every
           package below, all languages, archives kept next to the extracted
           tree, i.e. the previous behaviour.

Nothing downloads NLTK data at run time in an image: tfc/utils/nltk_data.py
only downloads a missing resource when NLTK_DOWNLOAD_MISSING allows it, which
the images turn off.
"""

from __future__ import annotations

import hashlib
import io
import os
import urllib.request
import zipfile
from pathlib import Path

NLTK_DATA_REVISION = "550b6625bcef1f2abff2ff770a5a0d272c9c6b2a"
NLTK_DATA_ROOT = Path(os.environ.get("NLTK_DATA", "/usr/local/share/nltk_data"))
PROFILES = ("minimal", "full")

# resource -> (archive path, sha256)
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

# The `minimal` profile: resource -> archive member prefixes to extract
# (None = the whole archive).
MINIMAL = {
    "corpora/stopwords": None,
    "tokenizers/punkt_tab": ("punkt_tab/english/", "punkt_tab/README"),
    "taggers/averaged_perceptron_tagger_eng": None,
    "corpora/wordnet": None,
}
# What nltk<3.9 loads instead of punkt_tab / averaged_perceptron_tagger_eng.
LEGACY = {
    "tokenizers/punkt": (
        "punkt/english.pickle",
        "punkt/PY3/english.pickle",
        "punkt/README",
    ),
    "taggers/averaged_perceptron_tagger": None,
}


def _profile() -> str:
    profile = os.environ.get("NLTK_DATA_PROFILE", "full").strip().lower() or "full"
    if profile not in PROFILES:
        raise SystemExit(
            f"NLTK_DATA_PROFILE must be one of {', '.join(PROFILES)}, not {profile!r}"
        )
    return profile


def _nltk_is_legacy() -> bool:
    """True when the installed nltk predates punkt_tab / *_eng (3.9)."""
    try:
        from importlib.metadata import version

        major, minor = (int(part) for part in version("nltk").split(".")[:2])
    except Exception:
        return False
    return (major, minor) < (3, 9)


def selected_packages(profile: str) -> dict[str, tuple[str, ...] | None]:
    """resource -> member prefixes (None = all) for a profile."""
    if profile == "full":
        return dict.fromkeys(PACKAGES)
    selected = dict(MINIMAL)
    if _nltk_is_legacy():
        selected.update(LEGACY)
    return selected


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


def _safe_extract(
    payload: bytes, destination: Path, prefixes: tuple[str, ...] | None = None
) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = [
            member
            for member in archive.infolist()
            if prefixes is None or member.filename.startswith(prefixes)
        ]
        for member in members:
            target = (destination / member.filename).resolve()
            if destination not in target.parents and target != destination:
                raise ValueError(f"Unsafe NLTK archive member: {member.filename}")
        archive.extractall(destination, members=members)


def install() -> None:
    profile = _profile()
    selected = selected_packages(profile)
    for resource_name, prefixes in selected.items():
        package_path, expected_sha256 = PACKAGES[resource_name]
        payload = _download(package_path)
        actual_sha256 = hashlib.sha256(payload).hexdigest()
        if actual_sha256 != expected_sha256:
            raise RuntimeError(
                f"Checksum mismatch for {package_path}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )

        if profile == "full":
            # NLTK's downloader status checks the original archive
            # size/checksum. Keep the pinned archive alongside the extracted
            # tree so a run-time ``nltk.download`` check cannot misclassify
            # baked data as missing.
            archive_path = NLTK_DATA_ROOT / package_path
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            archive_path.write_bytes(payload)

        destination = NLTK_DATA_ROOT / resource_name.split("/", 1)[0]
        destination.mkdir(parents=True, exist_ok=True)
        _safe_extract(payload, destination, prefixes)

    import nltk
    from nltk.corpus import stopwords, wordnet
    from nltk.stem import WordNetLemmatizer

    # Verify exactly what the clean image will contain. Do not allow a host's
    # pre-existing NLTK directories to hide a missing resource.
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
    if "corpora/omw-1.4" in selected and not wordnet.synsets("chien", lang="fra"):
        raise RuntimeError("NLTK multilingual WordNet verification failed")


if __name__ == "__main__":
    install()
