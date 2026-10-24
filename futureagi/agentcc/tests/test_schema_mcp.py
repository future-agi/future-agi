"""MCP-era Django schema for the gateway.

The MCP credential vault, tool-call, tool-revision, approval, registry and
OAuth models, the delegated grant and the memory-forget receipt, the attempt
``hollow`` outcome and request-log ``evidence`` column (migration 0043), and
the URL and schedule placeholders.
"""

import base64
import importlib
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.urls import reverse
from django.urls.resolvers import URLResolver
from django.utils import timezone

from accounts.models.user import User
from agentcc.models import (
    AgentccDelegatedGrant,
    AgentccMCPApproval,
    AgentccMCPCredential,
    AgentccMCPOAuthClient,
    AgentccMCPOAuthConsent,
    AgentccMCPOAuthRefreshToken,
    AgentccMCPOAuthSigningKey,
    AgentccMCPRegistryEntry,
    AgentccMCPToolRevision,
    AgentccMemoryForget,
    AgentccRequestAttempt,
    AgentccRequestLog,
    AgentccToolCall,
)
from agentcc.services.credential_manager import encrypt_token
from agentcc.tests.test_schema_v2 import EXISTING_ROUTES
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    get_current_organization,
    set_workspace_context,
)

# Obvious test values: none of them is a real credential.
PLAINTEXT_SECRET = "mcp-upstream-test-secret-not-a-real-credential"
PLAINTEXT_REFRESH = "mcp-refresh-test-secret-not-a-real-credential"
PLAINTEXT_PRIVATE_KEY = "es256-test-private-key-not-a-real-key"
# A client ID metadata document URL well past 512 characters.
LONG_CIMD_CLIENT_ID = "https://app.example.test/oauth/" + "c" * 600 + "/client.json"

MCP_MIGRATIONS = [
    "0035_mcp_credential",
    "0036_mcp_tool_call",
    "0037_mcp_tool_revision",
    "0038_mcp_approval",
    "0039_mcp_registry",
    "0040_mcp_oauth",
    "0041_mcp_indexes",
    "0042_delegated_grant",
    "0043_attempt_hollow_request_log_evidence",
    "0044_memory_forget",
]

# index name -> (columns, orders), created concurrently in 0041.
TOOL_CALL_INDEXES = {
    "agentcc_tc_org_started_idx": (
        ["organization_id", "started_at"],
        ["ASC", "DESC"],
    ),
    "agentcc_tc_org_run_idx": (["organization_id", "agent_run_id"], ["ASC", "ASC"]),
    "agentcc_tc_org_server_tool_idx": (
        ["organization_id", "server_id", "tool", "started_at"],
        ["ASC", "ASC", "ASC", "DESC"],
    ),
    "agentcc_tc_org_key_idx": (
        ["organization_id", "api_key_id", "started_at"],
        ["ASC", "ASC", "DESC"],
    ),
    "agentcc_tc_org_generation_idx": (
        ["organization_id", "generation_id"],
        ["ASC", "ASC"],
    ),
}

# column -> (Django field type, max_length); mcp-tool-call-v1 and the agent
# columns as on the request log.
TOOL_CALL_COLUMNS = {
    "event_id": ("CharField", 64),
    "tool_call_id": ("CharField", 64),
    "request_id": ("CharField", 64),
    "generation_id": ("CharField", 64),
    # An OAuth principal's client_id; a CIMD client_id is an HTTPS URL.
    "client_id": ("CharField", 2048),
    "agent_framework": ("CharField", 32),
    "agent_id": ("CharField", 128),
    "agent_version": ("CharField", 128),
    "agent_run_id": ("CharField", 128),
    "agent_parent_run_id": ("CharField", 128),
    "agent_root_run_id": ("CharField", 128),
    "agent_task_id": ("CharField", 128),
    "agent_trace_id": ("CharField", 128),
    "agent_workflow": ("CharField", 128),
    "agent_step": ("CharField", 32),
    "agent_turn": ("IntegerField", None),
    "agent_identity_source": ("CharField", 32),
    "budget_scope": ("CharField", 128),
    "upstream_cost_micros": ("BigIntegerField", None),
    "billed_cost_micros": ("BigIntegerField", None),
    "markup_micros": ("BigIntegerField", None),
    "arguments": ("JSONField", None),
    "result": ("JSONField", None),
    "evidence": ("JSONField", None),
}

AGENTCC_PLACEHOLDER_URL_MODULES = [
    "agentcc.urls_mcp_credentials",
    "agentcc.urls_mcp_toolcalls",
    "agentcc.urls_mcp_revisions",
    "agentcc.urls_mcp_approvals",
    "agentcc.urls_mcp_connect",
    "agentcc.urls_mcp_oauth",
    "agentcc.urls_mcp_registry",
    "agentcc.urls_run_envelope",
    "agentcc.urls_identity",
    "agentcc.urls_mcp_doctor",
    "agentcc.urls_mcp_review_pack",
    "agentcc.urls_mcp_policy",
    "agentcc.urls_importers",
    "agentcc.urls_enterprise_evidence",
    "agentcc.urls_budgets",
    "agentcc.urls_mcp_vendors",
    "agentcc.urls_memory",
]
WELLKNOWN_URL_MODULE = "agentcc.urls_mcp_wellknown"


def _indexes(table):
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table)
    return {name: c for name, c in constraints.items() if c["index"]}


def _other_user(organization):
    return User.objects.create_user(
        email=f"test-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Other User",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )


def _credential(organization, server_id="github", **fields):
    return AgentccMCPCredential.objects.create(
        organization=organization,
        server_id=server_id,
        scope=fields.pop("scope", AgentccMCPCredential.Scope.SHARED),
        kind=fields.pop("kind", AgentccMCPCredential.Kind.BEARER),
        encrypted_secret=fields.pop(
            "encrypted_secret", encrypt_token(PLAINTEXT_SECRET)
        ),
        **fields,
    )


@pytest.mark.integration
class TestMCPCredential:
    def test_secret_without_enc_prefix_raises(self, organization):
        with pytest.raises(ValidationError) as exc_info:
            _credential(organization, encrypted_secret=PLAINTEXT_SECRET)

        assert "encrypted_secret" in exc_info.value.message_dict
        assert PLAINTEXT_SECRET not in str(exc_info.value)
        assert not AgentccMCPCredential.objects.exists()

    def test_bare_prefix_raises(self, organization):
        with pytest.raises(ValidationError):
            _credential(organization, encrypted_secret="enc::")

    @pytest.mark.parametrize(
        "value",
        [
            # Clear text behind the prefix.
            "enc::" + PLAINTEXT_SECRET,
            "enc::gAAAAABfake",
            # Fernet-sized, but not Fernet version 0x80.
            "enc::" + base64.urlsafe_b64encode(b"\x81" + bytes(72)).decode(),
        ],
        ids=["prefixed-clear-text", "short-token", "wrong-version-byte"],
    )
    def test_prefix_without_fernet_ciphertext_raises(self, organization, value):
        with pytest.raises(ValidationError) as exc_info:
            _credential(organization, encrypted_secret=value)

        assert "encrypted_secret" in exc_info.value.message_dict
        assert value not in str(exc_info.value)
        assert not AgentccMCPCredential.objects.exists()

    def test_oauth_client_id_holds_a_cimd_url(self, organization):
        row = _credential(
            organization,
            kind=AgentccMCPCredential.Kind.OAUTH_CLIENT,
            oauth_client_id=LONG_CIMD_CLIENT_ID,
        )

        row.refresh_from_db()
        assert row.oauth_client_id == LONG_CIMD_CLIENT_ID

    def test_refresh_without_enc_prefix_raises(self, organization):
        with pytest.raises(ValidationError) as exc_info:
            _credential(
                organization,
                kind=AgentccMCPCredential.Kind.OAUTH,
                encrypted_refresh=PLAINTEXT_REFRESH,
            )

        assert "encrypted_refresh" in exc_info.value.message_dict
        assert PLAINTEXT_REFRESH not in str(exc_info.value)

    def test_encrypted_secret_and_refresh_save(self, organization, user):
        expires = timezone.now() + timedelta(hours=1)
        row = _credential(
            organization,
            scope=AgentccMCPCredential.Scope.USER,
            user=user,
            kind=AgentccMCPCredential.Kind.OAUTH,
            encrypted_refresh=encrypt_token(PLAINTEXT_REFRESH),
            token_expires_at=expires,
            oauth_issuer="https://auth.example.test",
            oauth_client_id="https://app.example.test/client.json",
            scopes=["repo", "read:org"],
            label="GitHub",
            account_label="work",
            created_by=user,
        )

        row.refresh_from_db()
        assert row.status == AgentccMCPCredential.Status.ACTIVE
        assert row.version == 1
        assert row.scopes == ["repo", "read:org"]
        assert row.token_expires_at == expires
        assert row.created_at is not None
        assert row.rotated_at is None
        assert row.last_used_at is None
        assert row.expiry_alerted_at is None
        assert PLAINTEXT_SECRET not in row.encrypted_secret
        assert PLAINTEXT_REFRESH not in row.encrypted_refresh

    def test_update_with_plaintext_secret_raises(self, organization):
        row = _credential(organization)
        row.encrypted_secret = PLAINTEXT_SECRET

        with pytest.raises(ValidationError):
            row.save(update_fields=["encrypted_secret"])

    def test_update_of_other_fields_saves(self, organization):
        row = _credential(organization)
        row.last_used_at = timezone.now()

        row.save(update_fields=["last_used_at"])

        row.refresh_from_db()
        assert row.last_used_at is not None

    def test_two_active_shared_credentials_for_one_server_raise(self, organization):
        _credential(organization)

        with pytest.raises(IntegrityError), transaction.atomic():
            _credential(organization)

    def test_same_account_label_raises_and_different_labels_save(self, organization):
        _credential(organization, account_label="work")
        with pytest.raises(IntegrityError), transaction.atomic():
            _credential(organization, account_label="work")

        _credential(organization, account_label="personal")

        assert AgentccMCPCredential.objects.filter(server_id="github").count() == 2

    def test_inactive_credentials_do_not_block_an_active_one(self, organization):
        status = AgentccMCPCredential.Status
        for inactive in (status.REVOKED, status.EXPIRED, status.NEEDS_REAUTH):
            _credential(organization, status=inactive)
        _credential(organization)
        _credential(organization, server_id="linear")

        active = AgentccMCPCredential.objects.filter(status=status.ACTIVE)
        assert active.count() == 2

    def test_user_credentials_are_unique_per_user(self, organization, user):
        user_scope = AgentccMCPCredential.Scope.USER
        _credential(organization, scope=user_scope, user=user)
        with pytest.raises(IntegrityError), transaction.atomic():
            _credential(organization, scope=user_scope, user=user)

        _credential(organization, scope=user_scope, user=_other_user(organization))
        _credential(organization)

        assert AgentccMCPCredential.objects.count() == 3

    def test_user_is_set_exactly_for_user_scope(self, organization, user):
        with pytest.raises(IntegrityError), transaction.atomic():
            _credential(organization, scope=AgentccMCPCredential.Scope.USER)
        with pytest.raises(IntegrityError), transaction.atomic():
            _credential(organization, user=user)

    def test_kind_values(self):
        assert set(AgentccMCPCredential.Kind.values) == {
            "bearer",
            "api_key",
            "header",
            "oauth",
            "oauth_client",
        }

    def test_repr_and_str_never_show_the_secret(self, organization):
        row = _credential(
            organization,
            kind=AgentccMCPCredential.Kind.OAUTH,
            encrypted_refresh=encrypt_token(PLAINTEXT_REFRESH),
            header_name="Authorization",
        )

        for text in (repr(row), str(row)):
            for secret in (
                PLAINTEXT_SECRET,
                PLAINTEXT_REFRESH,
                row.encrypted_secret,
                row.encrypted_refresh,
            ):
                assert secret not in text
            assert "github" in text


def _tool_call(organization, event_id="tc_1", **fields):
    return AgentccToolCall.objects.create(
        organization=organization,
        event_id=event_id,
        tool_call_id=fields.pop("tool_call_id", event_id),
        request_id=fields.pop("request_id", "req-1"),
        started_at=fields.pop("started_at", timezone.now()),
        server_id=fields.pop("server_id", "github"),
        tool=fields.pop("tool", "create_issue"),
        **fields,
    )


@pytest.mark.integration
class TestToolCall:
    def test_columns_match_contract(self):
        for name, (field_type, size) in TOOL_CALL_COLUMNS.items():
            field = AgentccToolCall._meta.get_field(name)
            assert field.get_internal_type() == field_type, name
            if size is not None:
                assert field.max_length == size, name
        assert AgentccToolCall._meta.get_field("event_id").unique is True

    def test_duplicate_event_id_raises(self, organization):
        _tool_call(organization)

        with pytest.raises(IntegrityError), transaction.atomic():
            _tool_call(organization, tool_call_id="tc_other")

    def test_record_fields_round_trip(self, organization):
        row = _tool_call(
            organization,
            ts_ms=1_760_000_000_000,
            api_key_id="key_1",
            principal_kind="key",
            virtual_server_id="vs_01JZ0000000000000000000000",
            upstream_tool="create_issue",
            tool_revision="a" * 64,
            via="call_tool",
            era="2026-07-28",
            protocol_version="2026-07-28",
            agent_framework="omega",
            agent_run_id="run-1",
            agent_root_run_id="run-root",
            agent_step="tool",
            agent_turn=3,
            session_id="sess-1",
            generation_id="req-1",
            outcome="is_error",
            is_error=True,
            error_class="upstream_4xx",
            error_origin="upstream",
            error_code="upstream_error",
            error_message="bad request",
            latency_ms=120,
            upstream_latency_ms=100,
            gateway_overhead_ms=20,
            bytes_in=300,
            bytes_out=4_000_000_000,
            billing_mode="managed",
            credential_type="managed",
            credential_source="managed",
            fallback_from="expired",
            upstream_cost_micros=2_000,
            billed_cost_micros=2_100,
            markup_micros=100,
            markup_bps=500,
            cost_source="tool_price",
            usage_estimated=True,
            dispatched=True,
            budget_scope="run:run-1",
            guardrail={"checked": ["pii"], "blocked": [], "masked": [], "shadow": []},
            approval_id="apr_1",
            content_mode="full",
            arguments={"title": "bug"},
            result={"content": [{"type": "text", "text": "ok"}]},
        )

        row.refresh_from_db()
        assert row.agent_turn == 3
        assert row.bytes_out == 4_000_000_000
        assert row.billed_cost_micros == 2_100
        assert row.is_error is True
        assert row.guardrail["checked"] == ["pii"]
        assert row.arguments == {"title": "bug"}

    def test_oauth_principal_with_a_long_cimd_client_id(self, organization):
        client = _oauth_client(client_id=LONG_CIMD_CLIENT_ID)
        row = _tool_call(
            organization,
            principal_kind="oauth",
            user_id="user-1",
            client_id=client.client_id,
        )

        row.refresh_from_db()
        assert len(row.client_id) > 512
        assert row.client_id == client.client_id

    def test_free_call_defaults(self, organization):
        row = _tool_call(organization)

        row.refresh_from_db()
        assert row.upstream_cost_micros == 0
        assert row.billed_cost_micros == 0
        assert row.markup_micros == 0
        assert row.is_error is False
        assert row.dispatched is False
        assert row.arguments is None
        assert row.result is None

    def test_tool_call_evidence_column(self, organization):
        field = AgentccToolCall._meta.get_field("evidence")
        assert field.null is True
        evidence = {
            "identity": {"sub": "user-1", "source": "idp"},
            "account_label": "work",
            "policy": {"rule": "p1", "action": "approve", "shadow": False},
            "flow": {"state_before": "clean", "decision": "allow", "sources": []},
            "shaped": {"original_bytes": 9000, "returned_bytes": 900, "spilled": True},
        }

        plain = _tool_call(organization)
        with_evidence = _tool_call(organization, event_id="tc_2", evidence=evidence)

        plain.refresh_from_db()
        with_evidence.refresh_from_db()
        assert plain.evidence is None
        assert with_evidence.evidence == evidence

    def test_the_five_indexes_exist(self, db):
        indexes = _indexes("agentcc_tool_call")
        for name, (columns, orders) in TOOL_CALL_INDEXES.items():
            assert name in indexes, name
            assert indexes[name]["columns"] == columns, name
            assert indexes[name]["orders"] == orders, name
            assert indexes[name]["unique"] is False, name


def _revision(organization, revision="a" * 64, **fields):
    return AgentccMCPToolRevision.objects.create(
        organization=organization,
        server_id=fields.pop("server_id", "github"),
        tool=fields.pop("tool", "create_issue"),
        revision=revision,
        definition=fields.pop("definition", {"name": "create_issue"}),
        **fields,
    )


@pytest.mark.integration
class TestToolRevision:
    def test_duplicate_revision_raises(self, organization):
        _revision(organization)

        with pytest.raises(IntegrityError), transaction.atomic():
            _revision(organization)

    def test_next_revision_and_other_tools_save(self, organization, user):
        _revision(organization)
        row = _revision(
            organization,
            revision="b" * 64,
            previous_revision="a" * 64,
            diff=[{"field": "description", "before": "x", "after": "y"}],
            status=AgentccMCPToolRevision.Status.APPROVED,
            decided_by=user,
            decided_at=timezone.now(),
        )
        _revision(organization, tool="list_issues")

        row.refresh_from_db()
        assert row.first_seen_at is not None
        assert row.diff[0]["field"] == "description"
        assert AgentccMCPToolRevision.objects.count() == 3

    def test_status_values(self):
        assert set(AgentccMCPToolRevision.Status.values) == {
            "pending",
            "approved",
            "rejected",
            "superseded",
        }
        assert _field_default(AgentccMCPToolRevision, "status") == "pending"


def _field_default(model, name):
    return model._meta.get_field(name).get_default()


@pytest.mark.integration
class TestApproval:
    def _approval(self, organization, approval_id="apr_1", **fields):
        return AgentccMCPApproval.objects.create(
            organization=organization,
            approval_id=approval_id,
            virtual_server_id="vs_01JZ0000000000000000000000",
            tool=fields.pop("tool", "delete_repo"),
            args_hash="c" * 64,
            requested_at=timezone.now(),
            expires_at=timezone.now() + timedelta(minutes=15),
            **fields,
        )

    def test_duplicate_approval_id_raises(self, organization):
        self._approval(organization)

        with pytest.raises(IntegrityError), transaction.atomic():
            self._approval(organization, tool="other")

    def test_decision_round_trips(self, organization, user):
        row = self._approval(
            organization,
            agent_run_id="run-1",
            api_key_id="key_1",
            status=AgentccMCPApproval.Status.APPROVED,
            decided_by=user,
            decided_at=timezone.now(),
            note="looks fine",
        )

        row.refresh_from_db()
        assert row.status == "approved"
        assert row.decided_by_id == user.id
        assert row.note == "looks fine"

    def test_status_values(self):
        assert set(AgentccMCPApproval.Status.values) == {
            "pending",
            "approved",
            "denied",
            "expired",
            "used",
        }
        assert _field_default(AgentccMCPApproval, "status") == "pending"


def _registry_entry(organization, name="io.github.example/server", **fields):
    return AgentccMCPRegistryEntry.objects.create(
        organization=organization,
        name=name,
        version=fields.pop("version", "1.0.0"),
        server_json=fields.pop("server_json", {"name": name}),
        source=fields.pop("source", AgentccMCPRegistryEntry.Source.OFFICIAL),
        **fields,
    )


@pytest.mark.integration
class TestRegistryEntry:
    def test_duplicate_org_entry_raises(self, organization):
        _registry_entry(organization, source=AgentccMCPRegistryEntry.Source.ORG)

        with pytest.raises(IntegrityError), transaction.atomic():
            _registry_entry(organization, source=AgentccMCPRegistryEntry.Source.ORG)

    def test_duplicate_mirror_entry_raises(self, db):
        # null organization = the FutureAGI mirror, still unique per version.
        _registry_entry(None)

        with pytest.raises(IntegrityError), transaction.atomic():
            _registry_entry(None)

    def test_other_versions_and_orgs_save(self, organization, user):
        _registry_entry(None)
        _registry_entry(None, version="1.1.0")
        row = _registry_entry(
            organization,
            source=AgentccMCPRegistryEntry.Source.ORG,
            review_status=AgentccMCPRegistryEntry.ReviewStatus.APPROVED,
            reviewed_by=user,
            reviewed_at=timezone.now(),
            probe={
                "ok": True,
                "era": "2026-07-28",
                "tool_count": 4,
                "tools_hash": "d" * 64,
                "latency_ms": 80,
                "probed_at": "2026-10-16T00:00:00Z",
                "error": None,
            },
            upstream_updated_at=timezone.now(),
        )

        row.refresh_from_db()
        assert row.probe["tool_count"] == 4
        assert row.deleted is False
        assert AgentccMCPRegistryEntry.objects.count() == 3

    def test_mirror_entry_stays_global_inside_an_org_context(self, organization):
        # tfc/utils/signals.py fills a null organization from the request's
        # org context on create (pre_save) and on every later save (post_save).
        set_workspace_context(organization=organization)
        try:
            row = _registry_entry(None)
            row.review_status = AgentccMCPRegistryEntry.ReviewStatus.APPROVED
            row.save()

            row.refresh_from_db()
            assert row.organization_id is None
            assert get_current_organization() == organization
        finally:
            clear_workspace_context()

    def test_new_entries_wait_for_review(self, db):
        row = _registry_entry(None)

        assert row.review_status == AgentccMCPRegistryEntry.ReviewStatus.PENDING
        assert set(AgentccMCPRegistryEntry.Source.values) == {
            "official",
            "org",
            "futureagi",
        }


def _oauth_client(client_id="https://app.example.test/client.json", **fields):
    return AgentccMCPOAuthClient.objects.create(
        client_id=client_id,
        kind=fields.pop("kind", AgentccMCPOAuthClient.Kind.CIMD),
        **fields,
    )


def _signing_key(kid="k1", **fields):
    return AgentccMCPOAuthSigningKey.objects.create(
        kid=kid,
        encrypted_private=fields.pop(
            "encrypted_private", encrypt_token(PLAINTEXT_PRIVATE_KEY)
        ),
        public_jwk=fields.pop(
            "public_jwk", {"kty": "EC", "crv": "P-256", "kid": kid, "x": "x", "y": "y"}
        ),
        **fields,
    )


@pytest.mark.integration
class TestOAuth:
    def test_client_id_is_unique(self, db):
        _oauth_client()

        with pytest.raises(IntegrityError), transaction.atomic():
            _oauth_client(kind=AgentccMCPOAuthClient.Kind.DCR)

    def test_client_consent_and_refresh_token_save(self, organization, user):
        client = _oauth_client(
            kind=AgentccMCPOAuthClient.Kind.PREREGISTERED,
            metadata={"client_name": "VS Code"},
            redirect_uris=["http://127.0.0.1/callback"],
            application_type="native",
            last_fetched_at=timezone.now(),
        )
        resource = "https://gw.example.test/mcp/v/vs_01JZ0000000000000000000000"
        consent = AgentccMCPOAuthConsent.objects.create(
            user=user,
            client=client,
            organization=organization,
            scopes=["mcp:tools"],
            resource=resource,
        )
        first = AgentccMCPOAuthRefreshToken.objects.create(
            token_hash="e" * 64,
            user=user,
            client=client,
            organization=organization,
            resource=resource,
            expires_at=timezone.now() + timedelta(days=30),
        )
        second = AgentccMCPOAuthRefreshToken.objects.create(
            token_hash="f" * 64,
            user=user,
            client=client,
            organization=organization,
            resource=resource,
            expires_at=timezone.now() + timedelta(days=30),
            rotated_from=first,
        )

        consent.refresh_from_db()
        second.refresh_from_db()
        assert consent.granted_at is not None
        assert consent.revoked_at is None
        assert second.rotated_from_id == first.id
        assert client.redirect_uris == ["http://127.0.0.1/callback"]

    def test_refresh_token_hash_is_unique(self, organization, user):
        client = _oauth_client()
        fields = {
            "token_hash": "e" * 64,
            "user": user,
            "client": client,
            "organization": organization,
            "resource": "https://gw.example.test/mcp",
            "expires_at": timezone.now() + timedelta(days=30),
        }
        AgentccMCPOAuthRefreshToken.objects.create(**fields)

        with pytest.raises(IntegrityError), transaction.atomic():
            AgentccMCPOAuthRefreshToken.objects.create(**fields)

    def test_signing_key_without_enc_prefix_raises(self, db):
        with pytest.raises(ValidationError) as exc_info:
            _signing_key(encrypted_private=PLAINTEXT_PRIVATE_KEY)

        assert "encrypted_private" in exc_info.value.message_dict
        assert PLAINTEXT_PRIVATE_KEY not in str(exc_info.value)
        assert not AgentccMCPOAuthSigningKey.objects.exists()

    def test_signing_key_kid_is_unique(self, db):
        _signing_key()

        with pytest.raises(IntegrityError), transaction.atomic():
            _signing_key()

    def test_signing_key_defaults(self, db):
        row = _signing_key()

        assert row.alg == "ES256"
        assert row.status == AgentccMCPOAuthSigningKey.Status.ACTIVE
        assert set(AgentccMCPOAuthSigningKey.Status.values) == {
            "active",
            "retiring",
            "retired",
        }

    def test_signing_key_repr_and_str_never_show_the_private_key(self, db):
        row = _signing_key()

        for text in (repr(row), str(row)):
            assert PLAINTEXT_PRIVATE_KEY not in text
            assert row.encrypted_private not in text
            assert "k1" in text


@pytest.mark.integration
class TestDelegatedGrant:
    def test_grant_saves_and_repr_holds_no_secret(self, organization, user):
        rule = {"tool": "github/create_issue", "args": {"repo": "acme/private-repo"}}
        grant = AgentccDelegatedGrant.objects.create(
            organization=organization,
            grantor=user,
            agent_id="agent-1",
            scopes=["mcp:tools"],
            preapproved_rules=[rule],
            max_usd=Decimal("12.50"),
            expires_at=timezone.now() + timedelta(hours=1),
        )

        grant.refresh_from_db()
        assert grant.max_usd == Decimal("12.50")
        assert grant.revoked_at is None
        assert grant.created_at is not None
        for text in (repr(grant), str(grant)):
            assert "acme/private-repo" not in text
            assert user.email not in text
            assert "agent-1" in text


@pytest.mark.integration
class TestMemoryForget:
    def test_receipt_saves_without_the_subject(self, organization, user):
        receipts = [
            {
                "server": "mem0",
                "status": "ok",
                "attempts": 1,
                "vendor_request_id": "r1",
            },
            {"server": "zep", "status": "failed", "attempts": 3},
        ]
        row = AgentccMemoryForget.objects.create(
            organization=organization,
            subject_hash="9" * 64,
            requested_by=user,
            status="partial",
            receipts=receipts,
        )

        row.refresh_from_db()
        assert row.receipts == receipts
        assert row.created_at is not None
        assert row.completed_at is None
        field_names = {f.name for f in AgentccMemoryForget._meta.get_fields()}
        assert "subject" not in field_names


@pytest.mark.integration
class TestMigration0043:
    def test_attempt_outcome_hollow_choice(self, organization):
        hollow = AgentccRequestAttempt.Outcome.HOLLOW
        assert hollow == "hollow"
        assert "hollow" in dict(
            AgentccRequestAttempt._meta.get_field("outcome").choices
        )

        attempt = AgentccRequestAttempt(
            organization=organization,
            request_id="req-1",
            attempt_id="req-1.1",
            index=1,
            kind=AgentccRequestAttempt.Kind.PRIMARY,
            outcome=hollow,
        )
        attempt.full_clean(exclude=["request_log"])
        attempt.save()

        attempt.refresh_from_db()
        assert attempt.outcome == "hollow"

    def test_request_log_evidence_column(self, organization):
        field = AgentccRequestLog._meta.get_field("evidence")
        assert field.get_internal_type() == "JSONField"
        assert field.null is True
        evidence = {
            "budget_hold": {"held_micros": 1000},
            "identity": {"sub": "user-1"},
            "eval_export": "forbidden",
        }

        plain = AgentccRequestLog.objects.create(organization=organization)
        stored = AgentccRequestLog.objects.create(
            organization=organization, request_id="req-2", evidence=evidence
        )

        plain.refresh_from_db()
        stored.refresh_from_db()
        assert plain.evidence is None
        assert stored.evidence == evidence


class TestMigrationChain:
    def test_mcp_migrations_follow_0034_in_order(self):
        graph = MigrationLoader(None, ignore_no_migrations=True).graph
        parent = "0034_request_outcome"
        for name in MCP_MIGRATIONS:
            assert ("prism", parent) in graph.node_map[("prism", name)].parents, name
            parent = name
        # One prism leaf, with 0044 in its history; later migrations follow, so the
        # leaf itself is not pinned here.
        (prism_leaf,) = graph.leaf_nodes("prism")
        assert ("prism", MCP_MIGRATIONS[-1]) in graph.forwards_plan(prism_leaf)

    def test_index_migration_runs_concurrently_outside_a_transaction(self):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        migration = loader.get_migration("prism", "0041_mcp_indexes")

        assert migration.atomic is False
        assert {op.index.name for op in migration.operations} == set(TOOL_CALL_INDEXES)
        assert {op.model_name for op in migration.operations} == {"agentcctoolcall"}
        assert all(
            type(op).__name__ == "AddIndexConcurrently" for op in migration.operations
        )

    def test_tool_call_table_is_created_without_its_indexes(self):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        migration = loader.get_migration("prism", "0036_mcp_tool_call")
        (create,) = migration.operations

        assert create.options.get("indexes", []) == []

    def test_0043_touches_only_the_two_fields(self):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        migration = loader.get_migration(
            "prism", "0043_attempt_hollow_request_log_evidence"
        )

        assert sorted(
            (type(op).__name__, op.model_name, op.name) for op in migration.operations
        ) == [
            ("AddField", "agentccrequestlog", "evidence"),
            ("AlterField", "agentccrequestattempt", "outcome"),
        ]


def _included(urlpatterns, module):
    return [
        p
        for p in urlpatterns
        if isinstance(p, URLResolver) and p.urlconf_name is module
    ]


class TestUrlPlaceholders:
    @pytest.mark.parametrize("name,kwargs,path", EXISTING_ROUTES)
    def test_existing_url_names_still_reverse(self, name, kwargs, path):
        assert reverse(name, kwargs=kwargs) == path

    @pytest.mark.parametrize("module_name", AGENTCC_PLACEHOLDER_URL_MODULES)
    def test_placeholder_is_empty_and_included_ahead_of_the_router(self, module_name):
        module = importlib.import_module(module_name)
        agentcc_urls = importlib.import_module("agentcc.urls")
        includes = _included(agentcc_urls.urlpatterns, module)

        assert module.urlpatterns == []
        assert len(includes) == 1
        assert str(includes[0].pattern) == ""
        first_router_pattern = agentcc_urls.router.urls[0]
        assert agentcc_urls.urlpatterns.index(
            includes[0]
        ) < agentcc_urls.urlpatterns.index(first_router_pattern)

    def test_wellknown_placeholder_is_included_at_the_site_root(self):
        module = importlib.import_module(WELLKNOWN_URL_MODULE)
        root_urls = importlib.import_module("tfc.urls")
        agentcc_urls = importlib.import_module("agentcc.urls")
        includes = _included(root_urls.urlpatterns, module)

        assert module.urlpatterns == []
        assert len(includes) == 1
        assert str(includes[0].pattern) == ""
        assert _included(agentcc_urls.urlpatterns, module) == []


class TestSchedulePlaceholders:
    def test_placeholders_are_empty_lists(self):
        from tfc.temporal.schedules import (
            agentcc_mcp_credentials,
            agentcc_mcp_revisions,
        )

        assert agentcc_mcp_revisions.AGENTCC_MCP_REVISION_SCHEDULES == []
        assert agentcc_mcp_credentials.AGENTCC_MCP_CREDENTIAL_SCHEDULES == []

    def test_all_schedules_includes_both_lists(self, monkeypatch):
        import tfc.temporal.schedules as schedules
        from tfc.temporal.schedules import (
            agentcc_mcp_credentials,
            agentcc_mcp_revisions,
        )

        revisions, credentials = object(), object()
        monkeypatch.setattr(
            agentcc_mcp_revisions, "AGENTCC_MCP_REVISION_SCHEDULES", [revisions]
        )
        monkeypatch.setattr(
            agentcc_mcp_credentials, "AGENTCC_MCP_CREDENTIAL_SCHEDULES", [credentials]
        )
        try:
            reloaded = importlib.reload(schedules)
            assert revisions in reloaded.ALL_SCHEDULES
            assert credentials in reloaded.ALL_SCHEDULES
        finally:
            monkeypatch.undo()
            importlib.reload(schedules)
