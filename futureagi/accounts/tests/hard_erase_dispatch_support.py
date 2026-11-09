"""Venue-aware assertions for the real graph, never fixture production edges."""

import runpy
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SOURCES = runpy.run_path(str(TESTS / "hard_erase_source_files.py"))


def check_edges(case, graph):
    dispatch = graph["DISPATCH"]
    present = SOURCES["ee_available"](graph["ROOT"])
    for key in graph["NESTED"] | set(graph["SEPARATE_TRANSACTIONS"]):
        if key == dispatch["CALLER"] and not present:
            case.assertNotIn(key, case.reached)
        else:
            case.assertIn(key, case.reached)
    if present:
        for source, target in (
            (dispatch["CALLER"], dispatch["INSTALL"]),
            (dispatch["INSTALL"], dispatch["DISPATCH"]),
            (dispatch["DISPATCH"], dispatch["ORIGINAL"]),
        ):
            case.assertIn(source, case.reached)
            case.assertIn(target, {callee for _, callee in case.edges[source]})
    else:
        # OSS has no renamed Falcon method: it has no EE source. Keep the
        # registration/capture controls on the reviewed fixture, not in graph().
        for key in (dispatch["INSTALL"], dispatch["DISPATCH"], dispatch["ORIGINAL"]):
            case.assertNotIn(key, case.functions)
            case.assertNotIn(key, case.reached)
        controls = runpy.run_path(str(TESTS / "test_hard_erase_cut_health_flow.py"))
        controls["DispatcherBindingTests"]().test_capture_and_registration_keep_original_issuance_method()
