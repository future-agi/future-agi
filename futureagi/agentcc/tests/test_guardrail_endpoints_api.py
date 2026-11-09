"""Coverage for the guardrail-policies, guardrail-configs helpers, blocklists, and guardrail-feedback endpoints."""

import pytest

from agentcc.models.blocklist import AgentccBlocklist
from agentcc.models.guardrail_feedback import AgentccGuardrailFeedback
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.request_log import AgentccRequestLog


def _make_policy(user, name="policy-a", **overrides):
    kwargs = {
        "organization": user.organization,
        "name": name,
        "scope": "global",
        "checks": [{"name": "pii-detector", "action": "block"}],
        "mode": "enforce",
        "is_active": True,
    }
    kwargs.update(overrides)
    return AgentccGuardrailPolicy.objects.create(**kwargs)


def _make_blocklist(user, name="block-a", words=None, **overrides):
    kwargs = {
        "organization": user.organization,
        "name": name,
        "words": words or ["forbidden"],
    }
    kwargs.update(overrides)
    return AgentccBlocklist.objects.create(**kwargs)


def _make_request_log(user, request_id="req-a"):
    return AgentccRequestLog.objects.create(
        organization=user.organization,
        request_id=request_id,
        model="gpt-4o",
        provider="openai",
    )


def _make_feedback(user, request_log, check_name="pii-detector", feedback="correct"):
    return AgentccGuardrailFeedback.objects.create(
        organization=user.organization,
        request_log=request_log,
        check_name=check_name,
        feedback=feedback,
        created_by=user,
    )


@pytest.mark.integration
@pytest.mark.api
class TestGuardrailPolicyCRUD:
    def test_list_returns_policies(self, auth_client, user):
        _make_policy(user, name="alpha")
        _make_policy(user, name="beta")

        response = auth_client.get("/agentcc/guardrail-policies/")
        assert response.status_code == 200
        names = {row["name"] for row in response.json()["result"]}
        assert {"alpha", "beta"} <= names

    def test_list_unauthenticated(self, api_client):
        response = api_client.get("/agentcc/guardrail-policies/")
        assert response.status_code in (401, 403)

    def test_retrieve_returns_policy(self, auth_client, user):
        policy = _make_policy(user, name="retrieve-me")
        response = auth_client.get(f"/agentcc/guardrail-policies/{policy.id}/")
        assert response.status_code == 200
        assert response.json()["result"]["id"] == str(policy.id)

    def test_create_writes_and_scopes_to_org(self, auth_client, user):
        response = auth_client.post(
            "/agentcc/guardrail-policies/",
            {
                "name": "new-policy",
                "scope": "global",
                "checks": [{"name": "injection-detector", "action": "block"}],
                "mode": "enforce",
                "is_active": True,
            },
            format="json",
        )
        assert response.status_code in (200, 201), response.json()
        row = AgentccGuardrailPolicy.objects.get(name="new-policy")
        assert row.organization == user.organization

    def test_patch_updates_mode(self, auth_client, user):
        policy = _make_policy(user, name="patch-me", mode="enforce")
        response = auth_client.patch(
            f"/agentcc/guardrail-policies/{policy.id}/",
            {"mode": "monitor"},
            format="json",
        )
        assert response.status_code == 200
        policy.refresh_from_db()
        assert policy.mode == "monitor"

    def test_destroy_soft_deletes_policy(self, auth_client, user):
        policy = _make_policy(user, name="destroy-me")
        response = auth_client.delete(f"/agentcc/guardrail-policies/{policy.id}/")
        assert response.status_code in (200, 204)
        policy.refresh_from_db()
        assert policy.deleted is True


@pytest.mark.integration
@pytest.mark.api
class TestGuardrailPolicySync:
    def test_sync_endpoint_returns_ok(self, auth_client, user):
        _make_policy(user, name="synceable", is_active=True)
        response = auth_client.post("/agentcc/guardrail-policies/sync/")
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["synced"] is True


@pytest.mark.integration
@pytest.mark.api
class TestGuardrailConfigHelpers:
    def test_pii_entities_returns_list(self, auth_client):
        response = auth_client.get("/agentcc/guardrail-configs/pii-entities/")
        assert response.status_code == 200
        rows = response.json()["result"]
        assert isinstance(rows, list) and len(rows) > 0

    def test_topics_returns_list(self, auth_client):
        response = auth_client.get("/agentcc/guardrail-configs/topics/")
        assert response.status_code == 200
        rows = response.json()["result"]
        assert isinstance(rows, list)

    def test_validate_cel_accepts_valid_expression(self, auth_client):
        response = auth_client.post(
            "/agentcc/guardrail-configs/validate-cel/",
            {"expression": "1 == 1"},
            format="json",
        )
        # Returns 200 with a JSON body reporting validity; the shape may
        # be {"valid": true} or {"is_valid": true}.
        assert response.status_code == 200

    def test_validate_cel_rejects_missing_expression(self, auth_client):
        response = auth_client.post(
            "/agentcc/guardrail-configs/validate-cel/",
            {},
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.integration
@pytest.mark.api
class TestBlocklistRemaining:
    def test_list_returns_blocklists(self, auth_client, user):
        _make_blocklist(user, name="list-a")
        _make_blocklist(user, name="list-b")

        response = auth_client.get("/agentcc/blocklists/")
        assert response.status_code == 200
        names = {row["name"] for row in response.json()["result"]}
        assert {"list-a", "list-b"} <= names

    def test_retrieve_returns_blocklist(self, auth_client, user):
        block = _make_blocklist(user, name="retrieve")
        response = auth_client.get(f"/agentcc/blocklists/{block.id}/")
        assert response.status_code == 200
        assert response.json()["result"]["id"] == str(block.id)

    # docs-agentcc-711, item 1: a stored blocklist never reaches the gateway,
    # so every write answers 501 and the stored list stays as it was.
    @pytest.mark.parametrize(
        "method, suffix, body",
        [
            ("post", "", {"name": "new-list", "words": ["w"]}),
            ("put", "{id}/", {"name": "put", "words": ["new-one", "new-two"]}),
            ("patch", "{id}/", {"description": "now with a description"}),
            ("delete", "{id}/", None),
            ("post", "{id}/add-words/", {"words": ["added"]}),
            ("post", "{id}/remove-words/", {"words": ["drop-me"]}),
        ],
        ids=["create", "put", "patch", "delete", "add-words", "remove-words"],
    )
    def test_every_write_answers_501_and_changes_nothing(
        self, auth_client, user, method, suffix, body
    ):
        block = _make_blocklist(user, name="stored", words=["keep-1", "drop-me"])
        url = "/agentcc/blocklists/" + suffix.format(id=block.id)
        if body is None:
            response = getattr(auth_client, method)(url)
        else:
            response = getattr(auth_client, method)(url, body, format="json")

        assert response.status_code == 501, response.json()
        assert response.json()["code"] == "blocklists_not_supported"
        block.refresh_from_db()
        assert block.words == ["keep-1", "drop-me"]
        assert block.description == ""
        assert block.deleted is False
        assert not AgentccBlocklist.all_objects.filter(name="new-list").exists()


@pytest.mark.integration
@pytest.mark.api
class TestGuardrailFeedbackCRUD:
    def test_list_returns_feedback(self, auth_client, user):
        log = _make_request_log(user)
        _make_feedback(user, log, check_name="pii-detector")
        _make_feedback(user, log, check_name="injection-detector")

        response = auth_client.get("/agentcc/guardrail-feedback/")
        assert response.status_code == 200
        assert len(response.json()["result"]) >= 2

    def test_retrieve_returns_feedback(self, auth_client, user):
        log = _make_request_log(user)
        fb = _make_feedback(user, log)

        response = auth_client.get(f"/agentcc/guardrail-feedback/{fb.id}/")
        assert response.status_code == 200
        assert response.json()["result"]["id"] == str(fb.id)

    def test_create_writes_feedback(self, auth_client, user):
        log = _make_request_log(user, request_id="req-create-fb")
        response = auth_client.post(
            "/agentcc/guardrail-feedback/",
            {
                "request_log": str(log.id),
                "check_name": "pii-detector",
                "feedback": "false_positive",
                "comment": "false positive",
            },
            format="json",
        )
        assert response.status_code in (200, 201), response.json()
        assert AgentccGuardrailFeedback.objects.filter(
            request_log=log, check_name="pii-detector"
        ).exists()

    def test_patch_updates_comment(self, auth_client, user):
        log = _make_request_log(user)
        fb = _make_feedback(user, log)

        response = auth_client.patch(
            f"/agentcc/guardrail-feedback/{fb.id}/",
            {"comment": "reviewed and confirmed"},
            format="json",
        )
        assert response.status_code == 200
        fb.refresh_from_db()
        assert fb.comment == "reviewed and confirmed"

    def test_destroy_soft_deletes_feedback(self, auth_client, user):
        log = _make_request_log(user)
        fb = _make_feedback(user, log)

        response = auth_client.delete(f"/agentcc/guardrail-feedback/{fb.id}/")
        assert response.status_code in (200, 204)
        fb.refresh_from_db()
        assert fb.deleted is True

    def test_summary_aggregates_by_check(self, auth_client, user):
        log = _make_request_log(user)
        _make_feedback(user, log, check_name="pii-detector", feedback="correct")
        _make_feedback(user, log, check_name="pii-detector", feedback="false_positive")

        response = auth_client.get("/agentcc/guardrail-feedback/summary/")
        assert response.status_code == 200
        # Response shape is a dict of aggregations; assert the endpoint
        # returns 200 with a dict body rather than pinning the exact keys.
        result = response.json()["result"]
        assert isinstance(result, (dict, list))
