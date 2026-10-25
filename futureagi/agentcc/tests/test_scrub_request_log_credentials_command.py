"""The agentcc_scrub_request_log_credentials management command.

It reports the credential headers held in stored request-log rows by
organization and key id, never by value, and with --apply masks them.
"""

import hashlib
from io import StringIO

import pytest
from django.core.management import call_command

from accounts.models import Organization
from agentcc.models import AgentccAPIKey, AgentccRequestLog

# Planted test values with a gateway key's shape, not real keys.
KNOWN_KEY = "sk-agentcc-1111111111111111111111111111111111111111111111aa"
REVOKED_KEY = "sk-agentcc-2222222222222222222222222222222222222222222222bb"
UNKNOWN_KEY = "sk-agentcc-3333333333333333333333333333333333333333333333cc"
AMZ_TOKEN = "planted-amz-security-token-4d5e"
UPSTREAM_COOKIE = "session=planted-upstream-cookie-6f70"
# A key from a self-hosted gateway's config file: any string, no prefix.
CONFIG_FILE_KEY = "planted-config-file-key-8a9b"
VALUES = (
    KNOWN_KEY,
    REVOKED_KEY,
    UNKNOWN_KEY,
    AMZ_TOKEN,
    UPSTREAM_COOKIE,
    CONFIG_FILE_KEY,
)


def _run(*args):
    out = StringIO()
    call_command("agentcc_scrub_request_log_credentials", *args, stdout=out)
    return out.getvalue()


def _row(org, request_id, request_headers, response_headers=None, **fields):
    return AgentccRequestLog.no_workspace_objects.create(
        organization=org,
        request_id=request_id,
        model="gemini-2.5-flash",
        request_headers=request_headers,
        response_headers=response_headers,
        **fields,
    )


def _stored(row):
    row = AgentccRequestLog._base_manager.get(pk=row.pk)
    return row.request_headers, row.response_headers


@pytest.mark.integration
class TestScrubRequestLogCredentials:
    @pytest.fixture
    def other_org(self, db):
        return Organization.objects.create(name="Scrub Other Org")

    @pytest.fixture
    def rows(self, organization, workspace, other_org):
        AgentccAPIKey.no_workspace_objects.create(
            organization=organization,
            gateway_key_id="key_goog",
            name="genai client",
            key_hash=hashlib.sha256(KNOWN_KEY.encode()).hexdigest(),
        )
        AgentccAPIKey.no_workspace_objects.create(
            organization=organization,
            gateway_key_id="key_revoked",
            name="old client",
            status=AgentccAPIKey.REVOKED,
            key_hash=hashlib.sha256(REVOKED_KEY.encode()).hexdigest(),
        )
        return {
            # A Google GenAI SDK call: the key in X-Goog-Api-Key, Authorization masked.
            "goog": _row(
                organization,
                "scrub-goog",
                {
                    "X-Goog-Api-Key": [KNOWN_KEY],
                    "Authorization": "***",
                    "X-Amz-Security-Token": [AMZ_TOKEN],
                    "Content-Type": ["application/json"],
                },
                api_key_id="key_goog",
            ),
            "goog-again": _row(
                organization,
                "scrub-goog-2",
                {"X-Goog-Api-Key": [KNOWN_KEY]},
                api_key_id="key_goog",
            ),
            "revoked": _row(
                organization,
                "scrub-revoked",
                {"Proxy-Authorization": [f"Bearer {REVOKED_KEY}"]},
                api_key_id="key_revoked",
            ),
            # A key no stored hash matches (a key synced without its hash),
            # in another org, on a row that was soft-deleted since.
            "unknown": _row(
                other_org,
                "scrub-unknown",
                {"X-Goog-Api-Key": [UNKNOWN_KEY]},
                response_headers={"Set-Cookie": [UPSTREAM_COOKIE]},
                api_key_id="key_b_request",
                deleted=True,
            ),
            "config-file": _row(
                organization,
                "scrub-config-file",
                {"X-Goog-Api-Key": [CONFIG_FILE_KEY]},
                api_key_id="key_cfg",
            ),
            "clean": _row(
                organization,
                "scrub-clean",
                {"Authorization": "***", "Content-Type": ["application/json"]},
            ),
        }

    def test_dry_run_reports_by_org_and_key_id_and_changes_nothing(
        self, rows, organization, other_org
    ):
        before = {name: _stored(row) for name, row in rows.items()}

        out = _run()

        assert [value for value in VALUES if value in out] == []
        assert "Scanned 6 request log row(s); 5 held a credential" in out
        assert (
            f"org {organization.id}: gateway key key_goog (active) in 2 row(s), "
            "sent as X-Goog-Api-Key. Rotate it." in out
        )
        assert (
            f"org {organization.id}: gateway key key_revoked (revoked) in 1 row(s), "
            "sent as Proxy-Authorization. It does not authenticate." in out
        )
        assert (
            f"org {other_org.id}: a gateway key no stored key hash matches, in 1 "
            "row(s), sent as X-Goog-Api-Key; the request was made with key "
            "key_b_request: rotate that key." in out
        )
        assert (
            f"org {organization.id}: a gateway key no stored key hash matches, in 1 "
            "row(s), sent as X-Goog-Api-Key; the request was made with key "
            "key_cfg: rotate that key." in out
        )
        assert (
            f"org {organization.id}: other credential headers: X-Amz-Security-Token "
            "in 1 row(s)." in out
        )
        assert f"org {other_org.id}: other credential headers: Set-Cookie in 1" in out
        assert "Dry run: nothing was changed." in out
        assert {name: _stored(row) for name, row in rows.items()} == before

    def test_apply_masks_every_credential_header_and_keeps_the_rest(self, rows):
        out = _run("--apply")

        assert [value for value in VALUES if value in out] == []
        assert "Masked the credential headers of 5 row(s)." in out
        assert _stored(rows["goog"]) == (
            {
                "X-Goog-Api-Key": "***",
                "Authorization": "***",
                "X-Amz-Security-Token": "***",
                "Content-Type": ["application/json"],
            },
            None,
        )
        assert _stored(rows["unknown"]) == (
            {"X-Goog-Api-Key": "***"},
            {"Set-Cookie": "***"},
        )
        assert _stored(rows["clean"]) == (
            {"Authorization": "***", "Content-Type": ["application/json"]},
            None,
        )
        stored = repr([_stored(row) for row in rows.values()])
        assert [value for value in VALUES if value in stored] == []

        again = _run()

        assert "Scanned 6 request log row(s); 0 held a credential" in again
        assert "Dry run" not in again

    def test_org_limits_the_scan(self, rows, organization, other_org):
        out = _run("--org", str(other_org.id), "--apply")

        assert "Scanned 1 request log row(s); 1 held a credential" in out
        assert str(organization.id) not in out
        assert _stored(rows["goog"])[0]["X-Goog-Api-Key"] == [KNOWN_KEY]
        assert _stored(rows["unknown"])[0] == {"X-Goog-Api-Key": "***"}

    def test_small_batches_cover_every_row(self, rows):
        out = _run("--batch-size", "1", "--apply")

        assert "Scanned 6 request log row(s); 5 held a credential" in out
        assert "Masked the credential headers of 5 row(s)." in out


@pytest.mark.integration
def test_headers_stored_as_something_but_an_object_are_dropped(organization, workspace):
    # A headers value that is not an object cannot be checked name by name, so
    # --apply drops it, as ingestion does.
    row = _row(organization, "scrub-list", [["X-Goog-Api-Key", KNOWN_KEY]])

    out = _run()

    assert KNOWN_KEY not in out
    assert (
        f"org {organization.id}: other credential headers: (headers not stored "
        "as an object) in 1 row(s)." in out
    )
    assert _stored(row) == ([["X-Goog-Api-Key", KNOWN_KEY]], None)

    _run("--apply")

    assert _stored(row) == (None, None)
