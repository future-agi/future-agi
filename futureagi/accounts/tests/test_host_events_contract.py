"""READINESS lane 1.2a: the signed FA -> Omega EE event format, the operator config, and the rules
that turn a captured change into an event. No database; runs under any settings module.

The vectors in ``accounts/host_events/contract/omega-host-events.v1.json`` are the same bytes
EE's ``host-events-contract.test.ts`` verifies, so a change on either side that breaks the
other fails here or there.
"""

import json
import os
import stat
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from accounts.host_events import config as channel
from accounts.host_events.interpret import (
    EVENT_MEMBERSHIP_REMOVED,
    EVENT_ROLE_CHANGED,
    EVENT_USER_DEACTIVATED,
    Lookups,
    collapse,
    interpret,
)
from accounts.host_events.signing import (
    SIGNATURE_HEADER,
    encode_body,
    signature,
    signature_header,
)

CONTRACT = (
    Path(__file__).resolve().parents[1]
    / "host_events"
    / "contract"
    / "omega-host-events.v1.json"
)
USER, ORG, WS = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _contract():
    return json.loads(CONTRACT.read_text())


class TestContractVectors:
    def test_header_name_and_tolerance_match_the_contract(self):
        doc = _contract()
        assert doc["signatureHeader"] == SIGNATURE_HEADER
        assert doc["toleranceSeconds"] == 300

    def test_every_valid_vector_is_reproduced_byte_for_byte_by_the_fa_signer(self):
        doc = _contract()
        secret = doc["keys"][0]["secret"].encode()
        valid = [vector for vector in doc["vectors"] if vector["expect"] == "ok"]
        assert len(valid) >= 5
        for vector in valid:
            parts = dict(item.split("=", 1) for item in vector["header"].split(","))
            body = vector["body"].encode()
            assert (
                signature_header(secret, parts["kid"], body, now=int(parts["t"]))
                == vector["header"]
            )
            # The body is what encode_body produces for its own events: compact, key-sorted.
            document = json.loads(body)
            assert encode_body(document["issuer"], document["events"]) == body

    def test_negative_vectors_really_differ_from_a_valid_signature(self):
        doc = _contract()
        secret = doc["keys"][0]["secret"].encode()
        for vector in doc["vectors"]:
            if (
                vector["expect"] != "signature_invalid"
                or not vector["header"]
                or "t=" not in vector["header"]
            ):
                continue
            parts = dict(item.split("=", 1) for item in vector["header"].split(","))
            if parts.get("kid") != "fa-1":
                continue
            assert parts["v1"] != signature(
                secret, int(parts["t"]), vector["body"].encode()
            )

    def test_short_secrets_and_bad_key_ids_are_refused(self):
        with pytest.raises(ValueError):
            signature(b"short", 1, b"{}")
        with pytest.raises(ValueError):
            signature_header(b"x" * 32, "bad key id", b"{}", now=1)


def _secret_file(directory, content=b"s" * 48, mode=0o600):
    path = Path(directory) / "events.secret"
    path.write_bytes(content)
    path.chmod(mode)
    return str(path)


class TestChannelConfig:
    def _env(self, directory, **overrides):
        env = {
            "OMEGA_SQUAD_DELEGATION_ISSUER": "https://host.example/omega",
            "OMEGA_HOST_EVENTS_URL": "https://ee.internal/host/events",
            "OMEGA_HOST_EVENTS_KEY_ID": "fa-1",
            "OMEGA_HOST_EVENTS_SECRET_FILE": _secret_file(directory),
        }
        env.update(overrides)
        return {key: value for key, value in env.items() if value is not None}

    def test_disabled_when_omega_is_not_configured(self):
        assert channel.load_config({}).state == channel.DISABLED

    def test_ready_with_every_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            config = channel.load_config(self._env(directory))
        assert config.state == channel.READY
        assert config.secret == b"s" * 48
        assert "s" * 48 not in repr(config)

    @pytest.mark.parametrize(
        "overrides, problem",
        [
            ({"OMEGA_HOST_EVENTS_URL": None}, "url_missing"),
            (
                {"OMEGA_HOST_EVENTS_URL": "http://ee.internal/host/events"},
                "url_not_https",
            ),
            (
                {"OMEGA_HOST_EVENTS_URL": "https://user:pw@ee.internal/host/events"},
                "url_invalid",
            ),
            ({"OMEGA_HOST_EVENTS_URL": "https://ee.internal"}, "url_path_invalid"),
            ({"OMEGA_HOST_EVENTS_URL": "https://ee.internal:8443/"}, "url_path_invalid"),
            (
                {"OMEGA_HOST_EVENTS_URL": "https://ee.internal/host/events/"},
                "url_path_invalid",
            ),
            ({"OMEGA_HOST_EVENTS_KEY_ID": "not a key id"}, "key_id_invalid"),
            (
                {"OMEGA_HOST_EVENTS_SECRET_FILE": "relative/path"},
                "secret_path_not_absolute",
            ),
            ({"OMEGA_SQUAD_DELEGATION_ISSUER": None}, "issuer_missing"),
        ],
    )
    def test_misconfigured_names_the_problem_and_never_the_secret(
        self, overrides, problem
    ):
        with tempfile.TemporaryDirectory() as directory:
            config = channel.load_config(self._env(directory, **overrides))
        assert config.state == channel.MISCONFIGURED
        assert config.problem == problem
        assert config.secret == b""

    def test_plain_http_only_when_allowed_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self._env(
                directory, OMEGA_HOST_EVENTS_URL="http://127.0.0.1:18402/host/events"
            )
            env["OMEGA_HOST_EVENTS_ALLOW_HTTP"] = "1"
            assert channel.load_config(env).state == channel.READY

    def test_group_readable_short_or_symlinked_secrets_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            loose = self._env(directory)
            os.chmod(loose["OMEGA_HOST_EVENTS_SECRET_FILE"], 0o640)
            assert channel.load_config(loose).problem == "secret_file_unsafe"
            short = self._env(directory)
            Path(short["OMEGA_HOST_EVENTS_SECRET_FILE"]).write_bytes(b"too-short")
            assert channel.load_config(short).problem == "secret_length"
            link = Path(directory) / "link.secret"
            link.symlink_to(_secret_file(directory))
            assert channel.load_config(
                self._env(directory, OMEGA_HOST_EVENTS_SECRET_FILE=str(link))
            ).problem == ("secret_file_unreadable")
            assert stat.S_IMODE(os.stat(_secret_file(directory)).st_mode) == 0o600


# The variables only the Helm chart sets (deploy/helm/futureagi templates/omega/_validate.tpl).
# Where config.extraEnvFrom feeds a pod, the chart pins each one it does not set to "" in the
# container's env, which Kubernetes prefers to envFrom: blank must read as unset.
CHART_PINNED = (
    "OMEGA_SQUAD_DELEGATION_ISSUER",
    "OMEGA_HOST_EVENTS_URL",
    "OMEGA_HOST_EVENTS_KEY_ID",
    "OMEGA_HOST_EVENTS_SECRET_FILE",
    "OMEGA_HOST_EVENTS_ALLOW_HTTP",
    "OMEGA_HOST_EVENTS_CA_FILE",
)


class TestChartPinnedBlanks:
    def test_every_pinned_blank_leaves_the_channel_disabled(self):
        for blank in ("", "  "):
            config = channel.load_config(dict.fromkeys(CHART_PINNED, blank))
            assert config.state == channel.DISABLED

    def test_a_pinned_blank_ca_file_or_allow_http_keeps_the_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            env = TestChannelConfig()._env(directory)
            env.update(OMEGA_HOST_EVENTS_CA_FILE="", OMEGA_HOST_EVENTS_ALLOW_HTTP="")
            config = channel.load_config(env)
            assert config.state == channel.READY
            assert config.ca_file == ""
            env["OMEGA_HOST_EVENTS_URL"] = "http://ee.internal/host/events"
            assert channel.load_config(env).problem == "url_not_https"

    def test_a_pinned_blank_issuer_turns_delegation_off(self):
        # An envFrom source with the issuer, key id and key file would turn the session
        # exchange on; the chart's blank issuer in env wins, and the exchange refuses.
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        from accounts.omega_session import DelegationUnavailable, HostAssertionSigner

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / "private.pem"
            key_file.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            key_file.chmod(0o600)
            source = {
                "OMEGA_SQUAD_DELEGATION_ISSUER": "https://host.example/omega",
                "OMEGA_SQUAD_DELEGATION_KEY_ID": "k1",
                "OMEGA_SQUAD_DELEGATION_PRIVATE_KEY_FILE": str(key_file),
            }
            assert HostAssertionSigner.from_environment(source).issuer
            for blank in ("", "  "):
                with pytest.raises(DelegationUnavailable):
                    HostAssertionSigner.from_environment(
                        {**source, "OMEGA_SQUAD_DELEGATION_ISSUER": blank}
                    )


def _row(source, old, new, *, operation="UPDATE", org=ORG, ws=None):
    return SimpleNamespace(
        id=1,
        source=source,
        operation=operation,
        user_id=USER,
        organization_id=org,
        workspace_id=ws,
        old_state=old,
        new_state=new,
    )


def _lookups(org_level=None, organizations=()):
    return Lookups(
        org_level=lambda *_: org_level, organizations_of=lambda *_: set(organizations)
    )


def _member(level, *, active=True, deleted=False, role="Member"):
    return {"is_active": active, "deleted": deleted, "level": level, "role": role}


class TestInterpret:
    def test_org_removal_by_deactivate_soft_delete_or_hard_delete(self):
        for new in (_member(3, active=False), _member(3, deleted=True), None):
            event = interpret(
                _row("organization_membership", _member(3), new), _lookups()
            )
            assert event == {
                "type": EVENT_MEMBERSHIP_REMOVED,
                "subject": str(USER),
                "organizationId": str(ORG),
                "workspaceId": None,
                "role": None,
            }

    def test_demotion_to_viewer_is_a_removal_and_admin_to_member_a_role_change(self):
        assert interpret(
            _row("organization_membership", _member(8), _member(1)), _lookups()
        )["type"] == (EVENT_MEMBERSHIP_REMOVED)
        event = interpret(
            _row("organization_membership", _member(8), _member(3)), _lookups()
        )
        assert (event["type"], event["role"]) == (EVENT_ROLE_CHANGED, "member")
        assert (
            interpret(
                _row("organization_membership", _member(3), _member(15)), _lookups()
            )["role"]
            == "owner"
        )

    def test_changes_that_grant_access_or_change_nothing_send_nothing(self):
        lookups = _lookups()
        assert (
            interpret(
                _row("organization_membership", _member(3, active=False), _member(3)),
                lookups,
            )
            is None
        )
        assert (
            interpret(_row("organization_membership", _member(1), _member(3)), lookups)
            is None
        )
        assert (
            interpret(
                _row("organization_membership", _member(8), _member(8, role="Admin")),
                lookups,
            )
            is None
        )

    def test_legacy_rows_without_a_level_use_the_role_string(self):
        old = {"is_active": True, "deleted": False, "level": None, "role": "Admin"}
        new = {"is_active": True, "deleted": False, "level": None, "role": "Member"}
        assert (
            interpret(_row("organization_membership", old, new), _lookups())["role"]
            == "member"
        )

    def test_a_member_losing_a_workspace_is_a_workspace_removal(self):
        lookups = _lookups(org_level=3)
        for new in (_member(3, active=False), _member(1), None):
            event = interpret(
                _row("workspace_membership", _member(3), new, ws=WS), lookups
            )
            assert event["type"] == EVENT_MEMBERSHIP_REMOVED
            assert event["workspaceId"] == str(WS)

    def test_workspace_rows_never_affect_org_admins_and_admin_member_swaps_are_silent(
        self,
    ):
        assert (
            interpret(
                _row("workspace_membership", _member(3), None, ws=WS),
                _lookups(org_level=8),
            )
            is None
        )
        assert (
            interpret(
                _row("workspace_membership", _member(3), None, ws=WS),
                _lookups(org_level=15),
            )
            is None
        )
        assert (
            interpret(
                _row("workspace_membership", _member(8), _member(3), ws=WS),
                _lookups(org_level=3),
            )
            is None
        )

    def test_user_deactivation_names_every_organization_of_the_user(self):
        other = uuid.uuid4()
        event = interpret(
            _row("user", {"is_active": True}, {"is_active": False}, org=None),
            _lookups(organizations={ORG, other}),
        )
        assert event["type"] == EVENT_USER_DEACTIVATED
        assert event["organizations"] == sorted([str(ORG), str(other)])
        assert (
            interpret(_row("user", {"is_active": True}, None, org=None), _lookups())
            is None
        )

    def test_collapse_drops_workspace_removals_covered_by_an_org_removal(self):
        org_wide = interpret(
            _row("organization_membership", _member(3), None), _lookups()
        )
        ws = interpret(
            _row("workspace_membership", _member(3), None, ws=WS), _lookups(org_level=3)
        )
        other_org = dict(ws, organizationId=str(uuid.uuid4()))
        assert collapse([(1, org_wide), (2, ws), (3, other_org), (4, None)]) == [
            (1, org_wide),
            (2, None),
            (3, other_org),
            (4, None),
        ]
