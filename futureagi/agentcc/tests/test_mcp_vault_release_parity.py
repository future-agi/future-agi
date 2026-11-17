"""Release behaviour the MCP credential vault keeps (release behaviour wins
wherever release fixed something).

The release line kept MCP secrets inline as NG-01 ``enc::`` values. Stage 2
moves them into the vault (migration 0056); these are the rules the
release line enforced that the vault's own code must keep, plus the reads of a
legacy ``enc::`` leaf that a pod still on the release code can write during
the rollout:

- a URL the origin rule cannot read is a move, unless it is left exactly as
  stored (``moves_origin``);
- an NG-01 read mask is never a secret value (``new_secret``);
- a legacy ``enc::`` leaf is read as the push sends it, decrypted once, by
  the keep path of update-mcp-server and by the generic writers' origin check;
- the push places a secret under either spelling of ``managed_tools``.

No database: the vault's row lookups are replaced where a test reaches them.
Test values only, not real credentials.
"""

import uuid
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from agentcc.services import config_push, mcp_credential_lifecycle, mcp_credentials
from agentcc.services.mcp_credentials import (
    MCPCredentialError,
    MCPCredentialOriginChanged,
    Secret,
    console_server_entry,
    credential_origin_moves,
    moves_origin,
    new_secret,
)
from agentcc.services.org_config_secrets import (
    ENCRYPTED_PREFIX,
    MASK_STEM,
    _encrypt,
    _fingerprint,
)

URL = "https://mcp.s2int.example/mcp"
OTHER_HOST = "https://collector.s2int.example/mcp"
BAD_PORT = "https://mcp.s2int.example:99999/mcp"
BAD_BRACKET = "https://[mcp.s2int.example/mcp"
TOKEN = "test-s2int-not-a-real-token"


# --- moves_origin -------------------------------------------------------------


class TestAURLTheOriginRuleCannotReadIsAMove:
    """The release line's NG-01 rule: a URL whose origin cannot be read names
    no origin, so a kept secret does not follow a server there."""

    @pytest.mark.parametrize("bad", [BAD_PORT, BAD_BRACKET], ids=["port", "bracket"])
    def test_to_a_url_that_does_not_parse(self, bad):
        assert moves_origin(URL, bad) is True

    @pytest.mark.parametrize("bad", [BAD_PORT, BAD_BRACKET], ids=["port", "bracket"])
    def test_from_a_stored_url_that_does_not_parse(self, bad):
        assert moves_origin(bad, URL) is True

    def test_between_two_urls_that_do_not_parse(self):
        assert moves_origin(BAD_PORT, BAD_BRACKET) is True

    @pytest.mark.parametrize("bad", [BAD_PORT, BAD_BRACKET], ids=["port", "bracket"])
    def test_left_exactly_as_stored_is_not_a_move(self, bad):
        assert moves_origin(bad, bad) is False

    def test_controls(self):
        assert moves_origin(URL, "https://mcp.s2int.example/other?x=1") is False
        assert moves_origin(URL, "HTTPS://MCP.S2INT.EXAMPLE:443/mcp") is False
        assert moves_origin(URL, OTHER_HOST) is True
        assert moves_origin(URL, "http://mcp.s2int.example/mcp") is True
        assert moves_origin(URL, "https://mcp.s2int.example:8443/mcp") is True


# --- a read mask is not a secret ----------------------------------------------


class TestAReadMaskIsNeverASecret:
    """The release line refused an NG-01 read mask typed where a secret goes
    (400); the vault stored it as the literal token. A read of the vault never
    returns a mask, so a mask names some other field's secret."""

    @pytest.mark.parametrize(
        "mask",
        [MASK_STEM, MASK_STEM + _fingerprint(ENCRYPTED_PREFIX + "x")],
        ids=["stem", "stem-and-fingerprint"],
    )
    def test_new_secret_refuses_it(self, mask):
        with pytest.raises(MCPCredentialError, match="mask"):
            new_secret("bearer", mask)
        with pytest.raises(MCPCredentialError, match="mask"):
            new_secret("api_key", mask, "X-Api-Key")

    def test_update_mcp_server_refuses_it_as_a_token(self):
        mask = MASK_STEM + "0123456789abcdef"
        with pytest.raises(MCPCredentialError, match="mask"):
            mcp_credentials._sent_secret({"type": "bearer", "token": mask})

    def test_migration_0056_moves_no_mask_into_the_vault(self):
        # 0056 carries its own copy of new_secret's rules; it keeps this one.
        import importlib

        frozen = importlib.import_module("agentcc.migrations.0056_mcp_secrets_to_vault")
        for mask in (MASK_STEM, MASK_STEM + "0123456789abcdef"):
            assert frozen._usable("bearer", mask, None) is None
            assert frozen._usable("api_key", mask, "X-Api-Key") is None
        value = MASK_STEM + "not-a-fingerprint"
        assert frozen._usable("bearer", value, None) == ("bearer", value, "")

    def test_a_value_that_only_starts_like_one_is_a_secret(self):
        # As on the release line: only the exact stem, or the stem and a
        # 16-hex fingerprint, is a placeholder.
        value = MASK_STEM + "not-a-fingerprint"
        assert new_secret("bearer", value).value == value


# --- a legacy enc:: leaf, read as the push sends it ---------------------------


def _legacy(url, token):
    return {
        "url": url,
        "transport": "http",
        "auth": {"type": "bearer", "token": token},
    }


class TestTheGenericWritersOriginCheckReadsALegacyCiphertext:
    """credential_origin_moves: a generic write that moves a server while it
    leaves a legacy leaf in place is refused, as for a clear one. Read
    literally, an ``enc::`` leaf was no secret, the write passed, and the push
    (which decrypts the leaf) sent the secret to the new origin."""

    def test_an_enc_leaf_left_in_place_is_a_move(self):
        leaf = _encrypt(TOKEN)
        stored = {"servers": {"docs": _legacy(URL, leaf)}}
        value = {"servers": {"docs": _legacy(OTHER_HOST, leaf)}}
        assert credential_origin_moves(value, stored) == ["mcp.servers.docs.url"]

    def test_control_a_clear_leaf_left_in_place(self):
        stored = {"servers": {"docs": _legacy(URL, TOKEN)}}
        value = {"servers": {"docs": _legacy(OTHER_HOST, TOKEN)}}
        assert credential_origin_moves(value, stored) == ["mcp.servers.docs.url"]

    def test_control_a_path_change_keeps_it(self):
        leaf = _encrypt(TOKEN)
        stored = {"servers": {"docs": _legacy(URL, leaf)}}
        value = {"servers": {"docs": _legacy(URL + "/v2", leaf)}}
        assert credential_origin_moves(value, stored) == []

    def test_an_undecryptable_leaf_is_sent_nowhere_so_moves_nothing(self):
        # The push drops a well-formed token it cannot decrypt.
        other = Fernet(Fernet.generate_key()).encrypt(b'{"v": "x"}').decode()
        leaf = ENCRYPTED_PREFIX + other
        stored = {"servers": {"docs": _legacy(URL, leaf)}}
        value = {"servers": {"docs": _legacy(OTHER_HOST, leaf)}}
        assert credential_origin_moves(value, stored) == []

    def test_a_leaf_is_decrypted_exactly_once(self):
        # An NG-01 leaf reads as the value it was saved with. A value saved
        # that was itself some field's ciphertext (NG-01 encrypts it again,
        # never short-circuiting) reads as that ciphertext, which is no secret
        # the vault takes (it starts with enc::), never the value behind it.
        inner = _encrypt("some-other-fields-secret")
        stored = {"servers": {"docs": _legacy(URL, inner)}}
        clear = mcp_credentials._legacy_secret_as_pushed(
            "docs", stored["servers"]["docs"]
        )
        assert clear == Secret("bearer", "some-other-fields-secret", "")
        double = {"servers": {"docs": _legacy(URL, _encrypt(inner))}}
        assert (
            mcp_credentials._legacy_secret_as_pushed("docs", double["servers"]["docs"])
            is None
        )


class TestUpdateMcpServerKeepsALegacyCiphertext:
    """console_server_entry's keep path: an Edit save that leaves the secret
    blank moves the stored legacy secret into the vault. Read literally, an
    ``enc::`` leaf (written inline by a pod still on the release code during
    the rollout) was no secret, so the save dropped the server's auth."""

    @pytest.fixture
    def vault(self, monkeypatch):
        put = []
        row = SimpleNamespace(pk=uuid.uuid4())

        def put_shared_secret(org, server_id, secret, *, actor, origin):
            put.append((server_id, secret, origin))
            return row

        monkeypatch.setattr(mcp_credentials, "active_shared", lambda *a: None)
        monkeypatch.setattr(mcp_credentials, "put_shared_secret", put_shared_secret)
        monkeypatch.setattr(
            mcp_credentials, "_revoke_server_clients", lambda *a, **k: []
        )
        return SimpleNamespace(put=put, row=row)

    def test_a_blank_save_moves_the_decrypted_secret(self, vault):
        stored = _legacy(URL, _encrypt(TOKEN))
        entry = console_server_entry(
            SimpleNamespace(pk=1),
            "docs",
            {"url": URL, "transport": "http"},
            stored,
            actor=None,
        )
        # Bound to the origin of the url this save gives the server.
        assert vault.put == [
            ("docs", Secret("bearer", TOKEN, ""), "https://mcp.s2int.example:443")
        ]
        assert entry["credential"] == {
            "mode": "shared",
            "credential_id": str(vault.row.pk),
            "state": "active",
        }
        assert "auth" not in entry

    def test_a_blank_save_to_another_origin_is_refused(self, vault):
        stored = _legacy(URL, _encrypt(TOKEN))
        with pytest.raises(MCPCredentialOriginChanged):
            console_server_entry(
                SimpleNamespace(pk=1),
                "docs",
                {"url": OTHER_HOST, "transport": "http"},
                stored,
                actor=None,
            )
        assert vault.put == []

    def test_control_a_clear_legacy_secret(self, vault):
        console_server_entry(
            SimpleNamespace(pk=1),
            "docs",
            {"url": URL, "transport": "http"},
            _legacy(URL, TOKEN),
            actor=None,
        )
        assert vault.put == [
            ("docs", Secret("bearer", TOKEN, ""), "https://mcp.s2int.example:443")
        ]


# --- the push and the camelCase spelling --------------------------------------


class TestThePushPlacesAManagedSecretUnderEitherSpelling:
    """mcp.managed_tools has a camelCase spelling the contract reads
    (managedTools). The vault assembles a credential the section references
    under either spelling; the push must put its secret there, as the release
    line's decrypt path did, or the managed server loses its key."""

    SECRET = {"type": "bearer", "header": "Authorization", "value": TOKEN}

    def _payload(self, monkeypatch, mcp, *, state="active"):
        org_id = uuid.uuid4()
        managed = mcp.get("managed_tools", mcp.get("managedTools"))
        row = SimpleNamespace(
            pk=uuid.UUID(managed["credentials"]["fagi.web"]["credential_id"]),
            server_id="fagi.web",
            status=state,
            kind="bearer",
            token_expires_at=None,
            grant_revision=7,
        )

        def lifetime_rows(**filters):
            assert filters["organization_id"] == org_id
            assert filters["scope"] == "shared"
            assert set(filters["server_id__in"]) == {"fagi.web"}
            return SimpleNamespace(only=lambda *fields: [row])

        # The composed push reads lifetime metadata as well as secret values.
        # Stub its row lookup, not the lifetime overlay, so these no-DB spelling
        # tests still exercise the real authoritative-state merge.
        monkeypatch.setattr(
            mcp_credential_lifecycle.AgentccMCPCredential.objects,
            "filter",
            lifetime_rows,
        )
        monkeypatch.setattr(
            config_push,
            "_assemble_mcp_credentials",
            lambda org_id: {"fagi.web": dict(self.SECRET)},
        )
        return config_push._mcp_payload(org_id, SimpleNamespace(mcp=mcp))

    def _reference(self):
        return {
            "mode": "shared",
            "credential_id": str(uuid.uuid4()),
            "state": "active",
        }

    def test_camel_case(self, monkeypatch):
        mcp = {"managedTools": {"credentials": {"fagi.web": self._reference()}}}
        payload = self._payload(monkeypatch, mcp)
        assert payload["managedTools"]["credentials"]["fagi.web"]["secret"] == (
            self.SECRET
        )
        assert "secret" not in mcp["managedTools"]["credentials"]["fagi.web"]

    def test_control_snake_case(self, monkeypatch):
        mcp = {"managed_tools": {"credentials": {"fagi.web": self._reference()}}}
        payload = self._payload(monkeypatch, mcp)
        assert payload["managed_tools"]["credentials"]["fagi.web"]["secret"] == (
            self.SECRET
        )
        # The vault's grant revision travels with the secret, push-only.
        assert payload["managed_tools"]["credentials"]["fagi.web"]["revision"] == 7
        assert "revision" not in mcp["managed_tools"]["credentials"]["fagi.web"]

    @pytest.mark.parametrize("spelling", ["managed_tools", "managedTools"])
    @pytest.mark.parametrize("state", ["revoked", "expired", "needs_reauth"])
    def test_lapsed_metadata_wins_over_assembled_secret(
        self, monkeypatch, spelling, state
    ):
        mcp = {spelling: {"credentials": {"fagi.web": self._reference()}}}
        payload = self._payload(monkeypatch, mcp, state=state)
        credential = payload[spelling]["credentials"]["fagi.web"]
        assert credential["state"] == state
        assert "secret" not in credential
        assert mcp[spelling]["credentials"]["fagi.web"]["state"] == "active"

    def test_snake_case_wins_when_both_are_objects(self, monkeypatch):
        # The spelling the vault reads (mcp_credentials._managed_tools).
        mcp = {
            "managed_tools": {"credentials": {"fagi.web": self._reference()}},
            "managedTools": {"credentials": {"fagi.web": self._reference()}},
        }
        payload = self._payload(monkeypatch, mcp)
        assert payload["managed_tools"]["credentials"]["fagi.web"]["secret"] == (
            self.SECRET
        )
        assert "secret" not in payload["managedTools"]["credentials"]["fagi.web"]
