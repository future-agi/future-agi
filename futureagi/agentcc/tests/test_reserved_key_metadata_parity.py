"""Django and the gateway must reserve the same key metadata.

Django rejects these entries from tenants and strips them at key sync; the
gateway refuses changes to them on an existing key. The two lists hold the same names.
"""

import re
from pathlib import Path

from agentcc.validators import RESERVED_KEY_METADATA

_KEYSTORE = (
    Path(__file__).resolve().parents[3] / "agentcc-gateway/internal/auth/keystore.go"
)


def _gateway_reserved_keys():
    source = _KEYSTORE.read_text()
    block = re.search(
        r"^var reservedMetadataKeys = map\[string\]struct\{\}\{\n(.*?)^\}",
        source,
        re.MULTILINE | re.DOTALL,
    )
    assert block, f"reservedMetadataKeys not found in {_KEYSTORE}"
    return set(re.findall(r'^\s*"([^"]+)":\s*\{\},', block.group(1), re.MULTILINE))


def test_django_and_the_gateway_reserve_the_same_key_metadata():
    gateway = _gateway_reserved_keys()

    assert gateway, "parsed no reserved keys from the gateway"
    assert gateway == set(RESERVED_KEY_METADATA)
