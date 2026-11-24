"""How a connected agent fits a ready environment, an adaptation's design awaiting approval, and
one asked to run its measurable cases."""

import uuid
from datetime import UTC, datetime

import pytest
from rest_framework.test import APIClient

from accounts.models.workspace import Workspace
from simulate.models import (
    AgentDefinition,
    AgentVersion,
    HarnessEnvironmentRelease,
    HarnessSuiteRelease,
    HostedHarnessJob,
    HostedHarnessSecret,
)
from simulate.models.agent_definition import ProviderCredentials
from simulate.services import harness_fit
from simulate.services.harness_environment import status_for
from simulate.services.harness_fit import (
    release_conflicts,
    release_fit,
    resolved_pairing,
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
from simulate.services.hosted_sandbox import (
    SandboxFileNotFoundError,
    SandboxNotFoundError,
    SandboxProviderError,
)
from simulate.tests.test_hosted_harness_conversation import BASE, _headers, _payload
from tfc.utils.evaluation_denials import EvaluationDenied

ADAPTATIONS = "/simulate/api/harness-adaptations"
ASK_MODEL = harness_fit._ask_model


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
                (frozenset({"verify_order_owner", "cancel_order"}),),
            ),
            (
                "policy-question",
                "Policy question",
                (frozenset({"get_return_policy"}),),
            ),
            (
                "reply-by-email",
                "Reply by email",
                (frozenset({"create_draft", "get_order"}),),
            ),
            ("refuse-politely", "Refuse politely", (frozenset(),)),
            # Its reference reads a document the agent can't; its other route asks the policy.
            (
                "fee-from-the-policy",
                "Fee from the policy",
                (frozenset({"search_files"}), frozenset({"get_return_policy"})),
            ),
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
        assert fit["total"] == 5
        # Measured by its other route, though its reference needs a tool the agent lacks.
        assert "fee-from-the-policy" not in {case["key"] for case in fit["unmeasured"]}
        assert fit["unmeasured"] == [
            {"key": "reply-by-email", "title": "Reply by email"}
        ]
        # The platform's own actions are never paired with the world's tools.
        assert [one["name"] for one in fit["platform_actions"]] == ["end_call"]

    def test_a_pairing_the_person_changes_is_counted_again(
        self, retail, shopmate, fitted
    ):
        fit = release_fit(retail, shopmate, {"cancel_order": None})

        rows = {row["world"]: row for row in fit["rows"]}
        assert rows["cancel_order"]["kind"] == "missing"
        assert rows["get_order"]["agent"] == "order_details"
        assert {case["key"] for case in fit["unmeasured"]} == {
            "verify-then-cancel",
            "reply-by-email",
        }

    @pytest.mark.parametrize(
        "edits, said",
        [
            ({"nope": "cancel"}, "'nope' is not one of this world's tools"),
            ({"get_order": "get_order"}, "'get_order' is not one of the agent's tools"),
            (
                {"get_order": "cancel", "get_order_invoice": "cancel"},
                "cancel is paired with more than one world tool",
            ),
        ],
        ids=["unknown-world-tool", "not-an-agent-tool", "one-agent-tool-twice"],
    )
    def test_an_edit_that_names_no_real_pair_is_refused(
        self, retail, shopmate, fitted, edits, said
    ):
        with pytest.raises(HostedHarnessError) as refused:
            release_fit(retail, shopmate, edits)

        assert refused.value.code == "pairing_invalid"
        assert refused.value.status_code == 400
        assert said in str(refused.value)

    def test_the_resolved_pairing_is_complete_and_gives_each_agent_tool_once(
        self, retail, shopmate, fitted
    ):
        suggested = resolved_pairing(retail, shopmate)
        assert set(suggested) == {tool["name"] for tool in WORLD}
        assert suggested["cancel_order"] == "cancel"

        # Given to another world tool, the agent's tool leaves the one it was suggested for.
        moved = resolved_pairing(retail, shopmate, {"cancel_return": "cancel"})
        assert moved["cancel_return"] == "cancel" and moved["cancel_order"] is None
        paired = [tool for tool in moved.values() if tool]
        assert len(paired) == len(set(paired))

    def test_a_stored_pairing_is_shown_as_reviewed_and_never_suggested_again(
        self, retail, shopmate, fitted, monkeypatch
    ):
        stored = resolved_pairing(retail, shopmate, {"cancel_order": None})

        def never(*_arguments):
            raise AssertionError("a stored pairing is not suggested again")

        monkeypatch.setattr(harness_fit, "model_pairing", never)
        fit = release_fit(retail, shopmate, stored, frozen=True)

        assert {row["world"]: row["agent"] for row in fit["rows"]} == stored

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
        assert body["total"] == 5
        # With the policy tool unpaired, neither of that case's routes is reached.
        assert {case["key"] for case in body["unmeasured"]} == {
            "policy-question",
            "reply-by-email",
            "fee-from-the-policy",
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

    @pytest.mark.parametrize(
        "ended",
        [
            {"state": "completed"},
            {"state": "failed"},
            {"state": "canceled"},
            {"cancel_requested_at": datetime.now(UTC)},
        ],
        ids=["completed", "failed", "canceled", "cancel-requested"],
    )
    def test_an_adaptation_that_has_ended_cannot_be_approved(
        self, auth_client, organization, retail, shopmate, ended
    ):
        job = _reviewed(organization, retail, shopmate)
        conversation, question = _ask(job, APPROVAL)
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(**ended)

        response = auth_client.post(f"{ADAPTATIONS}/{job.id}/approve/")

        assert response.status_code == 409, response.content
        assert "conversation_closed" in response.content.decode()
        assert not conversation.messages.filter(reply_to=question).exists()
        job.refresh_from_db()
        assert "approved_at" not in job.payload["metadata"]["adaptation"]

    @pytest.mark.parametrize(
        "prompt, said, approved",
        [
            (APPROVAL, " Yes ", True),
            (APPROVAL, "approve", True),
            (APPROVAL, "no, cancel takes a reason", False),
            ("Should cancel take a reason?", "yes", False),
        ],
        ids=["yes", "approve", "not-a-yes", "another-question"],
    )
    def test_a_yes_typed_in_the_chat_is_the_approval_too(
        self, organization, retail, shopmate, prompt, said, approved
    ):
        job = _reviewed(organization, retail, shopmate)
        _conversation, question = _ask(job, prompt)

        enqueue_message(
            job,
            content=said,
            client_request_id="typed",
            kind="user_response",
            reply_to=uuid.UUID(question),
        )

        job.refresh_from_db()
        assert ("approved_at" in job.payload["metadata"]["adaptation"]) is approved

    def test_an_approval_that_is_not_delivered_is_not_recorded(
        self, organization, retail, shopmate, monkeypatch
    ):
        from simulate.services import hosted_harness_conversation
        from simulate.services.harness_releases import approve_adaptation

        job = _reviewed(organization, retail, shopmate)
        conversation, question = _ask(job, APPROVAL)

        def lost(*_arguments, **_options):
            raise RuntimeError("the answer was not stored")

        monkeypatch.setattr(
            hosted_harness_conversation.HostedHarnessConversation, "save", lost
        )
        with pytest.raises(RuntimeError):
            approve_adaptation(job)

        job.refresh_from_db()
        assert "approved_at" not in job.payload["metadata"]["adaptation"]
        assert not conversation.messages.filter(reply_to=question).exists()


@pytest.fixture
def elsewhere(organization, user):
    """Another workspace of the organization, which the caller is not in."""
    return Workspace.objects.create(
        name="Elsewhere", organization=organization, created_by=user
    )


@pytest.mark.django_db
class TestWorkspaceScope:
    def _elsewhere(self, organization, retail, shopmate, workspace):
        job = _reviewed(organization, retail, shopmate)
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
            workspace=workspace
        )
        return job

    def test_an_adaptation_of_another_workspace_is_not_found(
        self, auth_client, organization, retail, shopmate, elsewhere
    ):
        job = self._elsewhere(organization, retail, shopmate, elsewhere)
        conversation, question = _ask(job, APPROVAL)

        design = auth_client.get(f"{ADAPTATIONS}/{job.id}/design/")
        approve = auth_client.post(f"{ADAPTATIONS}/{job.id}/approve/")

        assert design.status_code == 404, design.content
        assert approve.status_code == 404, approve.content
        assert not conversation.messages.filter(reply_to=question).exists()

    def test_an_id_that_is_not_one_is_not_found(self, auth_client):
        assert auth_client.get(f"{ADAPTATIONS}/nope/design/").status_code == 404
        assert auth_client.post(f"{ADAPTATIONS}/nope/approve/").status_code == 404

    def test_an_agent_of_another_workspace_is_neither_fitted_nor_adapted(
        self, auth_client, retail, shopmate, fitted, elsewhere
    ):
        AgentDefinition.no_workspace_objects.filter(
            id=shopmate.agent_definition_id
        ).update(workspace=elsewhere)
        asked = {"suite_id": str(retail.id), "agent_version_id": str(shopmate.id)}

        fit = auth_client.post(
            "/simulate/api/harness-releases/fit/", asked, format="json"
        )
        created = auth_client.post(
            f"{ADAPTATIONS}/",
            {**asked, "name": "Retail", "idempotency_key": f"adapt-{uuid.uuid4()}"},
            format="json",
        )

        assert fit.status_code == 404, fit.content
        assert created.status_code == 404, created.content


@pytest.mark.django_db
class TestAdaptationReplay:
    @pytest.fixture
    def submit(self, auth_client, retail, shopmate, fitted, monkeypatch):
        from simulate.services import harness_provider
        from simulate.temporal import client

        ProviderCredentials.objects.create(
            agent_version=shopmate, provider_type="retell", api_key="test-key"
        )
        started = []
        monkeypatch.setattr(
            client,
            "start_hosted_harness_gateway_workflow",
            lambda job_id, *args: started.append(job_id),
        )
        monkeypatch.setattr(
            harness_provider, "_validate_known_hosted_egress", lambda *args: None
        )

        def send(key="adapt-once", **changed):
            return auth_client.post(
                f"{ADAPTATIONS}/",
                {
                    "suite_id": str(retail.id),
                    "agent_version_id": str(shopmate.id),
                    "name": "Retail",
                    "idempotency_key": key,
                    "pairing": {"cancel_order": None},
                    **changed,
                },
                format="json",
            )

        send.started = started
        retail.personas = [{"name": "Gareth Hughes"}]
        retail.save(update_fields=["personas"])
        return send

    def test_the_same_request_sent_twice_is_one_job(
        self, submit, organization, monkeypatch
    ):
        first = submit()
        assert first.status_code == 202, first.content
        job = HostedHarnessJob.no_workspace_objects.get(idempotency_key="adapt-once")
        assert list(job.payload["agent"]["secret_refs"]) == ["RETELL_API_KEY"]
        assert "test-key" not in str(job.payload)
        secrets = HostedHarnessSecret.no_workspace_objects.filter(
            organization=organization
        )
        assert secrets.count() == 1

        # The suggestion the partial pairing is completed with is another by now, and the
        # agent can't be read at all: neither is the client's request.
        monkeypatch.setattr(harness_fit, "profile_for_version", lambda version: None)
        second = submit()

        assert second.status_code == 202, second.content
        assert (
            HostedHarnessJob.no_workspace_objects.filter(
                idempotency_key="adapt-once"
            ).count()
            == 1
        )
        assert secrets.count() == 1
        assert submit.started == [str(job.id), str(job.id)]

    def test_another_request_under_the_same_key_is_refused(self, submit):
        assert submit().status_code == 202

        assert submit(name="Another").status_code == 409

    def test_a_refused_request_leaves_no_secret(self, submit, organization):
        response = submit(pairing={"nope": "cancel"})

        assert response.status_code == 400, response.content
        assert not HostedHarnessSecret.no_workspace_objects.filter(
            organization=organization
        ).exists()


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
    assert len(body["scenarios"]) == 5
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


def _listed(auth_client, status):
    response = auth_client.get(
        "/simulate/api/harness-environments/", {"status": status}
    )
    assert response.status_code == 200, response.content
    body = response.json()
    body = body.get("result", body)
    return [row["id"] for row in body["results"]], body["status_counts"]


@pytest.mark.django_db(transaction=True)
def test_an_adaptation_is_listed_awaiting_approval_once_the_approval_is_asked(
    auth_client, organization, retail, shopmate
):
    """Until the approval question is open the mapping is still being checked: the list says
    building, as the design says checking, and Approve has nothing to answer."""
    review = _reviewed(organization, retail, shopmate)
    run = _adaptation(organization, mode="run")

    awaiting, counts = _listed(auth_client, "awaiting_approval")
    assert str(review.id) not in awaiting and counts["awaiting_approval"] == 0
    assert status_for(review) == "building"
    assert str(review.id) in _listed(auth_client, "building")[0]

    _ask(review, APPROVAL)

    awaiting, counts = _listed(auth_client, "awaiting_approval")
    assert str(review.id) in awaiting and str(run.id) not in awaiting
    assert counts["awaiting_approval"] == 1
    assert status_for(review) == "awaiting_approval"
    assert str(review.id) not in _listed(auth_client, "building")[0]


@pytest.mark.django_db(transaction=True)
def test_another_question_from_the_builder_is_not_the_approval(
    auth_client, organization, retail, shopmate
):
    review = _reviewed(organization, retail, shopmate)
    _ask(review, "Should cancel take a reason?")

    assert status_for(review) == "building"
    assert str(review.id) not in _listed(auth_client, "awaiting_approval")[0]


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
    from simulate.services import harness_releases, harness_usage, hosted_harness
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
        harness_usage,
        "require_harness_call_usage",
        lambda job: started.update(checked=job.id),
    )
    monkeypatch.setattr(
        client,
        "start_hosted_harness_gateway_workflow",
        lambda job_id, *args: started.setdefault("dispatched", job_id),
    )

    adaptation = harness_releases._run_measurable(adaptation, str(composition.id))

    assert started["checked"] == started["environment"] == composition.id
    assert started["scenario_keys"] == ["a", "c"]
    assert started["dispatched"] == "run-1"
    assert adaptation.payload["metadata"]["adaptation_result"]["run"] == {
        "job_id": "run-1",
        "run_test_id": "rt-1",
        "test_execution_id": "te-1",
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    "refusal, said",
    [
        (
            HostedHarnessError("parallelism_unavailable", "no capacity now"),
            {"code": "parallelism_unavailable", "message": "no capacity now"},
        ),
        (
            EvaluationDenied("the free calls are used", code="FREE_TIER_LIMIT"),
            {"code": "FREE_TIER_LIMIT", "message": "the free calls are used"},
        ),
        (
            RuntimeError("temporal is down at 10.0.0.1"),
            {
                "code": "run_not_started",
                "message": "the run could not be started; run the environment to try again",
            },
        ),
    ],
    ids=["refused", "usage", "unexpected"],
)
def test_a_run_that_cant_start_says_why_on_the_adaptation(
    organization, retail, monkeypatch, refusal, said
):
    from simulate.services import harness_releases, harness_usage, hosted_harness

    retail.manifest = {"scenarios": [{"key": "a"}]}
    retail.save(update_fields=["manifest"])
    composition = _adaptation(organization)
    composition.payload["metadata"]["release_composition"] = {
        "suite": {"digest": retail.digest}
    }
    composition.save(update_fields=["payload"])
    adaptation = _adaptation(organization, mode="run")
    adaptation.payload["metadata"]["adaptation_result"] = {
        "composition_id": str(composition.id)
    }
    created = []

    def refuse(environment):
        raise refusal

    monkeypatch.setattr(harness_usage, "require_harness_call_usage", refuse)
    monkeypatch.setattr(
        hosted_harness,
        "create_selected_harness_run",
        lambda *args, **kwargs: created.append(args),
    )

    harness_releases._run_measurable(adaptation, str(composition.id))

    adaptation.refresh_from_db()
    assert adaptation.payload["metadata"]["adaptation_result"] == {
        "composition_id": str(composition.id),
        "run_failure": said,
    }
    assert created == []


@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": '["pairs"]'}}]},
        ["no", "choices"],
        {"choices": [{"message": {"content": "no JSON"}}]},
    ],
    ids=["no-choices", "no-content", "a-list", "not-an-answer", "not-json"],
)
@pytest.mark.django_db
def test_a_malformed_model_answer_is_answered_by_names(
    retail, shopmate, fitted, monkeypatch, body
):
    import httpx

    monkeypatch.setattr(harness_fit, "_ask_model", ASK_MODEL)
    monkeypatch.setattr(
        harness_fit, "platform_model_gateway", lambda: ("https://gw", "k")
    )
    monkeypatch.setattr(
        harness_fit.httpx,
        "post",
        lambda url, **kwargs: httpx.Response(
            200, json=body, request=httpx.Request("POST", url)
        ),
    )

    assert resolved_pairing(retail, shopmate)["cancel_order"] == "cancel"
    assert release_conflicts(retail, shopmate) == {"checked": False, "conflicts": []}


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


@pytest.mark.django_db
def test_a_run_of_a_case_the_agent_cant_be_scored_on_is_refused(organization):
    from simulate.services import harness_releases

    environment = _adaptation(organization)
    environment.payload["metadata"]["release_composition"] = {
        "binding": {"digest": "sha256:" + "b" * 64},
        "alignment": {
            "unscorable": [{"scenario_key": "b", "reason": "needs create_draft"}]
        },
    }
    environment.save(update_fields=["payload"])

    with pytest.raises(HostedHarnessError) as refused:
        harness_releases.freeze_run_manifest(
            environment, environment, scenario_keys=["a", "b"], trials=1
        )

    assert refused.value.code == "scenario_unscorable"
    assert "b (needs create_draft)" in str(refused.value)


@pytest.mark.django_db
def test_a_release_run_composes_with_its_binding_proof(organization, monkeypatch):
    from simulate.services import harness_releases

    monkeypatch.setattr(
        harness_releases, "_release_archives", lambda *args: ([], "true")
    )
    run = _adaptation(organization)
    composition = {
        "environment": [],
        "suite": {},
        "binding": {"mode": "tool_routed"},
        "binding_proof": {"passed": True},
    }
    run.payload["metadata"] = {
        "release_composition": composition,
        "run_manifest": {"simulator": {}, "runtime": {}},
    }

    launch = harness_releases.release_launch_for(run)

    assert ("/work/binding-proof.json", b'{"passed": true}') in launch.uploads
    assert launch.bundle_command.endswith(" --binding-proof /work/binding-proof.json")
    del composition["binding_proof"]
    assert (
        "--binding-proof" not in harness_releases.release_launch_for(run).bundle_command
    )


# --- the connection given with an adaptation --------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "agent",
    [
        # Another Retell agent than the version's own.
        {"connector": "retell_chat", "config": {"agent_id": "agent_other"}},
        # Another provider.
        {"connector": "vapi", "config": {"assistant_id": "agent_shopmate"}},
        {"connector": "retell_chat", "config": {}},
    ],
    ids=["another-target", "another-provider", "no-target"],
)
def test_a_connection_to_another_provider_agent_is_refused(shopmate, agent):
    from simulate.services.harness_releases import check_agent_connection

    with pytest.raises(HostedHarnessError) as refused:
        check_agent_connection(shopmate, agent)

    assert refused.value.code == "agent_connection_mismatch"
    assert refused.value.status_code == 409


@pytest.mark.django_db
def test_a_connection_to_the_versions_own_provider_agent_is_taken(shopmate):
    from simulate.services.harness_releases import check_agent_connection

    for connector in ("retell", "retell_chat"):
        check_agent_connection(
            shopmate, {"connector": connector, "config": {"agent_id": "agent_shopmate"}}
        )


@pytest.mark.django_db
def test_an_adaptation_for_one_version_through_another_agents_connection_is_refused(
    auth_client, organization, retail, shopmate, fitted
):
    response = auth_client.post(
        f"{ADAPTATIONS}/",
        {
            "suite_id": str(retail.id),
            "agent_version_id": str(shopmate.id),
            "agent": {
                "connector": "retell_chat",
                "mode": "provider_import",
                "config": {"agent_id": "agent_other"},
            },
            "name": "Retail",
            "idempotency_key": f"adapt-{uuid.uuid4()}",
        },
        format="json",
    )

    assert response.status_code == 409, response.content
    assert "agent_connection_mismatch" in response.content.decode()


# --- the proof a tool-routed composition is accepted with -------------------------------------

SHA = "sha256:" + "b" * 64
BOUND_AGENT = {"agent_version_id": "v1", "connector": "retell_chat"}


def _routed_binding():
    document = {
        "schema_version": "futureagi.agent-binding.v1",
        "agent": {**BOUND_AGENT, "provider_target_id": "t1", "provider_digest": SHA},
        "environment": {"digest": "sha256:" + "e" * 64},
        "mode": "tool_routed",
        "agent_tools": [],
        "tools": [],
        "fixtures": {},
        "aliases": {},
        "rendering": {},
        "prompt_changes": [],
    }
    return {**document, "digest": canonical_digest(document)}


def _proof(binding, **statuses):
    return {
        "schema_version": "futureagi.binding-proof.v1",
        "binding": {"digest": binding["digest"]},
        "suite": {"digest": "sha256:" + "s" * 64},
        "environment": binding["environment"],
        "passed": True,
        "scenarios": {
            key: {"status": status, "reason": "", "adversarial": {}}
            for key, status in statuses.items()
        },
    }


def _accepted(binding, proof, keys=("a", "b")):
    from simulate.services.harness_releases import _checked_binding

    return _checked_binding(
        binding,
        agent=BOUND_AGENT,
        target_id="t1",
        environment_digest=binding["environment"]["digest"],
        suite_digest="sha256:" + "s" * 64,
        proof=proof,
        scenario_keys=set(keys),
    )


def test_a_proof_that_covers_exactly_the_suite_is_accepted():
    binding = _routed_binding()

    assert _accepted(binding, _proof(binding, a="proved", b="unscorable")) == binding


@pytest.mark.parametrize(
    "statuses",
    [
        {"a": "proved"},
        {"a": "proved", "b": "unscorable", "c": "proved"},
        {"a": "proved", "b": "failed"},
        {"a": "unscorable", "b": "unscorable"},
    ],
    ids=[
        "a-case-left-out",
        "a-case-the-suite-lacks",
        "a-failed-case",
        "nothing-proved",
    ],
)
def test_a_proof_that_is_not_of_exactly_this_suite_is_refused(statuses):
    binding = _routed_binding()

    with pytest.raises(HostedHarnessError) as refused:
        _accepted(binding, _proof(binding, **statuses))

    assert refused.value.code == "binding_unproved"


def test_a_run_manifest_freezes_the_digest_of_the_proof_the_platform_holds():
    from simulate.services.harness_releases import _frozen_proof, proof_digest

    binding = _routed_binding()
    proof = _proof(binding, a="proved", b="unscorable")
    composition = {"binding": binding, "binding_proof": proof}

    frozen = _frozen_proof(composition)

    assert frozen == {"binding_proof": {"digest": proof_digest(proof)}}
    # Any edit to a status is another proof.
    edited = _proof(binding, a="proved", b="proved")
    assert _frozen_proof({"binding": binding, "binding_proof": edited}) != frozen
    # A canonical composition has no proof and freezes none.
    assert _frozen_proof({"binding": {**binding, "mode": "canonical"}}) == {}


def _left_in_the_output(tmp_path, binding, proof, monkeypatch):
    """An adaptation's downloaded output, whose result names the proof beside it: the two files
    agree with each other, as any pair a session wrote would."""
    import json

    from simulate.services import harness_releases

    result = {
        "schema_version": harness_releases.ADAPTATION_RESULT_SCHEMA,
        "status": "completed",
        "suite": {"digest": "sha256:" + "s" * 64, "derived": False},
        "binding_proof": {"digest": harness_releases.proof_digest(proof)},
        # The session's own word on what can't be scored; the verified proof's is taken.
        "alignment": {"mapped": [], "unscorable": [{"scenario_key": "forged"}]},
    }
    for name, body in (
        ("result.json", result),
        ("binding.json", binding),
        ("binding-proof.json", proof),
    ):
        (tmp_path / name).write_text(json.dumps(body))
    suite = type("Suite", (), {"digest": "sha256:" + "s" * 64, "name": "retail"})()
    job = type(
        "Job",
        (),
        {
            "id": "job-1",
            "workspace": None,
            "organization": object(),
            "payload": {
                "agent": {},
                "metadata": {
                    "adaptation": {
                        "suite": {"digest": suite.digest},
                        "agent_version_id": "v1",
                    }
                },
            },
        },
    )()
    monkeypatch.setattr(harness_releases, "_visible", lambda *args: suite)
    monkeypatch.setattr(
        harness_releases.AgentVersion.no_workspace_objects,
        "filter",
        lambda **kwargs: type("Found", (), {"first": lambda self: object()})(),
    )
    composed = {}

    def compose(*args, **kwargs):
        composed.update(kwargs)
        return type("Composition", (), {"id": "composition-1"})()

    monkeypatch.setattr(harness_releases, "create_composition", compose)
    return job, composed


def _verified(binding, proof):
    from simulate.services import harness_releases

    return {
        "schema_version": harness_releases.ADAPTATION_VERIFIED_SCHEMA,
        "binding": {"digest": binding["digest"], "mode": binding["mode"]},
        "binding_proof": {"digest": harness_releases.proof_digest(proof)},
    }


def test_the_proof_the_control_process_verified_is_accepted(tmp_path, monkeypatch):
    from simulate.services import harness_releases

    binding = _routed_binding()
    proof = _proof(binding, a="proved", b="unscorable")
    job, composed = _left_in_the_output(tmp_path, binding, proof, monkeypatch)

    result, failure = harness_releases._adapt(job, tmp_path, _verified(binding, proof))

    assert failure is None and result["composition_id"] == "composition-1"
    assert composed["binding_proof"] == proof
    assert composed["alignment"] == {
        "mapped": [],
        "unscorable": [{"scenario_key": "b", "reason": ""}],
    }


def test_a_matching_pair_the_control_process_did_not_verify_is_refused(
    tmp_path, monkeypatch
):
    """The reviewer's case: after the control process proved the binding, the output's proof and
    the digest in its result are both replaced, so they agree. The record read apart from the
    output still names the proof that was verified."""
    from simulate.services import harness_releases

    binding = _routed_binding()
    verified = _verified(binding, _proof(binding, a="proved", b="unscorable"))
    forged = _proof(binding, a="proved", b="proved")
    job, composed = _left_in_the_output(tmp_path, binding, forged, monkeypatch)

    with pytest.raises(HostedHarnessError) as refused:
        harness_releases._adapt(job, tmp_path, verified)

    assert refused.value.code == "binding_unproved"
    assert "not the proof the control process verified" in str(refused.value)
    assert composed == {}


@pytest.mark.parametrize(
    "verified",
    [
        None,
        {"schema_version": "something-else", "binding_proof": {"digest": "x"}},
        {"schema_version": "futureagi.adaptation-verified.v1", "binding": None},
    ],
    ids=["no-record", "another-schema", "nothing-recorded"],
)
def test_a_routed_binding_with_no_verified_record_is_refused(
    tmp_path, monkeypatch, verified
):
    from simulate.services import harness_releases

    binding = _routed_binding()
    proof = _proof(binding, a="proved", b="unscorable")
    job, composed = _left_in_the_output(tmp_path, binding, proof, monkeypatch)

    with pytest.raises(HostedHarnessError) as refused:
        harness_releases._adapt(job, tmp_path, verified)

    assert refused.value.code == "binding_unproved"
    assert "recorded no verified proof" in str(refused.value)
    assert composed == {}


def test_another_binding_than_the_one_verified_is_refused(tmp_path, monkeypatch):
    from simulate.services import harness_releases

    binding = _routed_binding()
    proof = _proof(binding, a="proved", b="unscorable")
    job, composed = _left_in_the_output(tmp_path, binding, proof, monkeypatch)
    verified = _verified(binding, proof)
    verified["binding"]["digest"] = "sha256:" + "0" * 64

    with pytest.raises(HostedHarnessError) as refused:
        harness_releases._adapt(job, tmp_path, verified)

    assert "not the binding the control process verified" in str(refused.value)
    assert composed == {}


@pytest.mark.parametrize(
    "said", [None, "canonical", "tool_routed"], ids=["nothing", "canonical", "routed"]
)
def test_a_canonical_binding_is_held_to_the_record_too(tmp_path, monkeypatch, said):
    from simulate.services import harness_releases

    document = {**_routed_binding(), "mode": "canonical"}
    del document["digest"]
    binding = {**document, "digest": canonical_digest(document)}
    job, composed = _left_in_the_output(tmp_path, binding, {}, monkeypatch)
    verified = said and {
        "schema_version": harness_releases.ADAPTATION_VERIFIED_SCHEMA,
        "binding": {"digest": binding["digest"], "mode": said},
        "binding_proof": None,
    }

    if said == "canonical":
        result, failure = harness_releases._adapt(job, tmp_path, verified)
        assert failure is None and result["composition_id"] == "composition-1"
        assert composed["binding_proof"] is None
        return
    with pytest.raises(HostedHarnessError) as refused:
        harness_releases._adapt(job, tmp_path, verified)
    assert refused.value.code == "binding_unproved"
    assert composed == {}


def test_the_verified_record_is_read_from_its_own_path_never_the_output():
    import json

    from simulate.services import hosted_harness_gateway
    from simulate.services.harness_releases import (
        ADAPTATION_OUTPUT,
        ADAPTATION_VERIFIED,
    )

    record = {"schema_version": "futureagi.adaptation-verified.v1"}
    asked = []

    class Files:
        def download_file(self, path, timeout):
            asked.append(path)
            if path != ADAPTATION_VERIFIED:
                raise FileNotFoundError(path)
            return json.dumps(record).encode()

    sandbox = type("Sandbox", (), {"fs": Files()})()

    assert hosted_harness_gateway._read_verified(sandbox, ADAPTATION_VERIFIED) == record
    assert asked == [ADAPTATION_VERIFIED]
    assert not ADAPTATION_VERIFIED.startswith(ADAPTATION_OUTPUT.rsplit("/", 1)[0] + "/")
    # A sandbox that left none, or something unreadable, verified nothing.
    assert hosted_harness_gateway._read_verified(sandbox, "/work/missing.json") is None


@pytest.mark.parametrize(
    "found",
    [
        SandboxFileNotFoundError("no file"),
        SandboxNotFoundError("404"),
        b"{not json",
        b"[]",
    ],
    ids=["no-file", "not-found", "not-json", "not-a-record"],
)
def test_a_record_the_sandbox_does_not_hold_verifies_nothing(found):
    from simulate.services import hosted_harness_gateway

    assert hosted_harness_gateway._read_verified(_holding(found), "/work/r") is None


def test_a_record_the_provider_could_not_read_is_raised_to_be_read_again():
    from simulate.services import hosted_harness_gateway

    with pytest.raises(SandboxProviderError):
        hosted_harness_gateway._read_verified(
            _holding(SandboxProviderError("gateway timeout", status_code=504)),
            "/work/r",
        )


def _holding(found):
    class Files:
        def download_file(self, path, timeout):
            if isinstance(found, Exception):
                raise found
            return found

    return type("Sandbox", (), {"fs": Files()})()


def test_the_fit_waits_one_short_budget_for_the_model_retry_included(monkeypatch):
    """Two tries never add up to more than the budget: the second gets what the first left, and
    with nothing left the model is not asked again."""
    import httpx

    clock = iter([100.0, 100.0, 109.0, 111.5])
    monkeypatch.setattr(harness_fit.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        harness_fit, "platform_model_gateway", lambda: ("https://gw", "k")
    )
    timeouts = []

    def post(url, **kwargs):
        timeouts.append(kwargs["timeout"])
        raise httpx.ConnectError("dropped")

    monkeypatch.setattr(harness_fit.httpx, "post", post)

    with pytest.raises(httpx.TransportError):
        harness_fit._ask_model("task", {})

    assert timeouts == [12.0, 3.0]
    assert sum(timeouts) <= harness_fit._MODEL_BUDGET_SECONDS + 3

    spent = iter([100.0, 100.0, 111.5])
    monkeypatch.setattr(harness_fit.time, "monotonic", lambda: next(spent))
    timeouts.clear()
    with pytest.raises(httpx.TimeoutException):
        harness_fit._ask_model("task", {})
    assert timeouts == [12.0]
