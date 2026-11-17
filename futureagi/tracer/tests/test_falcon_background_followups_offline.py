"""Additional offline continuation coverage; same no-egress AST harness.

These probes are not Django integration evidence. Running this file also runs
its inherited r2 RED/GREEN regressions without changing their original oracles.
"""

import runpy
import unittest
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

support = runpy.run_path(
    str(Path(__file__).with_name("test_falcon_background_boundaries_offline.py"))
)
definitions = support["definitions"]
module = support["module"]


class FollowupTests(support["BoundaryTests"]):
    def test_vector_update_cannot_authorize_late_insert(self):
        writes = []

        def execute(query, *args, **kwargs):
            writes.append(query.split()[0])
            self.active = False

        upsert = definitions(
            "agentic_eval/core/database/ch_vector.py",
            {"bulk_upsert_vectors"},
            {
                "uuid": SimpleNamespace(uuid4=uuid4),
                "datetime": datetime,
                "sanitize_metadata": lambda x: x,
                "sanitize_keys": lambda x: x,
                "logger": Mock(),
            },
            owner="ClickHouseVectorDB",
        )["bulk_upsert_vectors"]
        db = SimpleNamespace(client=SimpleNamespace(execute=execute))
        with self.assertRaises(self.Aborted):
            upsert(
                db,
                "synthetic",
                "project",
                [[1.0]],
                [{"id": "detail"}],
                ["id"],
                before_write=self.guard.check,
            )
        self.assertEqual(writes, ["ALTER"])

    def test_embedding_read_and_provider_cannot_authorize_late_vectors(self):
        for boundary in ("existence", "provider"):
            with self.subTest(boundary=boundary):
                self.active = True
                self.guard = self.Guard("p", "u", "o", "w", 2)
                effects = []
                analysis = SimpleNamespace(trace_id="trace", project_id="project")
                analyses = Mock()
                analyses.filter.return_value.select_related.return_value.first.return_value = analysis
                details = Mock()
                details.filter.return_value.select_related.return_value = [
                    SimpleNamespace(id="detail", error_id="E1", category="synthetic")
                ]

                def exists(*args, boundary=boundary):
                    if boundary == "existence":
                        self.active = False
                    return False

                def embed(text, effects=effects):
                    effects.append("provider")
                    self.active = False
                    return [1.0]

                em = SimpleNamespace(
                    db_client=SimpleNamespace(
                        bulk_upsert_vectors=lambda effects=effects, **kw: (
                            effects.append("vectors")
                        )
                    ),
                    close=Mock(),
                )
                clusterer = SimpleNamespace(
                    table_name="synthetic",
                    _build_embedding_text_from_detail=lambda d: "synthetic",
                    _normalize_category_family=lambda c: c,
                )
                analysis._analysis_snapshot_details = (
                    details.filter.return_value.select_related.return_value
                )
                self.modules.update(
                    {
                        "tracer.services.analysis_result_snapshot": module(
                            "snapshot", read_analysis=lambda query: query.first()
                        ),
                        "ee.agenthub.traceerroragent.error_cluster": module(
                            "clusterer",
                            ErrorEmbeddingClusterer=lambda clusterer=clusterer: (
                                clusterer
                            ),
                        ),
                        "agentic_eval.core.embeddings.embedding_manager": module(
                            "embedding",
                            EmbeddingManager=lambda em=em: em,
                            model_manager=SimpleNamespace(text_model=embed),
                        ),
                    }
                )
                ingest = definitions(
                    "tracer/queries/error_analysis.py",
                    {"ingest_trace_error_embeddings"},
                    {
                        "TraceErrorAnalysis": SimpleNamespace(objects=analyses),
                        "TraceErrorDetail": SimpleNamespace(objects=details),
                        "datetime": datetime,
                        "logger": Mock(),
                    },
                    owner="TraceErrorAnalysisDB",
                )["ingest_trace_error_embeddings"]
                self.install()
                with self.assertRaises(self.Aborted):
                    ingest(
                        SimpleNamespace(_embedding_exists_for_detail=exists),
                        "trace",
                        guard=self.guard,
                    )
                self.assertEqual(
                    effects, [] if boundary == "existence" else ["provider"]
                )
                em.close.assert_called_once()

    def test_native_tool_error_wrapper_preserves_terminal_abort(self):
        self.modules["tfc.middleware.workspace_context"] = module(
            "workspace", workspace_context=lambda **kw: nullcontext()
        )
        self.install()
        capability_support = runpy.run_path(
            str(
                Path(__file__).with_name(
                    "test_falcon_background_capabilities_offline.py"
                )
            )
        )
        ns = capability_support["CapabilityTests"].tools(self)
        run = ns["BaseTool"].run

        def execute(*args):
            self.guard.abort("analysis_deadline_exceeded")

        tool = type(
            "SubmitTraceFindingTool",
            (ns["BaseTool"],),
            {
                "__module__": "ai_tools.tools.tracing.submit_trace_finding",
                "name": "submit_trace_finding",
                "input_model": SimpleNamespace(model_validate=lambda p: p),
                "execute": execute,
            },
        )()
        with self.assertRaises(self.Aborted) as caught:
            run(tool, {}, self.context)
        self.assertEqual(caught.exception.code, "analysis_deadline_exceeded")

    def test_unguarded_vector_writes_keep_existing_behavior(self):
        # The callback is background-only; ordinary callers keep both writes.
        writes = []
        db = SimpleNamespace(
            client=SimpleNamespace(
                execute=lambda q, *a, **kw: writes.append(q.split()[0])
            )
        )
        upsert = definitions(
            "agentic_eval/core/database/ch_vector.py",
            {"bulk_upsert_vectors"},
            {
                "uuid": SimpleNamespace(uuid4=uuid4),
                "datetime": datetime,
                "sanitize_metadata": lambda x: x,
                "sanitize_keys": lambda x: x,
                "logger": Mock(),
            },
            owner="ClickHouseVectorDB",
        )["bulk_upsert_vectors"]
        self.assertEqual(
            len(
                upsert(db, "synthetic", "project", [[1.0]], [{"id": "detail"}], ["id"])
            ),
            1,
        )
        self.assertEqual(writes, ["ALTER", "INSERT"])


if __name__ == "__main__":
    unittest.main()
