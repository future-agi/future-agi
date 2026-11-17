"""Provider icons of spans read from ClickHouse come from the install's own UI.

CHSpanReader.to_django_dict() shapes ClickHouse spans for the shared trace
page, separate evals and Falcon's trace explorer. Its ``provider_logo`` used to
be ``https://app.futureagi.com/static/providers/<name>.svg``, so every stack's
browser fetched the icon from production. It is now a root-relative path the
frontend serves from ``frontend/public/``.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tracer.services.clickhouse.v2.span_reader import CHSpan, CHSpanReader

# futureagi/tracer/tests/<this file> -> the repository root.
REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_PUBLIC = REPO_ROOT / "frontend" / "public"

MAPPED_PROVIDERS = (
    "openai",
    "anthropic",
    "google",
    "gcp.vertex.agent",
    "vapi",
    "retell",
)
NEW_ICON_FILES = ("assets/providers/vapi.svg", "assets/providers/retell.svg")
# frontend/nginx.conf serves every file under frontend/public (the /assets/
# location and the image-extension regex alike) with "expires 1y" and
# "Cache-Control: public, immutable". These names carry no content hash, so an
# icon edited in place would stay stale in browsers for a year.
NEW_ICON_SHA256 = {
    "assets/providers/vapi.svg": "08d7aac6fb82f2fb27f77a11c75a04caaae129f3d186a045ed997b6babed3838",
    "assets/providers/retell.svg": "a92c90bc3787820461a2b5a8377d2f50fdd61c95f11b8de6ec54ff81ca355361",
}


def _ch_span(provider: str) -> CHSpan:
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    return CHSpan(
        id="span_provider_icon",
        project_id="00000000-0000-0000-0000-0000000000aa",
        trace_id="00000000-0000-0000-0000-0000000000bb",
        parent_span_id="",
        name="chat",
        observation_type="llm",
        operation_name="",
        start_time=now,
        end_time=now,
        latency_ms=10,
        model="m",
        provider=provider,
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        cost=0.0,
        status="OK",
        status_message="",
        org_id=None,
        project_version_id=None,
        end_user_id=None,
        trace_session_id=None,
        prompt_version_id=None,
        prompt_label_id=None,
        custom_eval_config_id=None,
        input="",
        output="",
        tags="[]",
        span_events="[]",
        metadata="{}",
        resource_attrs="{}",
        attributes_extra="{}",
    )


def _logo(provider: str):
    return CHSpanReader.to_django_dict(_ch_span(provider))["provider_logo"]


def _require_frontend():
    if not FRONTEND_PUBLIC.is_dir():
        pytest.skip(f"{FRONTEND_PUBLIC} is not in this checkout (backend-only image)")


@pytest.mark.parametrize("provider", MAPPED_PROVIDERS)
def test_ch_span_provider_logo_is_a_root_relative_path(provider):
    logo = _logo(provider)
    assert isinstance(logo, str) and logo, provider
    parts = urlsplit(logo)
    assert not parts.scheme and not parts.netloc, f"{provider}: {logo} names a host"
    assert logo.startswith("/") and not logo.startswith("//"), logo
    assert "futureagi.com" not in logo


@pytest.mark.parametrize("provider", MAPPED_PROVIDERS)
def test_ch_span_provider_logo_is_a_file_the_frontend_serves(provider):
    _require_frontend()
    logo = _logo(provider)
    served = FRONTEND_PUBLIC / logo.lstrip("/")
    assert served.is_file(), f"{provider}: {logo} is not under frontend/public"
    assert served.stat().st_size > 0


def test_provider_lookup_ignores_case_and_surrounding_space():
    assert _logo("OpenAI") == _logo("openai")
    assert _logo(" Anthropic ") == _logo("anthropic")
    assert _logo("gcp.vertex.agent") == _logo("google")


@pytest.mark.parametrize("provider", ("", "some-unknown-provider"))
def test_unmapped_provider_has_no_logo(provider):
    # The UI draws its generic model icon for None.
    assert _logo(provider) is None


@pytest.mark.parametrize("rel", NEW_ICON_FILES)
def test_new_icon_svgs_render_as_img_on_both_themes(rel):
    _require_frontend()
    text = (FRONTEND_PUBLIC / rel).read_text(encoding="utf-8")
    root = ET.fromstring(text)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.get("viewBox"), rel
    # An <img> gives currentColor nothing to inherit, so it draws black: an
    # invisible icon on the dark theme. A fixed fill reads on both.
    assert "currentColor" not in text, rel
    fills = {
        el.get("fill") for el in root.iter() if el.get("fill") not in (None, "none")
    }
    assert fills, rel
    # No external reference of any kind inside the icon.
    assert "href" not in text and "url(" not in text, rel


@pytest.mark.parametrize("rel", NEW_ICON_FILES)
def test_new_icon_files_change_only_under_a_new_name(rel):
    # Browsers keep these for a year (NEW_ICON_SHA256). A changed icon gets a
    # new file name, PROVIDER_ICON_PATHS points at it, and its pin goes here.
    _require_frontend()
    digest = hashlib.sha256((FRONTEND_PUBLIC / rel).read_bytes()).hexdigest()
    assert digest == NEW_ICON_SHA256[rel], (
        f"{rel} changed in place, but nginx serves it immutable for a year: "
        "save the new icon under a new name, point PROVIDER_ICON_PATHS "
        "(tracer/constants/provider_icons.py) at it and pin it here"
    )
