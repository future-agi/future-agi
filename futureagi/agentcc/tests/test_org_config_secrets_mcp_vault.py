"""The MCP part of org_config_secrets after ruling 3 of MCP-APPS-PLAN.md §2.1
(one MCP secret store, M14's vault).

MCP secrets live only in the MCP credential vault. NG-01's inline ``enc::``
stays for the cache, a2a, audit and alerting sections; for the mcp section
this module only finds the legacy leaves a release-line row can still hold:

- a config write never encrypts, keeps or resolves one (the vault's writers
  own MCP secrets, and refuse_mcp_secrets refuses a new one first), so no
  write puts ``enc::`` into the mcp section and an MCP secret's read mask
  never copies it into another section;
- ``clear_mcp_secrets`` reads them as the push sent them, for the move into
  the vault (agentcc_mcp_migrate_secrets);
- a read still masks one, and the push still decrypts one, until it moves.

Test values only, not real credentials.
"""

import copy
import json
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from agentcc.services import org_config_secrets as secrets
from agentcc.services.org_config_secrets import (
    MASK_STEM,
    SecretNotKept,
    _decrypt,
    _encrypt,
    _fingerprint,
    decrypt_payload_secrets,
    encrypt_config_secrets,
    mask_config_data,
)

URL = "https://mcp.s2v.example/mcp"
TOKEN = "test-s2v-ng01-not-a-real-token"
KEY = "test-s2v-ng01-not-a-real-key"
CRED = "test-s2v-ng01-cred-not-a-real-token"
MANAGED = "test-s2v-ng01-managed-not-a-real-key"
LOST = "test-s2v-ng01-lost-not-a-real-token"
REDIS = "test-s2v-ng01-redis-not-a-real-password"


def _other_key_token(clear):
    blob = Fernet(Fernet.generate_key()).encrypt(json.dumps({"v": clear}).encode())
    return "enc::" + blob.decode()


def _config(**sections):
    base = {name: {} for name in secrets.SECRET_SECTIONS}
    base.update(sections)
    return SimpleNamespace(**base)


def _mcp(token):
    return {
        "enabled": True,
        "servers": {
            "gh": {
                "transport": "http",
                "url": URL,
                "auth": {"type": "bearer", "token": token},
            }
        },
    }


class TestAConfigWriteLeavesTheMCPSectionToTheVault:
    def test_a_legacy_mcp_leaf_is_carried_over_as_it_is(self):
        # A row no NG-01 pass reached: the write encrypts the Redis password,
        # and leaves the MCP leaf for the move (it never writes enc:: there).
        stored = _config(
            cache={"redis": {"address": "r.s2v.example:6379", "password": REDIS}},
            mcp=_mcp(TOKEN),
        )
        new = copy.deepcopy(stored)
        encrypt_config_secrets(new, stored)
        assert _decrypt(new.cache["redis"]["password"]) == REDIS
        assert new.mcp == _mcp(TOKEN)

    def test_a_new_config_gets_no_enc_in_its_mcp_section(self):
        new = _config(
            mcp={
                **_mcp(TOKEN),
                "managed_tools": {
                    "credentials": {
                        "fagi.search": {
                            "secret": {
                                "type": "api_key",
                                "header": "X-K",
                                "value": MANAGED,
                            }
                        }
                    }
                },
            }
        )
        encrypt_config_secrets(new, None)
        assert "enc::" not in json.dumps(new.mcp)

    def test_a_blank_mcp_leaf_is_not_filled_from_the_stored_version(self):
        stored = _config(mcp=_mcp(_encrypt(TOKEN)))
        new = _config(mcp=_mcp(""))
        encrypt_config_secrets(new, stored)
        assert new.mcp["servers"]["gh"]["auth"]["token"] == ""

    def test_an_mcp_secrets_mask_is_never_copied_into_another_section(self):
        # Same origin, so the origin rule alone would let it through: the
        # mask of an MCP secret names nothing an inline section can keep.
        stored_token = _encrypt(TOKEN)
        stored = _config(mcp=_mcp(stored_token))
        mask = MASK_STEM + _fingerprint(stored_token)
        new = _config(
            mcp=_mcp(stored_token),
            a2a={
                "agents": {
                    "copy": {"url": URL, "auth": {"type": "bearer", "token": mask}}
                }
            },
        )
        with pytest.raises(SecretNotKept):
            encrypt_config_secrets(new, stored)

    def test_the_other_sections_keep_their_rules(self):
        stored = _config(
            cache={"redis": {"address": "r.s2v.example:6379", "password": REDIS}}
        )
        new = _config(
            cache={"redis": {"address": "r.s2v.example:6379", "password": ""}}
        )
        encrypt_config_secrets(new, stored)
        assert _decrypt(new.cache["redis"]["password"]) == REDIS


class TestTheMoveReadsALegacyLeafAsThePushSentIt:
    def test_ciphertext_is_decrypted_an_unreadable_token_removed_clear_kept(self):
        mcp = {
            "servers": {
                "gh": {
                    "url": URL,
                    "auth": {"type": "bearer", "token": _encrypt(TOKEN)},
                },
                "kv": {
                    "url": URL,
                    "auth": {"type": "api_key", "header": "X-K", "key": KEY},
                },
                "vault": {
                    "url": URL,
                    "credential": {
                        "mode": "shared",
                        "secret": {"type": "bearer", "value": _encrypt(CRED)},
                    },
                },
                "lost": {
                    "url": URL,
                    "auth": {"type": "bearer", "token": _other_key_token(LOST)},
                },
            },
            "managedTools": {
                "credentials": {
                    "fagi.search": {
                        "secret": {
                            "type": "api_key",
                            "header": "X-K",
                            "value": _encrypt(MANAGED),
                        }
                    }
                }
            },
        }
        original = copy.deepcopy(mcp)

        clear = secrets.clear_mcp_secrets(mcp)

        assert mcp == original  # the stored section is never changed
        servers = clear["servers"]
        assert servers["gh"]["auth"]["token"] == TOKEN
        assert servers["kv"]["auth"]["key"] == KEY
        assert servers["vault"]["credential"]["secret"]["value"] == CRED
        assert "token" not in servers["lost"]["auth"]
        managed = clear["managedTools"]["credentials"]["fagi.search"]["secret"]
        assert managed["value"] == MANAGED
        assert "enc::" not in json.dumps(clear)

    def test_a_section_with_no_secret_is_returned_equal(self):
        mcp = {"enabled": True, "servers": {"open": {"url": URL}}}
        assert secrets.clear_mcp_secrets(mcp) == mcp
        assert secrets.clear_mcp_secrets(None) is None


class TestALegacyLeafIsStillHiddenAndSentUntilItMoves:
    def test_a_read_masks_it(self):
        data = {"mcp": _mcp(_encrypt(TOKEN))}
        mask_config_data(data)
        assert data["mcp"]["servers"]["gh"]["auth"]["token"].startswith(MASK_STEM)
        assert data["secrets_set"] == ["/mcp/servers/gh/auth/token"]

    def test_the_push_decrypts_it(self):
        payload = {"mcp": _mcp(_encrypt(TOKEN))}
        decrypt_payload_secrets(payload)
        assert payload["mcp"]["servers"]["gh"]["auth"]["token"] == TOKEN


def test_the_post_org_configs_stand_in_is_retired():
    # M14's refuse_mcp_secrets refuses a new MCP secret on every write path
    # (P1 adaptation 1); NG-01's stand-in for it is gone.
    assert not hasattr(secrets, "new_clear_mcp_secret_paths")
    assert not hasattr(secrets, "SecretInConfig")
