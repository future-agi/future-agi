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
    assert "python bin/install_nltk_data.py" in contents


def test_slim_image_installs_the_minimal_profile() -> None:
    contents = (REPO_ROOT / "futureagi" / "Dockerfile.oss").read_text()

    # The slim variant (the base of futureagi/platform) installs minimal; the
    # default variant full (futureagi/docker/image-variant.sh).
    variant = (REPO_ROOT / "futureagi" / "docker" / "image-variant.sh").read_text()
    slim = variant.split("    slim)", 1)[1].split(";;", 1)[0]
    assert ': "${NLTK_DATA_PROFILE:=minimal}"' in slim
    standard = variant.split("    standard)", 1)[1].split(";;", 1)[0]
    assert ': "${NLTK_DATA_PROFILE:=full}"' in standard
    assert 'ARG NLTK_DATA_PROFILE=""' in contents
    # Run-time downloads are off in the image: the data is baked.
    assert "NLTK_DOWNLOAD_MISSING=false" in contents


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


def test_minimal_profile_selects_only_what_nltk_3_9_loads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(install_nltk_data, "_nltk_is_legacy", lambda: False)

    selected = install_nltk_data.selected_packages("minimal")

    assert selected == {
        "corpora/stopwords": None,
        "tokenizers/punkt_tab": ("punkt_tab/english/", "punkt_tab/README"),
        "taggers/averaged_perceptron_tagger_eng": None,
        "corpora/wordnet": None,
    }
    assert set(selected) <= set(install_nltk_data.PACKAGES)


def test_minimal_profile_adds_legacy_resources_for_old_nltk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(install_nltk_data, "_nltk_is_legacy", lambda: True)

    selected = install_nltk_data.selected_packages("minimal")

    assert {"tokenizers/punkt", "taggers/averaged_perceptron_tagger"} <= set(selected)
    assert "corpora/omw-1.4" not in selected


def test_full_profile_is_the_default_and_selects_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NLTK_DATA_PROFILE", raising=False)

    assert install_nltk_data._profile() == "full"
    assert install_nltk_data.selected_packages("full") == dict.fromkeys(
        install_nltk_data.PACKAGES
    )


def test_unknown_profile_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NLTK_DATA_PROFILE", "tiny")

    with pytest.raises(SystemExit, match="NLTK_DATA_PROFILE"):
        install_nltk_data._profile()


def test_safe_extract_honours_member_prefixes(tmp_path: Path) -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("punkt_tab/english/sentence_starters.txt", "a\n")
        archive.writestr("punkt_tab/german/sentence_starters.txt", "b\n")

    destination = tmp_path / "tokenizers"
    install_nltk_data._safe_extract(
        payload.getvalue(), destination, ("punkt_tab/english/",)
    )

    assert (destination / "punkt_tab" / "english" / "sentence_starters.txt").exists()
    assert not (destination / "punkt_tab" / "german").exists()


def _stub_nltk_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub everything install() verifies, without loading any NLTK data.

    The corpora in nltk.corpus are LazyCorpusLoader objects: reading any
    attribute of one (which monkeypatch.setattr(loader, ...) does to save the
    old value) loads the corpus and raises LookupError on a machine without
    NLTK data. Replace the loaders on the nltk.corpus module instead; install()
    imports them from there at call time.
    """
    import types

    import nltk
    import nltk.corpus
    from nltk.stem import WordNetLemmatizer

    monkeypatch.setattr(
        nltk.corpus, "stopwords", types.SimpleNamespace(words=lambda _: ["the"])
    )
    monkeypatch.setattr(
        nltk.corpus,
        "wordnet",
        types.SimpleNamespace(synsets=lambda _word, lang: [lang]),
    )
    monkeypatch.setattr(
        nltk, "word_tokenize", lambda _: ["Future", "AGI", "image", "verification"]
    )
    monkeypatch.setattr(nltk, "pos_tag", lambda _: [("Future", "NN")])
    monkeypatch.setattr(
        WordNetLemmatizer, "lemmatize", lambda _self, _word, _pos="n": "car"
    )


def test_minimal_profile_does_not_keep_archives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import nltk

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("stopwords/english", "a\nthe\n")
    archive_bytes = payload.getvalue()
    previous_paths = list(nltk.data.path)
    monkeypatch.setenv("NLTK_DATA_PROFILE", "minimal")
    monkeypatch.setattr(install_nltk_data, "NLTK_DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        install_nltk_data,
        "PACKAGES",
        {
            "corpora/stopwords": (
                "corpora/stopwords.zip",
                hashlib.sha256(archive_bytes).hexdigest(),
            )
        },
    )
    monkeypatch.setattr(install_nltk_data, "MINIMAL", {"corpora/stopwords": None})
    monkeypatch.setattr(install_nltk_data, "_nltk_is_legacy", lambda: False)
    monkeypatch.setattr(install_nltk_data, "_download", lambda _: archive_bytes)
    _stub_nltk_verification(monkeypatch)
    try:
        install_nltk_data.install()
    finally:
        nltk.data.path[:] = previous_paths

    assert (tmp_path / "corpora" / "stopwords" / "english").exists()
    assert not list(tmp_path.rglob("*.zip"))


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
    monkeypatch.delenv("NLTK_DATA_PROFILE", raising=False)
    _stub_nltk_verification(monkeypatch)

    try:
        install_nltk_data.install()
        assert nltk.data.path == [str(tmp_path)]
    finally:
        nltk.data.path[:] = previous_paths
    # The (default) full profile keeps the pinned archives.
    assert (tmp_path / "corpora" / "stopwords.zip").exists()
