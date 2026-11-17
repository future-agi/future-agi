"""Service reader snapshots under retry; socket-blocked, modeled storage only."""

import runpy
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

support = runpy.run_path(
    str(Path(__file__).with_name("test_analysis_legacy_snapshot_offline.py"))
)
readers, definition = support["readers"], support["definition"]


class ServiceSnapshots(readers["BoundReaders"]):
    def test_service_readers_hydrate_pinned_parents_during_retry(self):
        for name, args in (
            ("get_analysis_by_trace", ("trace",)),
            ("get_recent_analyses", ()),
            ("search_analyses", ({},)),
        ):
            with self.subTest(reader=name):
                receipt = self.receipts.rows[0]
                receipt.status, receipt.result_analysis_id = "completed", "G1"

                def retry(**kwargs):
                    self.receipts.rows[0].status = "running"
                    self.receipts.rows[0].result_analysis_id = None
                    return readers["Rows"](self.details.rows).filter(**kwargs)

                self.install_snapshot(detail_filter=retry)
                proxy = SimpleNamespace(
                    filter=lambda **kw: self.analyses.filter(
                        **{k: v for k, v in kw.items() if k != "analysis_date__gte"}
                    )
                )
                fn = definition(
                    "ee/agenthub/traceerroragent/analysis_service.py",
                    name,
                    {
                        "TraceErrorAnalysis": SimpleNamespace(objects=proxy),
                        "datetime": datetime,
                        "timedelta": timedelta,
                        "logger": Mock(),
                    },
                    "TraceErrorAnalysisService",
                )
                result = fn(SimpleNamespace(project_id="p"), *args)
                parent = result if name == "get_analysis_by_trace" else result[0]
                self.assertEqual(receipt.status, "running")
                self.assertIs(parent, self.good)
                self.assertEqual(
                    [d.id for d in parent._analysis_snapshot_details], ["new"]
                )
                self.assertEqual(
                    [
                        d.id
                        for d in parent._prefetched_objects_cache["error_details"].all()
                    ],
                    ["new"],
                )
                self.assertEqual(
                    list(
                        parent._prefetched_objects_cache["analysis_error_groups"].all()
                    ),
                    [],
                )


if __name__ == "__main__":
    unittest.main()
