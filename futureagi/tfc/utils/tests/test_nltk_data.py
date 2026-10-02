"""tfc.utils.nltk_data: baked data first, run-time download only when allowed."""

from __future__ import annotations

import pytest

from tfc.utils import nltk_data


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(nltk_data, "_present", set())


def _fake_nltk(monkeypatch: pytest.MonkeyPatch, *, installed: set[str]):
    import nltk

    downloads: list[str] = []

    def find(path):
        if path not in installed:
            raise LookupError(path)
        return path

    def download(package, quiet=False, raise_on_error=False):
        downloads.append(package)
        for path, pkg in nltk_data.RESOURCES.values():
            if pkg == package:
                installed.add(path)
        return True

    monkeypatch.setattr(nltk.data, "find", find)
    monkeypatch.setattr(nltk, "download", download)
    return downloads


def test_present_resource_is_not_downloaded(monkeypatch: pytest.MonkeyPatch):
    downloads = _fake_nltk(monkeypatch, installed={nltk_data.RESOURCES["stopwords"][0]})
    monkeypatch.setenv(nltk_data.DOWNLOAD_ENV, "true")

    nltk_data.ensure_nltk_data("stopwords")

    assert downloads == []


def test_missing_resource_is_downloaded_when_allowed(
    monkeypatch: pytest.MonkeyPatch,
):
    downloads = _fake_nltk(monkeypatch, installed=set())
    monkeypatch.delenv(nltk_data.DOWNLOAD_ENV, raising=False)

    nltk_data.ensure_nltk_data("punkt_tab", "wordnet")
    nltk_data.ensure_nltk_data("punkt_tab", "wordnet")  # cached: no re-check

    assert downloads == ["punkt_tab", "wordnet"]


@pytest.mark.parametrize("value", ["false", "0", "no", "OFF"])
def test_missing_resource_raises_when_downloads_are_off(
    monkeypatch: pytest.MonkeyPatch, value: str
):
    downloads = _fake_nltk(monkeypatch, installed=set())
    monkeypatch.setenv(nltk_data.DOWNLOAD_ENV, value)

    with pytest.raises(LookupError, match="install_nltk_data.py"):
        nltk_data.ensure_nltk_data("stopwords")

    assert downloads == []


def test_resources_match_the_minimal_image_profile():
    """Every resource callers ask for is one the image installs."""
    import importlib.util
    from pathlib import Path

    installer_path = (
        Path(__file__).resolve().parents[3] / "bin" / "install_nltk_data.py"
    )
    spec = importlib.util.spec_from_file_location("install_nltk_data", installer_path)
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)

    installed_groups = {resource.split("/", 1)[1] for resource in installer.MINIMAL}
    assert {pkg for _, pkg in nltk_data.RESOURCES.values()} == installed_groups
