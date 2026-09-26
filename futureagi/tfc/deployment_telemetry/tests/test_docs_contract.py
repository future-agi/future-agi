"""docs/telemetry.md must describe every field the sender can put on the wire
and every FUTURE_AGI_TELEMETRY_* setting it reads.

The page promises operators an exact list of what leaves their install; a
field or setting added here without a line there would quietly break that
promise. Skipped where the repository docs are not present (for example, in a
backend-only image).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tfc.deployment_telemetry import config
from tfc.deployment_telemetry.schema import (
    COUNT_FIELDS,
    HEARTBEAT_FIELDS,
    REGISTRATION_FIELDS,
    USER_FIELDS,
)

_DOC = Path(__file__).resolve().parents[4] / "docs" / "telemetry.md"


@pytest.fixture(scope="module")
def doc_text() -> str:
    if not _DOC.is_file():
        pytest.skip(f"{_DOC} not present in this checkout")
    return _DOC.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "field",
    sorted(REGISTRATION_FIELDS | HEARTBEAT_FIELDS | USER_FIELDS | set(COUNT_FIELDS)),
)
def test_every_wire_field_is_documented(doc_text, field):
    assert f"`{field}`" in doc_text, f"docs/telemetry.md does not describe `{field}`"


def test_every_telemetry_setting_is_documented(doc_text):
    source = Path(config.__file__).read_text(encoding="utf-8")
    settings = set(re.findall(r'"(FUTURE_AGI_TELEMETRY_[A-Z_]+)"', source))
    assert settings, "no FUTURE_AGI_TELEMETRY_* settings found in config.py"
    missing = sorted(name for name in settings if f"`{name}`" not in doc_text)
    assert not missing, f"docs/telemetry.md does not document {missing}"


def test_the_opt_out_is_documented(doc_text):
    assert "FUTURE_AGI_TELEMETRY_DISABLED=true" in doc_text
