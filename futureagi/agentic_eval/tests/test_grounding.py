"""Tests for grounding evaluators (CPU only, no GPU, no network)."""

import pytest

from agentic_eval.core_evals.fi_evals.eval_type import FunctionEvalTypeId
from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_bbox_iou,
    calculate_element_grounding,
    calculate_region_similarity,
)
from agentic_eval.core_evals.fi_evals.function.wrapper import (
    BoundingBoxIoU,
    ElementGrounding,
    RegionSimilarity,
)


class TestBoundingBoxIoU:
    def test_identical(self):
        res = calculate_bbox_iou([0, 0, 10, 10], [0, 0, 10, 10])
        assert res["result"] == 1.0

    def test_partial_overlap(self):
        res = calculate_bbox_iou([0, 0, 10, 10], [5, 5, 15, 15])
        assert abs(res["result"] - 0.142857) < 0.001

    def test_no_overlap(self):
        assert calculate_bbox_iou([0, 0, 5, 5], [10, 10, 15, 15])["result"] == 0.0

    def test_xywh_format(self):
        res = calculate_bbox_iou([0, 0, 10, 10], [0, 0, 10, 10], box_format="xywh")
        assert res["result"] == 1.0

    def test_invalid(self):
        assert calculate_bbox_iou([0, 0, 0, 0], [0, 0, 10, 10])["result"] == 0.0
        assert calculate_bbox_iou("bad", [0, 0, 10, 10])["result"] == 0.0

    def test_wrapper(self):
        ev = BoundingBoxIoU(box_format="xyxy")
        assert ev.function_name == FunctionEvalTypeId.BOUNDING_BOX_IOU.value


class TestElementGrounding:
    def test_full_match(self):
        pred = {"bbox": [0, 0, 10, 10], "label": "Submit button"}
        gold = {"bbox": [0, 0, 10, 10], "label": "Submit button"}
        assert calculate_element_grounding(pred, gold)["result"] == 1.0

    def test_wrong_label_drops(self):
        pred = {"bbox": [0, 0, 10, 10], "label": "Cancel button"}
        gold = {"bbox": [0, 0, 10, 10], "label": "Submit button"}
        full = calculate_element_grounding(gold, gold)["result"]
        partial = calculate_element_grounding(pred, gold)["result"]
        assert partial < full

    def test_wrong_box_fails(self):
        pred = {"bbox": [50, 50, 60, 60], "label": "Submit"}
        gold = {"bbox": [0, 0, 10, 10], "label": "Submit"}
        assert calculate_element_grounding(pred, gold)["result"] < 0.5

    def test_wrapper(self):
        ev = ElementGrounding()
        assert ev.function_name == FunctionEvalTypeId.ELEMENT_GROUNDING.value


class TestRegionSimilarity:
    def _images(self, changed=False):
        import numpy as np
        from PIL import Image

        rng = np.random.default_rng(0)
        arr = rng.integers(0, 256, size=(128, 128), dtype=np.uint8)
        base = Image.fromarray(arr, mode="L")
        if not changed:
            return base, Image.fromarray(arr.copy(), mode="L")
        mutated = arr.copy()
        mutated[8:24, 8:24] = 0
        return base, Image.fromarray(mutated, mode="L")

    def test_identical_region(self):
        first, second = self._images(changed=False)
        res = calculate_region_similarity(first, second, region=[0, 0, 128, 128])
        assert res["result"] >= 0.99

    def test_changed_region_drops(self):
        first, second = self._images(changed=True)
        full = calculate_region_similarity(first, second, region=[0, 0, 128, 128])["result"]
        target = calculate_region_similarity(first, second, region=[8, 8, 24, 24])["result"]
        assert full >= 0.9
        assert target < 0.5
        assert target < full

    def test_invalid_region(self):
        first, second = self._images()
        assert calculate_region_similarity(first, second, region=[0, 0, 0, 0])["result"] == 0.0

    def test_wrapper(self):
        ev = RegionSimilarity()
        assert ev.function_name == FunctionEvalTypeId.REGION_SIMILARITY.value
