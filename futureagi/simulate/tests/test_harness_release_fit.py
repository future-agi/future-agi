"""How a connected agent fits a ready environment, an adaptation's design awaiting approval, and
one asked to run its measurable cases."""

import uuid
from datetime import UTC, datetime

import pytest
from rest_framework.test import APIClient

from simulate.models import (
    AgentDefinition,
    AgentVersion,
    HarnessEnvironmentRelease,
    HarnessSuiteRelease,
)
from simulate.services import harness_fit
from simulate.services.harness_environment import status_for
from simulate.services.harness_fit import (
    release_conflicts,
    release_fit,
    suggested_pairing,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    canonical_digest,
    create_hosted_job,
)
from simulate.services.hosted_harness_conversation import (
    enqueue_message,
    issue_conversation_capability,
)
from simulate.tests.test_hosted_harness_conversation import BASE, _headers, _payload

ADAPTATIONS = "/simulate/api/harness-adaptations"


def _tool(name, *parameters):
    return {
        "name": name,
        "parameters": {"type": "object", "properties": {p: {} for p in parameters}},
    }


WORLD = [
    _tool("verify_order_owner", "email", "order_id", "customer_id", "postal_code"),
    _tool("get_order", "order_id"),
    _tool("get_order_invoice", "order_id", "customer_id"),
    _tool("search_orders", "email", "status", "customer_id", "postal_code"),
    _tool("get_return_policy", "category"),
    _tool("cancel_order", "reason", "order_id", "confirmed", "customer_id"),
    _tool("cancel_return", "confirmed", "return_id", "customer_id"),
    _tool("create_draft", "to", "subject", "body"),
]
AGENT = [
    _tool("check_owner", "order", "zip"),
    _tool("order_details", "order"),
    _tool("find_orders", "zip", "email", "state"),
    _tool("returns_policy", "kind"),
    _tool("cancel", "order", "customer", "why", "ok"),
]


class TestSuggestedPairing:
    def test_tools_that_read_alike_are_paired_each_once(self):
        assert {
            world: agent
            for world, agent in suggested_pairing(AGENT, WORLD).items()
            if agent
        } == {
            "verify_order_owner": "check_owner",
            "get_order": "order_details",
            "search_orders": "find_orders",
            "get_return_policy": "returns_policy",
            "cancel_order": "cancel",
        }

    def test_the_same_name_is_paired_first(self):
        pairing = suggested_pairing([_tool("get_order", "order_id")], WORLD)
        assert pairing["get_order"] == "get_order"
        assert pairing["get_order_invoice"] is None

    def test_a_tool_with_nothing_in_common_stays_unpaired(self):
        pairing = suggested_pairing([_tool("play_music", "song")], WORLD)
        assert not any(pairing.values())


@pytest.fixture
def retail(organization):
    environment = HarnessEnvironmentRelease.no_workspace_objects.create(
        organization=organization,
        digest="sha256:" + "e" * 64,
        name="retail",
        object_key="releases/retail",
        manifest={},
        tools=WORLD,
        policy={
            "rules": [
                {
                    "id": "verify_first",
                    "text": "Verify the owner before discussing an order.",
                    "class": "agent_policy",
                },
                {
                    "id": "confirm_cancel",
                    "text": "Confirm before cancelling an order.",
                    "class": "agent_policy",
                },
                {
                    "id": "orders_owned",
                    "text": "An order is only disclosed to its owner.",
                    "class": "system_invariant",
                },
            ]
        },
    )
    return HarnessSuiteRelease.no_workspace_objects.create(
        organization=organization,
        digest="sha256:" + "s" * 64,
        name="retail_base",
        environment=environment,
        object_key="releases/retail_base",
        manifest={"scenarios": []},
        personas=[],
    )


@pytest.fixture
def shopmate(organization):
    definition = AgentDefinition.no_workspace_objects.create(
        agent_name="ShopMate",
        agent_type="text",
        provider="retell",
        assistant_id="agent_shopmate",
        inbound=True,
        organization=organization,
        description="",
    )
    return AgentVersion.no_workspace_objects.create(
        agent_definition=definition,
        version_number=1,
        version_name="v1",
        organization=organization,
        description="",
        commit_message="",
    )


INSTRUCTIONS = (
    "You are ShopMate. Check the owner with check_owner before discussing an order.\n"
    "Cancel straight away when asked; no need to confirm."
)


def _model_says(monkeypatch, answer):
    """The model answers `answer`, or raises it when it is an exception."""

    def ask(task, material):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(harness_fit, "_ask_model", ask)


@pytest.fixture
def fitted(monkeypatch):
    from django.core.cache import cache

    cache.clear()
    # Without a model the pairs are suggested by name.
    _model_says(monkeypatch, HostedHarnessError("agentcc_not_configured", "no gateway"))
    profile = {
        "response_engine_type": "retell-llm",
        "general_prompt": INSTRUCTIONS,
        "general_tools": [
            {"type": "custom", "url": "https://shop.example/tools", **tool}
            for tool in AGENT
        ]
        + [{"type": "end_call", "name": "end_call", "description": "End the call."}],
    }
    monkeypatch.setattr(harness_fit, "profile_for_version", lambda version: profile)
    monkeypatch.setattr(
        harness_fit,
        "_solution_tools",
        lambda object_key, suite_json: (
            (
                "verify-then-cancel",
                "Verify, then cancel",
                frozenset({"verify_order_owner", "cancel_order"}),
            ),
            ("policy-question", "Policy question", frozenset({"get_return_policy"})),
            (
                "reply-by-email",
                "Reply by email",
                frozenset({"create_draft", "get_order"}),
            ),
            ("refuse-politely", "Refuse politely", frozenset()),
        ),
    )


@pytest.mark.django_db
class TestReleaseFit:
    def test_the_suggestion_measures_every_case_its_tools_reach(
        self, retail, shopmate, fitted
    ):
        fit = release_fit(retail, shopmate)

        rows = {row["world"]: row for row in fit["rows"]}
        assert rows["verify_order_owner"]["agent"] == "check_owner"
        assert rows["verify_order_owner"]["kind"] == "mapped"
        assert rows["create_draft"]["kind"] == "missing"
        assert fit["agent"] == {"name": "ShopMate", "version": "v1"}
        assert fit["total"] == 4
        assert fit["unmeasured"] == [
            {"key": "reply-by-email", "title": "Reply by email"}
        ]
        # The platform's own actions are never paired with the world's tools.
        assert [one["name"] for one in fit["platform_actions"]] == ["end_call"]

    def test_a_pairing_the_person_changes_is_counted_again(
        self, retail, shopmate, fitted
    ):
        fit = release_fit(
            retail,
            shopmate,
            {"cancel_order": None, "get_order": "get_order", "nope": "cancel"},
        )

        rows = {row["world"]: row for row in fit["rows"]}
        assert rows["cancel_order"]["kind"] == "missing"
        # A tool the agent doesn't have is not a pairing.
        assert rows["get_order"]["agent"] == "order_details"
        assert {case["key"] for case in fit["unmeasured"]} == {
            "verify-then-cancel",
            "reply-by-email",
        }

    def test_the_model_pairs_tools_by_what_they_do(
        self, retail, shopmate, fitted, monkeypatch
    ):
        _model_says(
            monkeypatch,
            {
                "pairs": [
                    {
                        "world": "get_order_invoice",
                        "agent": "order_details",
                        "reason": "Reads an order.",
                    },
                    {
                        "world": "get_order",
                        "agent": "order_details",
                        "reason": "Reads one order by its number.",
                    },
                    {"world": "create_draft", "agent": "no_such_tool", "reason": "x"},
                ]
            },
        )

        fit = release_fit(retail, shopmate)

        rows = {row["world"]: row for row in fit["rows"]}
        # Each agent tool pairs once, the first pair the model gave for it; a tool the agent
        # doesn't have pairs nothing.
        assert rows["get_order_invoice"]["agent"] == "order_details"
        assert rows["get_order"]["agent"] is None
        assert rows["create_draft"]["agent"] is None

    def test_an_agent_whose_tools_cant_be_read_is_refused(
        self, retail, shopmate, monkeypatch
    ):
        monkeypatch.setattr(harness_fit, "profile_for_version", lambda version: None)

        with pytest.raises(HostedHarnessError) as refused:
            release_fit(retail, shopmate)
        assert refused.value.code == "agent_tools_unavailable"

    def test_the_fit_is_served_for_the_organizations_release(
        self, auth_client, retail, shopmate, fitted
    ):
        response = auth_client.post(
            "/simulate/api/harness-releases/fit/",
            {
                "suite_id": str(retail.id),
                "agent_version_id": str(shopmate.id),
                "pairing": {"get_return_policy": None},
            },
            format="json",
        )

        assert response.status_code == 200, response.content
        body = response.json()
        body = body.get("result", body)
        assert body["total"] == 4
        assert {case["key"] for case in body["unmeasured"]} == {
            "policy-question",
            "reply-by-email",
        }


@pytest.mark.django_db
class TestReleaseConflicts:
    def test_a_conflict_is_flagged_with_the_instruction_quoted(
        self, retail, shopmate, fitted, monkeypatch
    ):
        _model_says(
            monkeypatch,
            {
                "conflicts": [
                    {
                        "rule": "confirm_cancel",
                        "agent": "Cancel straight away when asked; no  need to confirm.",
                    },
                    # Not what the instructions say: the model's words, dropped.
                    {"rule": "verify_first", "agent": "Never check the owner."},
                    {"rule": "no_such_rule", "agent": "You are ShopMate."},
                    # The world enforces this itself: not the agent's to conflict with.
                    {"rule": "orders_owned", "agent": "You are ShopMate."},
                ]
            },
        )

        assert release_conflicts(retail, shopmate) == {
            "checked": True,
            "conflicts": [
                {
                    "rule": "confirm_cancel",
                    "policy": "Confirm before cancelling an order.",
                    "agent": "Cancel straight away when asked; no  need to confirm.",
                }
            ],
        }

    def test_instructions_the_model_couldnt_read_are_not_called_clean(
        self, retail, shopmate, fitted
    ):
        assert release_conflicts(retail, shopmate) == {
            "checked": False,
            "conflicts": [],
        }


def _adaptation(organization, **adaptation):
    payload = _payload()
    payload["metadata"] = {"adaptation": adaptation}
    job, _ = create_hosted_job(
        organization, payload, idempotency_key=f"adapt-{uuid.uuid4()}"
    )
    return job


def _ask(job, prompt):
    """The builder asks `prompt` with yes/no options, as the chat process sends it."""
    conversation, _command, _ = enqueue_message(
        job, content="Bind this agent", client_request_id="bind"
    )
    capability = issue_conversation_capability(
        conversation,
        endpoint_base_url="https://platform.example",
        provider_ref="sandbox-1",
        attempt=None,
        ttl_seconds=600,
    )
    question = str(uuid.uuid4())
    for sequence, (kind, message_id, payload) in enumerate(
        [
            ("turn_started", None, {}),
            (
                "question_requested",
                question,
                {"prompt": prompt, "options": ["yes", "no"]},
            ),
        ],
        start=1,
    ):
        event = {
            "schema_version": "futureagi.harness-conversation-event.v1",
            "event_id": f"ce-{uuid.uuid4()}",
            "conversation_id": str(conversation.id),
            "sequence": sequence,
            "kind": kind,
            "message_id": message_id,
            "stage": "builder",
            "invocation_id": "turn-1",
            "function_call_id": None,
            "emitted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "payload": payload,
        }
        event["digest"] = canonical_digest(event)
        response = APIClient().post(
            f"{BASE}/{conversation.id}/events/",
            {
                "schema_version": "futureagi.harness-conversation-event.v1",
                "acknowledged_through": 1,
                "events": [event],
            },
            format="json",
            **_headers(capability),
        )
        assert response.status_code == 200, response.content
    return conversation, question


def _answered(conversation, question, reply):
    """The chat process says it took `reply` as the answer to `question`."""
    capability = issue_conversation_capability(
        conversation,
        endpoint_base_url="https://platform.example",
        provider_ref="sandbox-1",
        attempt=None,
        ttl_seconds=600,
    )
    event = {
        "schema_version": "futureagi.harness-conversation-event.v1",
        "event_id": f"ce-{uuid.uuid4()}",
        "conversation_id": str(conversation.id),
        "sequence": 3,
        "kind": "question_answered",
        "message_id": question,
        "stage": "builder",
        "invocation_id": "turn-1",
        "function_call_id": None,
        "emitted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "payload": {"reply_message_id": str(reply.id)},
    }
    event["digest"] = canonical_digest(event)
    return APIClient().post(
        f"{BASE}/{conversation.id}/events/",
        {
            "schema_version": "futureagi.harness-conversation-event.v1",
            "acknowledged_through": 2,
            "events": [event],
        },
        format="json",
        **_headers(capability),
    )


APPROVAL = "Use this binding for ShopMate on retail?\n\nMapped tools (1):"


def _reviewed(organization, retail, shopmate, **adaptation):
    return _adaptation(
        organization,
        mode="review",
        suite={"digest": retail.digest},
        agent_version_id=str(shopmate.id),
        **adaptation,
    )


@pytest.mark.django_db(transaction=True)
class TestApproveAndBuild:
    def test_nothing_is_built_until_the_person_approves(
        self, organization, retail, shopmate
    ):
        job = _reviewed(organization, retail, shopmate)
        conversation, question = _ask(job, APPROVAL)

        assert not conversation.messages.filter(reply_to=question).exists()
        conversation.refresh_from_db()
        assert conversation.blocking_input["message_id"] == question
        assert status_for(job) == "awaiting_approval"

    def test_approve_and_build_answers_the_approval(
        self, auth_client, organization, retail, shopmate
    ):
        job = _reviewed(organization, retail, shopmate)
        conversation, question = _ask(job, APPROVAL)

        response = auth_client.post(f"{ADAPTATIONS}/{job.id}/approve/")

        assert response.status_code == 202, response.content
        answer = conversation.messages.get(reply_to=question)
        assert answer.content == "yes"
        job.refresh_from_db()
        assert job.payload["metadata"]["adaptation"]["approved_at"]
        assert status_for(job) == "building"
        # The builder takes the answer mid-turn: the question is settled then.
        assert _answered(conversation, question, answer).status_code == 200
        answer.refresh_from_db()
        assert answer.state == "completed"
        conversation.refresh_from_db()
        assert conversation.blocking_input is None
        assert conversation.state == "responding"

    def test_there_is_nothing_to_approve_while_the_mapping_is_checked(
        self, auth_client, organization, retail, shopmate
    ):
        job = _reviewed(organization, retail, shopmate)
        _ask(job, "Should cancel take a reason?")

        response = auth_client.post(f"{ADAPTATIONS}/{job.id}/approve/")

        assert response.status_code == 409, response.content
        assert not job.conversation.messages.filter(content="yes").exists()


@pytest.mark.django_db(transaction=True)
def test_review_the_design_reads_the_ready_world_and_the_pairing(
    auth_client, organization, retail, shopmate, fitted
):
    retail.personas = [{"name": "Gareth Hughes"}, {"name": "Gareth Hughes"}]
    retail.save(update_fields=["personas"])
    job = _reviewed(organization, retail, shopmate, pairing={"cancel_order": None})

    design = auth_client.get(f"{ADAPTATIONS}/{job.id}/design/")

    assert design.status_code == 200, design.content
    body = design.json()
    body = body.get("result", body)
    assert body["state"] == "checking"
    assert body["agent"] == {"name": "ShopMate", "version": "v1"}
    assert body["world"]["name"] == "retail"
    assert [rule["id"] for rule in body["world"]["rules"]] == [
        "verify_first",
        "confirm_cancel",
    ]
    assert body["world"]["people"] == ["Gareth Hughes"]
    assert len(body["scenarios"]) == 4
    rows = {row["world"]: row["agent"] for row in body["fit"]["rows"]}
    assert rows["cancel_order"] is None
    assert body["check"] is None

    conversation, _question = _ask(job, APPROVAL)
    conversation.refresh_from_db()
    conversation.blocking_input = {
        **conversation.blocking_input,
        "data": {"total": 4, "scorable": 3},
    }
    conversation.save(update_fields=["blocking_input"])
    body = auth_client.get(f"{ADAPTATIONS}/{job.id}/design/").json()
    body = body.get("result", body)
    assert body["state"] == "awaiting_approval"
    assert body["check"] == {"total": 4, "scorable": 3}


@pytest.mark.django_db
def test_an_adaptation_in_review_is_listed_awaiting_approval(
    auth_client, organization, retail, shopmate
):
    review = _reviewed(organization, retail, shopmate)
    run = _adaptation(organization, mode="run")

    response = auth_client.get(
        "/simulate/api/harness-environments/", {"status": "awaiting_approval"}
    )

    assert response.status_code == 200, response.content
    body = response.json()
    body = body.get("result", body)
    ids = [row["id"] for row in body["results"]]
    assert str(review.id) in ids
    assert str(run.id) not in ids
    assert body["status_counts"]["awaiting_approval"] >= 1


@pytest.mark.django_db
def test_run_mode_drafts_without_a_builder_session(
    organization, retail, shopmate, monkeypatch
):
    from simulate.services import harness_releases

    monkeypatch.setattr(
        harness_releases, "_release_archives", lambda *args: ([], "true")
    )
    run = _adaptation(organization, mode="run", environment=[], suite={})
    review = _adaptation(organization, mode="review", environment=[], suite={})

    assert harness_releases.adaptation_launch_for(run).command.endswith(" --auto")
    assert "--auto" not in harness_releases.adaptation_launch_for(review).command


@pytest.mark.django_db
def test_the_builder_is_asked_to_keep_the_reviewed_pairing(organization, monkeypatch):
    from simulate.services import hosted_harness_gateway
    from simulate.tasks import hosted_harness_conversation as runtime

    monkeypatch.setattr(runtime, "schedule_conversation_runtime", lambda *a: None)
    job = _adaptation(
        organization,
        pairing={"get_order": "order_details", "create_draft": None},
    )

    hosted_harness_gateway._ask_harness_to_adapt(
        job, attempt=type("Attempt", (), {"id": "a1"})(), endpoint_base_url="https://x"
    )

    message = job.conversation.messages.get()
    assert message.content.endswith(
        "The person paired the tools (environment tool <- agent tool): "
        "get_order <- order_details. Keep that pairing; an agent tool not in it is "
        "unsupported."
    )


@pytest.mark.django_db
def test_a_sealed_run_mode_adaptation_runs_its_measurable_cases(
    organization, retail, monkeypatch
):
    from simulate.services import harness_releases, hosted_harness
    from simulate.temporal import client

    retail.manifest = {"scenarios": [{"key": "a"}, {"key": "b"}, {"key": "c"}]}
    retail.save(update_fields=["manifest"])
    composition = _adaptation(organization)
    composition.payload["metadata"]["release_composition"] = {
        "suite": {"digest": retail.digest},
        "alignment": {"unscorable": [{"scenario_key": "b"}]},
    }
    composition.save(update_fields=["payload"])
    adaptation = _adaptation(organization, mode="run")
    adaptation.payload["metadata"]["adaptation_result"] = {
        "composition_id": str(composition.id)
    }
    started = {}
    run = type(
        "Run",
        (),
        {
            "id": "run-1",
            "run_test_id": "rt-1",
            "test_execution_id": "te-1",
            "payload": {"retry": _payload()["retry"]},
        },
    )()

    def create(environment, **kwargs):
        started.update(environment=environment.id, **kwargs)
        return run, True

    monkeypatch.setattr(hosted_harness, "create_selected_harness_run", create)
    monkeypatch.setattr(
        client,
        "start_hosted_harness_gateway_workflow",
        lambda job_id, *args: started.setdefault("dispatched", job_id),
    )

    adaptation = harness_releases._run_measurable(adaptation, str(composition.id))

    assert started["environment"] == composition.id
    assert started["scenario_keys"] == ["a", "c"]
    assert started["dispatched"] == "run-1"
    assert adaptation.payload["metadata"]["adaptation_result"]["run"] == {
        "job_id": "run-1",
        "run_test_id": "rt-1",
        "test_execution_id": "te-1",
    }


def test_a_model_call_that_hits_a_gateway_error_is_tried_once_more(monkeypatch):
    import httpx

    replies = [
        httpx.Response(502, json={"error": "upstream"}),
        httpx.Response(
            200,
            json={"choices": [{"message": {"content": '```json\n{"pairs": []}\n```'}}]},
        ),
    ]
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        reply = replies.pop(0)
        reply.request = httpx.Request("POST", url)
        return reply

    monkeypatch.setattr(
        harness_fit, "platform_model_gateway", lambda: ("https://gw", "k")
    )
    monkeypatch.setattr(harness_fit.httpx, "post", post)

    assert harness_fit._ask_model("task", {}) == {"pairs": []}
    assert calls == ["https://gw/v1/chat/completions"] * 2
