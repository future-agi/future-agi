"""Provider logos are bundled artwork served by the install's own UI.

ProviderLogoUrls returned absolute URLs on the fi-image-assets S3 bucket (the
two voice logos on fi-content-dev), and the Postgres span serializers and the
onboarding demo prompt named the same objects. Every browser that drew one of
these logos fetched it from that bucket: on Future AGI Cloud, on self-hosted
installs and on every local or cloud test stack.

Each value is now a root-relative path to a file in frontend/public, the
pattern of tracer/constants/provider_icons.py: the frontend origin serves it,
and no response names another host. frontend/public/assets/provider-logos/
manifest.json pins every file with its earlier URL, its source and SHA-256.

Set PROVIDER_STATUS_EXPORT to a file path to save the provider-status
response; the browser proof (frontend/scripts/browser-provider-logos.mjs)
serves exactly that response to the real AI Providers page.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from rest_framework import status

from model_hub.models.choices import LiteLlmModelProvider, ProviderLogoUrls
from tracer.constants.provider_logos import PROVIDER_LOGOS

# futureagi/model_hub/tests/<this file> -> the repository root.
REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "futureagi"
FRONTEND_PUBLIC = REPO_ROOT / "frontend" / "public"
MANIFEST = FRONTEND_PUBLIC / "assets" / "provider-logos" / "manifest.json"
LOGO_BUCKET = "fi-image-assets.s3.ap-south-1.amazonaws.com"
EARLIER_LOGO_HOSTS = {LOGO_BUCKET, "fi-content-dev.s3.ap-south-1.amazonaws.com"}
PROVIDER_STATUS_URL = "/model-hub/develops/provider-status/"


def assert_bundled_path(value, label):
    """``value`` is a root-relative path to a non-empty file in frontend/public."""
    assert isinstance(value, str), f"{label}: {value!r}"
    parts = urlsplit(value)
    assert not parts.scheme and not parts.netloc, f"{label} names a host: {value}"
    assert value.startswith("/") and not value.startswith("//"), f"{label}: {value}"
    assert not parts.query and not parts.fragment, f"{label}: {value}"
    # "+", "%" and spaces are decoded differently by some static servers.
    assert not set(value) & {"+", "%", " ", "\\"}, f"{label}: {value}"
    asset = FRONTEND_PUBLIC / value.lstrip("/")
    assert asset.resolve().is_relative_to(FRONTEND_PUBLIC.resolve()), value
    assert asset.is_file(), f"{label}: {asset} is missing"
    assert asset.stat().st_size > 50, f"{label}: {asset} is empty"


def test_every_provider_and_alias_returns_a_bundled_same_origin_path():
    members = ProviderLogoUrls.__members__
    assert len(members) >= 55
    for name, member in members.items():
        for spelling in (name.lower(), name.lower().replace("_", "-")):
            assert ProviderLogoUrls.get_url_by_provider(spelling) == member.value
        assert_bundled_path(member.value, name)


def test_unknown_providers_still_get_no_logo():
    for unknown in ("", "not-a-provider", "https://openai", "__proto__"):
        assert ProviderLogoUrls.get_url_by_provider(unknown) is None
    assert ProviderLogoUrls.get_url_by_provider(
        "text-completion-openai"
    ) == ProviderLogoUrls.get_url_by_provider("openai")


def test_manifest_pins_source_and_sha256_of_every_logo():
    manifest = json.loads(MANIFEST.read_text())
    members = ProviderLogoUrls.__members__
    rows = manifest["assets"]
    assert sorted(n for row in rows for n in row["providers"]) == sorted(members)
    for row in rows:
        assert_bundled_path(row["path"], row["path"])
        data = (FRONTEND_PUBLIC / row["path"].lstrip("/")).read_bytes()
        assert hashlib.sha256(data).hexdigest() == row["sha256"], row["path"]
        assert urlsplit(row["original_url"]).hostname in EARLIER_LOGO_HOSTS
        assert row["kind"] in {
            "exact-original-object",
            "repository",
            "locked-dependency",
        }
        if row["kind"] == "exact-original-object":
            assert row["source"] == row["original_url"]
            assert urlsplit(row["source"]).hostname == LOGO_BUCKET
        for name in row["providers"]:
            assert members[name].value == row["path"], name


def test_span_provider_logos_are_bundled_same_origin_paths():
    assert PROVIDER_LOGOS
    for provider, value in PROVIDER_LOGOS.items():
        assert_bundled_path(value, provider)


def test_backend_runtime_code_names_no_logo_bucket():
    hits = []
    for root, dirs, files in os.walk(BACKEND_ROOT):
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".")
            and d not in {"tests", "migrations", "node_modules", "__pycache__"}
        ]
        for name in files:
            if name.endswith(".py") and not name.startswith("test_"):
                path = Path(root, name)
                if LOGO_BUCKET in path.read_text(errors="replace"):
                    hits.append(str(path.relative_to(REPO_ROOT)))
    assert hits == []


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_provider_status_returns_bundled_logo_paths(auth_client):
    response = auth_client.get(PROVIDER_STATUS_URL)
    assert response.status_code == status.HTTP_200_OK
    providers = response.json()["result"]["providers"]
    export = os.environ.get("PROVIDER_STATUS_EXPORT")
    if export:
        # Saved before any assertion so the browser proof sees the real response.
        Path(export).write_text(json.dumps(providers, indent=2) + "\n")
    assert LOGO_BUCKET not in response.content.decode()
    assert [p["provider"] for p in providers] == [
        value for value, _ in LiteLlmModelProvider.get_choices()
    ]
    for row in providers:
        expected = ProviderLogoUrls.get_url_by_provider(row["provider"])
        assert expected is not None, row["provider"]
        assert row["logo_url"] == expected
        assert_bundled_path(row["logo_url"], row["provider"])
