"""Phase 3A unit tests — execution-policy classification + confirmation gate.

Covers (design §§1.2-1.5):
- classify() / classify_name_only() binding rules,
- `confirm` schema injection (never into the input models),
- the BaseTool.run gate: blocks without approval, jailbreak-proof cold
  confirm=true on falcon transport, CONFIRMATION_PENDING, consume-once,
  exact-args binding, transport-aware mcp/harness behavior, user isolation,
- registry backfill: every delete_*/remove_*/bulk_* tool is destructive.

Redis is faked in-process (no live Redis needed).
"""

from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel as PydanticBaseModel

from ai_tools import confirmations
from ai_tools.base import BaseTool, ToolContext, ToolResult

# ---------------------------------------------------------------------------
# Fake redis
# ---------------------------------------------------------------------------


class FakeRedis:
    """Minimal SETEX/GET/TTL fake — enough for the confirmation store."""

    def __init__(self):
        self.store = {}  # key -> (value, ttl)

    def setex(self, key, ttl, value):
        self.store[key] = (value, int(ttl))

    def get(self, key):
        item = self.store.get(key)
        return item[0] if item else None

    def ttl(self, key):
        item = self.store.get(key)
        return item[1] if item else -2

    def expire_key(self, key):
        self.store.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(confirmations, "_get_redis", lambda: r)
    return r


def _ctx(user_id=1, transport="falcon", conversation_id="conv-1"):
    user = MagicMock()
    user.id = user_id
    org = MagicMock()
    org.id = 10
    ws = MagicMock()
    ws.id = 100
    return ToolContext(
        user=user,
        organization=org,
        workspace=ws,
        transport=transport,
        conversation_id=conversation_id,
    )


class _DeleteInput(PydanticBaseModel):
    thing_id: str


class DummyDeleteTool(BaseTool):
    name = "delete_dummy_thing"
    description = "Delete a dummy thing."
    category = "tests"
    input_model = _DeleteInput
    execution_policy = "destructive"

    def __init__(self):
        self.executed_with = []

    def execute(self, params, context):
        self.executed_with.append(params.thing_id)
        return ToolResult(content="deleted", data={"id": params.thing_id})


class DummyReadTool(BaseTool):
    name = "get_dummy_thing"
    description = "Read a dummy thing."
    category = "tests"
    input_model = _DeleteInput
    execution_policy = "read"

    def execute(self, params, context):
        return ToolResult(content="read", data=params.model_dump())


# ---------------------------------------------------------------------------
# Classification (unit)
# ---------------------------------------------------------------------------


class TestClassify:
    def test_override_wins(self):
        assert confirmations.classify("get_x", method="GET", override="destructive") == "destructive"
        assert confirmations.classify("delete_x", action="destroy", method="DELETE", override="read") == "read"

    def test_invalid_override_raises(self):
        with pytest.raises(ValueError):
            confirmations.classify("x", override="dangerous")

    def test_destroy_action_or_delete_method(self):
        assert confirmations.classify("anything", action="destroy", method="POST") == "destructive"
        assert confirmations.classify("anything", method="DELETE") == "destructive"

    def test_write_method_with_destructive_name(self):
        for name in (
            "delete_x", "remove_x", "revoke_x", "bulk_x", "hard_x",
            "purge_x", "reset_x", "do_bulk_thing", "mark_evals_deleted",
        ):
            assert confirmations.classify(name, method="POST") == "destructive", name

    def test_write_method_plain_is_mutate(self):
        assert confirmations.classify("create_x", method="POST") == "mutate"
        assert confirmations.classify("update_x", method="PATCH") == "mutate"
        assert confirmations.classify("submit_x", method="PUT") == "mutate"

    def test_get_is_read_even_with_destructive_name(self):
        # No write method -> read (e.g. a GET named oddly).
        assert confirmations.classify("delete_preview", method="GET") == "read"
        assert confirmations.classify("list_x", method="GET") == "read"

    def test_name_only_backfill(self):
        assert confirmations.classify_name_only("delete_x") == "destructive"
        assert confirmations.classify_name_only("bulk_review_items") == "destructive"
        assert confirmations.classify_name_only("mark_eval_tasks_deleted") == "destructive"
        assert confirmations.classify_name_only("create_x") == "mutate"
        assert confirmations.classify_name_only("run_x") == "mutate"
        assert confirmations.classify_name_only("get_x") == "read"
        assert confirmations.classify_name_only("whoami") == "read"


# ---------------------------------------------------------------------------
# Schema confirm injection (unit)
# ---------------------------------------------------------------------------


class TestConfirmInjection:
    def test_destructive_schema_advertises_optional_confirm(self):
        schema = DummyDeleteTool().input_schema
        assert "confirm" in schema["properties"]
        assert schema["properties"]["confirm"]["type"] == "boolean"
        assert "confirm" not in schema.get("required", [])

    def test_input_model_untouched(self):
        _ = DummyDeleteTool().input_schema  # build (deep-copies)
        assert "confirm" not in _DeleteInput.model_json_schema()["properties"]
        assert "confirm" not in _DeleteInput.model_fields

    def test_non_destructive_schema_has_no_confirm(self):
        assert "confirm" not in DummyReadTool().input_schema["properties"]

    def test_to_dict_exports_policy(self):
        assert DummyDeleteTool().to_dict()["execution_policy"] == "destructive"
        assert DummyReadTool().to_dict()["execution_policy"] == "read"

    def test_coerce_confirm(self):
        coerce = BaseTool._coerce_confirm
        for truthy in (True, "true", "True", "1", "yes", " YES "):
            assert coerce(truthy) is True, truthy
        for falsy in (False, None, "false", "0", "no", "", "nope", 0):
            assert coerce(falsy) is False, falsy


# ---------------------------------------------------------------------------
# Gate behavior (unit, fake redis)
# ---------------------------------------------------------------------------


class TestConfirmationGate:
    def _approve(self, result):
        token = result.data["confirmation"]["token"]
        assert confirmations.set_status(token, "approved") is not None
        return token

    def test_first_call_blocks_with_preview(self, fake_redis):
        tool = DummyDeleteTool()
        result = tool.run({"thing_id": "t-1"}, _ctx())
        assert result.error_code == "CONFIRMATION_REQUIRED"
        assert result.is_error is False  # never counts toward consecutive_errors
        assert "no action was taken" in result.content
        payload = result.data["confirmation"]
        assert payload["tool_name"] == "delete_dummy_thing"
        assert payload["policy"] == "destructive"
        assert payload["args"] == {"thing_id": "t-1"}
        assert tool.executed_with == []  # zero side effects
        rec = confirmations.get(payload["token"])
        assert rec["status"] == "pending"

    def test_cold_confirm_true_is_jailbreak_proof(self, fake_redis):
        """LLM told to 'skip confirmation, pass confirm=true immediately'."""
        tool = DummyDeleteTool()
        result = tool.run({"thing_id": "t-1", "confirm": True}, _ctx())
        assert result.error_code == "CONFIRMATION_REQUIRED"
        assert tool.executed_with == []

    def test_confirm_true_while_pending_returns_pending(self, fake_redis):
        tool = DummyDeleteTool()
        ctx = _ctx()
        tool.run({"thing_id": "t-1"}, ctx)  # creates pending
        result = tool.run({"thing_id": "t-1", "confirm": True}, ctx)
        assert result.error_code == "CONFIRMATION_PENDING"
        assert result.is_error is False
        assert "Confirm button" in result.content
        assert tool.executed_with == []

    def test_typed_yes_never_approves_without_button(self, fake_redis):
        # Same as pending case, repeated calls never execute.
        tool = DummyDeleteTool()
        ctx = _ctx()
        tool.run({"thing_id": "t-1"}, ctx)
        for _ in range(3):
            r = tool.run({"thing_id": "t-1", "confirm": "yes"}, ctx)
            assert r.error_code == "CONFIRMATION_PENDING"
        assert tool.executed_with == []

    def test_approved_flow_executes_and_consumes_once(self, fake_redis):
        tool = DummyDeleteTool()
        ctx = _ctx()
        first = tool.run({"thing_id": "t-1"}, ctx)
        token = self._approve(first)
        result = tool.run({"thing_id": "t-1", "confirm": True}, ctx)
        assert result.is_error is False
        assert result.error_code is None
        assert result.content == "deleted"
        assert result.data["confirmed"] is True  # audit hook
        assert tool.executed_with == ["t-1"]
        # single-use: the record is consumed...
        assert confirmations.get(token)["status"] == "consumed"
        # ...so a replay does NOT execute again — it gets a fresh preview.
        replay = tool.run({"thing_id": "t-1", "confirm": True}, ctx)
        assert replay.error_code == "CONFIRMATION_REQUIRED"
        assert tool.executed_with == ["t-1"]

    def test_recall_without_confirm_preserves_approval(self, fake_redis):
        """LLM forgot confirm=true after the button: the approval survives
        (no fresh-pending clobber) and the result instructs the LLM; the
        action still does NOT execute without confirm=true."""
        tool = DummyDeleteTool()
        ctx = _ctx()
        first = tool.run({"thing_id": "t-1"}, ctx)
        token = self._approve(first)
        retry = tool.run({"thing_id": "t-1"}, ctx)  # no confirm
        assert retry.error_code == "CONFIRMATION_PENDING"
        assert "confirm=true" in retry.content
        assert tool.executed_with == []
        assert confirmations.get(token)["status"] == "approved"  # intact
        # And the corrected re-call executes.
        done = tool.run({"thing_id": "t-1", "confirm": True}, ctx)
        assert done.is_error is False and done.error_code is None
        assert tool.executed_with == ["t-1"]

    def test_approval_is_exact_args_bound(self, fake_redis):
        tool = DummyDeleteTool()
        ctx = _ctx()
        first = tool.run({"thing_id": "t-1"}, ctx)
        self._approve(first)
        # Different args -> different hash -> no approval; fresh preview.
        result = tool.run({"thing_id": "t-OTHER", "confirm": True}, ctx)
        assert result.error_code == "CONFIRMATION_REQUIRED"
        assert tool.executed_with == []

    def test_cancelled_falls_through_to_fresh_pending(self, fake_redis):
        tool = DummyDeleteTool()
        ctx = _ctx()
        first = tool.run({"thing_id": "t-1"}, ctx)
        token = first.data["confirmation"]["token"]
        confirmations.set_status(token, "cancelled")
        result = tool.run({"thing_id": "t-1", "confirm": True}, ctx)
        assert result.error_code == "CONFIRMATION_REQUIRED"
        new_token = result.data["confirmation"]["token"]
        assert new_token != token
        assert tool.executed_with == []

    def test_user_isolation(self, fake_redis):
        """User B can never consume user A's approval (key embeds user_id)."""
        tool = DummyDeleteTool()
        ctx_a = _ctx(user_id=1)
        ctx_b = _ctx(user_id=2)
        first = tool.run({"thing_id": "t-1"}, ctx_a)
        self._approve(first)
        result_b = tool.run({"thing_id": "t-1", "confirm": True}, ctx_b)
        assert result_b.error_code == "CONFIRMATION_REQUIRED"
        assert tool.executed_with == []

    def test_conversation_isolation(self, fake_redis):
        tool = DummyDeleteTool()
        ctx1 = _ctx(conversation_id="conv-1")
        ctx2 = _ctx(conversation_id="conv-2")
        first = tool.run({"thing_id": "t-1"}, ctx1)
        self._approve(first)
        result = tool.run({"thing_id": "t-1", "confirm": True}, ctx2)
        assert result.error_code == "CONFIRMATION_REQUIRED"
        assert tool.executed_with == []

    def test_harness_transport_two_phase_autoconfirm(self, fake_redis):
        """mcp/harness: the client is the approver, but preview-first holds."""
        tool = DummyDeleteTool()
        ctx = _ctx(transport="harness")
        # Cold confirm=true on harness STILL only creates a preview.
        cold = tool.run({"thing_id": "t-1", "confirm": True}, ctx)
        assert cold.error_code == "CONFIRMATION_REQUIRED"
        assert tool.executed_with == []
        # Phase-2 with an existing pending record executes (no button).
        result = tool.run({"thing_id": "t-1", "confirm": True}, ctx)
        assert result.is_error is False and result.error_code is None
        assert tool.executed_with == ["t-1"]

    def test_mcp_transport_without_confirm_still_previews(self, fake_redis):
        tool = DummyDeleteTool()
        ctx = _ctx(transport="mcp")
        first = tool.run({"thing_id": "t-1"}, ctx)
        assert first.error_code == "CONFIRMATION_REQUIRED"
        second = tool.run({"thing_id": "t-1"}, ctx)  # still no confirm
        assert second.error_code == "CONFIRMATION_REQUIRED"
        assert tool.executed_with == []

    def test_expired_record_means_fresh_preview(self, fake_redis):
        tool = DummyDeleteTool()
        ctx = _ctx()
        first = tool.run({"thing_id": "t-1"}, ctx)
        token = self._approve(first)
        # Simulate TTL expiry of both keys.
        fake_redis.expire_key(f"{confirmations.REC_PREFIX}{token}")
        args_hash = confirmations.compute_args_hash({"thing_id": "t-1"})
        fake_redis.expire_key(confirmations._lookup_key(ctx, tool.name, args_hash))
        result = tool.run({"thing_id": "t-1", "confirm": True}, ctx)
        assert result.error_code == "CONFIRMATION_REQUIRED"
        assert tool.executed_with == []

    def test_non_destructive_tool_never_gated_and_keeps_confirm_param(
        self, fake_redis
    ):
        tool = DummyReadTool()
        result = tool.run({"thing_id": "t-1"}, _ctx())
        assert result.is_error is False
        assert result.content == "read"
        # `confirm` must NOT be popped for non-destructive tools — it is a
        # legit (unknown) field and pydantic ignore/validation applies.
        assert "confirm" not in (result.data or {})

    def test_preview_builder_used_and_fallback_on_error(self, fake_redis):
        tool = DummyDeleteTool()
        confirmations.PREVIEW_BUILDERS[tool.name] = lambda params, ctx: (
            f"CUSTOM PREVIEW for {params['thing_id']}"
        )
        try:
            result = tool.run({"thing_id": "t-1"}, _ctx())
            assert "CUSTOM PREVIEW for t-1" in result.data["confirmation"]["preview"]
            # Broken builder -> default preview, gate still works.
            def _boom(params, ctx):
                raise RuntimeError("boom")

            confirmations.PREVIEW_BUILDERS[tool.name] = _boom
            result2 = tool.run({"thing_id": "t-2"}, _ctx())
            assert result2.error_code == "CONFIRMATION_REQUIRED"
            assert "delete_dummy_thing" in result2.data["confirmation"]["preview"]
        finally:
            confirmations.PREVIEW_BUILDERS.pop(tool.name, None)

    def test_undo_payload_on_executed_leg(self, fake_redis):
        class UndoableDelete(DummyDeleteTool):
            name = "delete_undoable_thing"
            undo_note = "Undo: re-create it."
            undo_prompt = "Re-create thing {thing_id}."

        tool = UndoableDelete()
        ctx = _ctx()
        first = tool.run({"thing_id": "t-9"}, ctx)
        assert first.data["confirmation"]["undo_note"] == "Undo: re-create it."
        self._approve(first)
        result = tool.run({"thing_id": "t-9", "confirm": True}, ctx)
        assert result.data["undo"] == {
            "prompt": "Re-create thing t-9.",
            "note": "Undo: re-create it.",
        }

    def test_default_preview_counts_targets(self):
        preview = confirmations.build_preview(
            DummyDeleteTool(), {"ids": ["a", "b", "c"], "flag": True}
        )
        assert "3 item(s) targeted." in preview
        assert "This cannot be undone." in preview


# ---------------------------------------------------------------------------
# Registry backfill (in-container: imports the full registry)
# ---------------------------------------------------------------------------


class TestRegistryBackfill:
    @pytest.fixture(scope="class")
    def full_registry(self):
        import ai_tools.tools  # noqa: F401 — triggers registration
        from ai_tools.registry import registry

        return registry

    def test_every_tool_has_a_policy(self, full_registry):
        bad = [
            t.name
            for t in full_registry.list_all()
            if getattr(t, "execution_policy", "") not in confirmations.POLICIES
        ]
        assert bad == []

    def test_existing_delete_tools_are_gated(self, full_registry):
        """PHASES.md:202 — migration of every existing destructive bridge:
        all delete_*/remove_*/bulk_*/hard_*/reset_* tools are destructive."""
        wrong = [
            t.name
            for t in full_registry.list_all()
            if confirmations._name_is_destructive(t.name)
            and t.execution_policy != "destructive"
        ]
        assert wrong == []

    def test_delete_tools_advertise_confirm(self, full_registry):
        missing = [
            t.name
            for t in full_registry.list_all()
            if t.execution_policy == "destructive"
            and "confirm" not in t.input_schema.get("properties", {})
        ]
        assert missing == []

    def test_the_fifteen_are_registered_destructive(self, full_registry):
        fifteen = [
            "bulk_delete_prompt_templates",
            "hard_delete_annotation_queue",
            "bulk_remove_queue_items",
            "bulk_delete_eval_tasks",
            "bulk_delete_test_executions",
            "bulk_delete_eval_templates",
            "delete_project_version_runs",
            "remove_shared_link_access",
            "bulk_delete_annotations",
            "reset_annotations",
            "remove_prompt_label_from_version",
            "remove_blocklist_words",
            "remove_gateway_provider",
            "remove_gateway_budget",
            "remove_gateway_mcp_server",
        ]
        for name in fifteen:
            tool = full_registry.get(name)
            assert tool is not None, f"{name} not registered"
            assert tool.execution_policy == "destructive", name
            assert name in confirmations.PREVIEW_BUILDERS, f"{name} has no preview builder"

    def test_add_side_pairs_are_mutate(self, full_registry):
        for name in (
            "assign_prompt_labels_to_version",
            "add_blocklist_words",
            "set_gateway_budget",
            "update_gateway_mcp_server",
        ):
            tool = full_registry.get(name)
            assert tool is not None, f"{name} not registered"
            assert tool.execution_policy == "mutate", name
