from __future__ import annotations

import hashlib
import importlib.util
import io
import re
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER_PATH = REPO_ROOT / "futureagi" / "bin" / "install_nltk_data.py"
SPEC = importlib.util.spec_from_file_location("install_nltk_data", INSTALLER_PATH)
assert SPEC is not None and SPEC.loader is not None
install_nltk_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install_nltk_data)


@pytest.mark.parametrize(
    "dockerfile",
    [
        REPO_ROOT / "Dockerfile",
        REPO_ROOT / "Dockerfile.oss",
        REPO_ROOT / "futureagi" / "Dockerfile",
        REPO_ROOT / "futureagi" / "Dockerfile.oss",
    ],
)
def test_backend_dockerfiles_install_pinned_nltk_data(dockerfile: Path) -> None:
    contents = dockerfile.read_text()

    assert "NLTK_DATA=/usr/local/share/nltk_data" in contents
    assert "RUN python bin/install_nltk_data.py" in contents


def test_nltk_archives_are_revision_and_checksum_pinned() -> None:
    assert re.fullmatch(r"[0-9a-f]{40}", install_nltk_data.NLTK_DATA_REVISION)
    assert set(install_nltk_data.PACKAGES) == {
        "corpora/stopwords",
        "tokenizers/punkt",
        "tokenizers/punkt_tab",
        "taggers/averaged_perceptron_tagger_eng",
        "taggers/averaged_perceptron_tagger",
        "corpora/wordnet",
        "corpora/omw-1.4",
    }

    for _, expected_sha256 in install_nltk_data.PACKAGES.values():
        assert re.fullmatch(r"[0-9a-f]{64}", expected_sha256)


def test_safe_extract_writes_expected_resource(tmp_path: Path) -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("stopwords/english", "a\nthe\n")

    destination = tmp_path / "corpora"
    install_nltk_data._safe_extract(payload.getvalue(), destination)

    assert (destination / "stopwords" / "english").read_text() == "a\nthe\n"


def test_safe_extract_rejects_parent_traversal(tmp_path: Path) -> None:
    escaped_name = f"{tmp_path.name}-escaped"
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(f"../{escaped_name}", "unsafe")

    with pytest.raises(ValueError, match="Unsafe NLTK archive member"):
        install_nltk_data._safe_extract(payload.getvalue(), tmp_path / "corpora")

    assert not (tmp_path.parent / escaped_name).exists()


def test_checksum_mismatch_fails_before_extracting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"tampered archive"
    expected_sha256 = hashlib.sha256(b"expected archive").hexdigest()
    destination = tmp_path / "nltk_data"
    monkeypatch.setattr(install_nltk_data, "NLTK_DATA_ROOT", destination)
    monkeypatch.setattr(
        install_nltk_data,
        "PACKAGES",
        {"corpora/stopwords": ("corpora/stopwords.zip", expected_sha256)},
    )
    monkeypatch.setattr(install_nltk_data, "_download", lambda _: payload)

    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        install_nltk_data.install()

    assert not destination.exists()


def test_install_verification_uses_only_the_fresh_destination(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import nltk
    from nltk.corpus import stopwords, wordnet
    from nltk.stem import WordNetLemmatizer

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("placeholder/resource", "verified by test doubles")
    archive_bytes = payload.getvalue()
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    packages = {
        "corpora/stopwords": ("corpora/stopwords.zip", archive_sha256),
        "tokenizers/punkt": ("tokenizers/punkt.zip", archive_sha256),
        "tokenizers/punkt_tab": ("tokenizers/punkt_tab.zip", archive_sha256),
    }
    previous_paths = list(nltk.data.path)

    monkeypatch.setattr(install_nltk_data, "NLTK_DATA_ROOT", tmp_path)
    monkeypatch.setattr(install_nltk_data, "PACKAGES", packages)
    monkeypatch.setattr(install_nltk_data, "_download", lambda _: archive_bytes)
    monkeypatch.setattr(stopwords, "words", lambda _: ["the"])
    monkeypatch.setattr(
        nltk,
        "word_tokenize",
        lambda _: ["Future", "AGI", "image", "verification"],
    )
    monkeypatch.setattr(nltk, "pos_tag", lambda _: [("Future", "NN")])
    monkeypatch.setattr(
        WordNetLemmatizer,
        "lemmatize",
        lambda _self, _word, _pos="n": "car",
    )
    monkeypatch.setattr(wordnet, "synsets", lambda _word, lang: [lang])

    try:
        install_nltk_data.install()
        assert nltk.data.path == [str(tmp_path)]
    finally:
        nltk.data.path[:] = previous_paths
