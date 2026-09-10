#!/usr/bin/env python3
"""Standalone verification for grounding evaluators (CPU only)."""
import os
import sys
import types
import unittest.mock as mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../futureagi"))

for _mod in [
    "structlog",
    "rest_framework",
    "rest_framework.response",
    "tfc.telemetry",
    "tfc.ee_stub",
    "agentic_eval.core_evals.fi_utils.json",
    "agentic_eval.core_evals.fi_utils.logging",
    "agentic_eval.core_evals.fi_utils.utils",
    "agentic_eval.core_evals.keys.openai_api",
    "agentic_eval.core_evals.llm_services.openai_api",
    "agentic_eval.core_evals.fi_utils.fi_code_execution",
    "agentic_eval.core_evals.fi_utils.exceptions",
    "agentic_eval.core_evals.fi_evals.grounded.similarity",
]:
    if _mod not in sys.modules:
        _m = types.ModuleType(_mod)
        _m.__spec__ = None
        sys.modules[_mod] = _m

sys.modules["agentic_eval.core_evals.fi_utils.json"].extract_json_path = lambda *a, **kw: None
sys.modules["agentic_eval.core_evals.fi_utils.json"].validate_json = lambda *a, **kw: True
sys.modules["agentic_eval.core_evals.fi_utils.logging"].logger = mock.MagicMock()
sys.modules["agentic_eval.core_evals.fi_utils.utils"].PreserveUndefined = object
sys.modules["agentic_eval.core_evals.keys.openai_api"].OpenAiApiKey = object
sys.modules["agentic_eval.core_evals.llm_services.openai_api"].OpenAiService = object
sys.modules["agentic_eval.core_evals.fi_utils.fi_code_execution"].CodeExecution = object
sys.modules["agentic_eval.core_evals.fi_utils.exceptions"].NoOpenAiApiKeyException = Exception
sys.modules["agentic_eval.core_evals.fi_evals.grounded.similarity"].CosineSimilarity = mock.MagicMock
sys.modules["tfc.telemetry"].wrap_for_thread = lambda x: x
sys.modules["tfc.ee_stub"]._ee_stub = lambda x: object
sys.modules["structlog"].get_logger = lambda *a, **kw: mock.MagicMock()

import importlib.util
import time

_spec = importlib.util.spec_from_file_location(
    "grounding_functions",
    os.path.join(
        os.path.dirname(__file__),
        "../futureagi/agentic_eval/core_evals/fi_evals/function/functions.py",
    ),
)
_functions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_functions)


def main():
    start = time.time()
    identical = _functions.calculate_bbox_iou([0, 0, 10, 10], [0, 0, 10, 10])
    print("IoU identical:", identical["result"], identical["reason"][:80])
    assert identical["result"] == 1.0
    partial = _functions.calculate_bbox_iou([0, 0, 10, 10], [5, 5, 15, 15])
    print("IoU partial:", partial["result"], partial["reason"][:80])
    assert abs(partial["result"] - 0.142857) < 0.001
    element = _functions.calculate_element_grounding(
        {"bbox": [0, 0, 10, 10], "label": "Submit button"},
        {"bbox": [0, 0, 10, 10], "label": "Submit button"},
    )
    print("Element full:", element["result"])
    assert element["result"] == 1.0

    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(0)
    arr = rng.integers(0, 256, size=(128, 128), dtype=np.uint8)
    base = Image.fromarray(arr, mode="L")
    mutated = arr.copy()
    mutated[8:24, 8:24] = 0
    changed = Image.fromarray(mutated, mode="L")
    full = _functions.calculate_region_similarity(base, changed, region=[0, 0, 128, 128])
    target = _functions.calculate_region_similarity(base, changed, region=[8, 8, 24, 24])
    print("Region full:", round(full["result"], 4), "target:", round(target["result"], 4))
    assert target["result"] < full["result"]
    elapsed = time.time() - start
    print(f"Total time: {elapsed:.2f}s")
    print("OVERALL PASS")


if __name__ == "__main__":
    main()
