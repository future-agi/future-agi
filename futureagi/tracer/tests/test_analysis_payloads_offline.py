"""Original JSON -> actual tool run -> AgentLoop -> worker; socket blocked."""

import runpy
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID

from pydantic import BaseModel, Field

HERE = Path(__file__).parent
b = runpy.run_path(str(HERE / "test_analysis_tool_branch_cases_offline.py"))
f = b["f"]
ROOT = f["ROOT"]
base = runpy.run_path(str(ROOT / "ai_tools/base.py"))
MODELS = {
    "explore_trace": "ExploreTraceInput",
    "read_trace_span": "ReadTraceSpanInput",
    "submit_trace_finding": "SubmitTraceFindingInput",
    "submit_trace_scores": "SubmitTraceScoresInput",
}


def model_for(name):
    ns = f["p"]["f"]["compile_definitions"](
        ROOT / f"ai_tools/tools/tracing/{name}.py",
        {"PydanticBaseModel": BaseModel, "Field": Field, "UUID": UUID},
        names={MODELS[name]},
    )
    model = ns[MODELS[name]]
    model.model_rebuild(_types_namespace=ns)
    return model


class OriginalArguments(b["BranchCases"]):
    def raw(
        self, name, changes, *, success=False, foreground=False, forbid_cleanup=False
    ):
        model = model_for(name)

        def prepare(tool, ns, query, span, modules):
            ns["BaseTool"] = base["BaseTool"]
            ns["ToolContext"] = base["ToolContext"]
            if name.startswith("submit_"):
                selected, params = self.submission(name, "new")(
                    tool, ns, query, span, modules
                )
            elif name == "read_trace_span":
                span.status, span.model, span.parent_span_id = "OK", "offline", ""
                span.latency_ms, span.total_tokens = 3, 7
                span.metadata, span.span_events = {}, []
                selected, params = b["select_tool"](
                    name, {"span_id": "span", "exact": False}, ns
                )
            else:
                selected, params = tool, SimpleNamespace(trace_id="trace")
            raw = {k: v for k, v in vars(params).items() if k in model.model_fields}
            raw["trace_id"] = "00000000-0000-0000-0000-000000000001"
            wire_data = raw.copy() if foreground else raw
            raw.update(changes)
            selected._offline_wire = lambda: __import__("json").dumps(wire_data)
            selected._offline_foreground = foreground
            selected.input_model = model
            # The explore fixture originally inherits object; load its real run
            # definition if present, otherwise use actual BaseTool.run at RED.
            selected._clean_params = base["BaseTool"]._clean_params
            if forbid_cleanup:
                selected._clean_params = Mock(
                    side_effect=AssertionError("background cleanup forbidden")
                )
                model.model_validate = Mock(
                    side_effect=AssertionError("background Pydantic coercion forbidden")
                )
            if not isinstance(selected, base["BaseTool"]):
                selected.input_schema = model.model_json_schema()
            execute = selected.execute
            dispatch = getattr(type(selected), "run", base["BaseTool"].run)
            if name.startswith("submit_"):
                closure = dict(
                    zip(
                        execute.__code__.co_freevars,
                        [c.cell_contents for c in execute.__closure__],
                        strict=True,
                    )
                )
                closure["replacements"]["tracer.services.clickhouse.v2"].get_reader = (
                    lambda: nullcontext(
                        SimpleNamespace(
                            root_ids_by_trace_ids=lambda ids: {
                                str(i): ("root", "project") for i in ids
                            }
                        )
                    )
                )

            def call(_params, context):
                selected.execute = execute
                guard = context.background_analysis
                if foreground:
                    context.background_analysis = None
                try:
                    workspace = b["module"](
                        "workspace", workspace_context=lambda **k: nullcontext()
                    )
                    with patch.dict(
                        "sys.modules", {"tfc.middleware.workspace_context": workspace}
                    ):
                        if foreground:
                            # A separate foreground request has no background
                            # ContextVars, not merely a cleared context field.
                            from contextvars import Context

                            return Context().run(dispatch, selected, raw, context)
                        return dispatch(selected, raw, context)
                finally:
                    context.background_analysis = guard
                    selected.execute = call

            selected.execute = call
            return selected, params

        witness = self.probe(
            "success",
            prepare=prepare,
            success=success,
            sends_expected=1 if name == "explore_trace" and success else 0,
        )
        if not success:
            self.assertTrue(witness.failed or witness.fallback_used)
            if name.startswith("submit_"):
                self.draft_model.drafts.create.assert_not_called()
                self.draft_details.drafts.create.assert_not_called()
        return witness

    def test_all_typed_fields_reject_original_json_type_mismatches(self):
        for name in MODELS:
            for field, spec in (
                model_for(name).model_json_schema()["properties"].items()
            ):
                types = {spec.get("type")} | {
                    s.get("type") for s in spec.get("anyOf", [])
                }
                bad = (
                    [True, "4", "0.9"]
                    if "number" in types
                    else ['["span"]', False]
                    if "array" in types
                    else ["false", 1]
                    if "boolean" in types
                    else [False, ["text"]]
                )
                for value in bad:
                    with self.subTest(tool=name, field=field, raw=value):
                        self.raw(name, {field: value})

    def test_valid_original_payloads_complete(self):
        for name in MODELS:
            with self.subTest(tool=name):
                self.raw(name, {}, success=True)

    def test_background_never_enters_cleanup_or_pydantic_coercion(self):
        for name in MODELS:
            with self.subTest(tool=name):
                self.raw(name, {}, success=True, forbid_cleanup=True)

    def test_foreground_number_and_array_cleanup_is_unchanged(self):
        self.raw(
            "submit_trace_scores",
            {"overall_score": True},
            success=True,
            foreground=True,
        )
        self.raw(
            "submit_trace_finding",
            {"location_spans": '["span"]'},
            success=True,
            foreground=True,
        )


if __name__ == "__main__":
    unittest.main()
