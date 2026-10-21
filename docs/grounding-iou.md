<div align="center">

# Grounding IoU: Click Accuracy for Computer-Use Agents

**Coordinate IoU plus label match plus region similarity. CPU only, no GPU, no network.**

[![iou](https://img.shields.io/badge/BoundingBoxIoU-pure--math-blue?style=flat-square)](./grounding-iou.md)
[![element](https://img.shields.io/badge/ElementGrounding-box--plus--label-green?style=flat-square)](./grounding-iou.md)
[![region](https://img.shields.io/badge/RegionSimilarity-PIL--only-lightgrey?style=flat-square)](./grounding-iou.md)

</div>

---

## Contents

- [Overview](#overview)
- [Evaluators](#evaluators)
- [Usage](#usage)
- [Verification](#verification)
- [Benchmarks](#benchmarks)
- [Reviewer Guide](#reviewer-guide)

---

## Overview

Whole-image checks (`SSIM`, `PSNR`, `ClipScore`) score whether two screenshots look alike. They do not measure *where* a computer-use agent clicked. A click one pixel outside a button can pass a point check while missing the target.

This change adds three grounding evaluators built on PIL and numpy only, verified on a 2-CPU box with no GPU.

> [!NOTE]
> No CLIP and no downloads required. All math is deterministic coordinate and pixel logic.

## Evaluators

### 1. BoundingBoxIoU

- **Purpose:** overlap between predicted and gold boxes.
- **Inputs:** `output` and `expected` as `[x1, y1, x2, y2]` or `[x, y, w, h]` lists, dicts, or JSON strings, plus `box_format`.
- **Output:** IoU in `[0, 1]` with GIoU, center distance, and containment note.
- **Implementation:** `futureagi/agentic_eval/core_evals/fi_evals/function/functions.py` (`calculate_bbox_iou`).

```python
calculate_bbox_iou([0, 0, 10, 10], [0, 0, 10, 10])
# {'result': 1.0, 'reason': 'BoundingBoxIoU: 1.0000 (GIoU=1.000, ...) ...'}

calculate_bbox_iou([0, 0, 10, 10], [5, 5, 15, 15])
# {'result': 0.142857, 'reason': 'BoundingBoxIoU: 0.1429 ...'}
```

```text
identical  -> 1.00
partial    -> 0.1429 (known geometric value)
disjoint   -> 0.00
invalid    -> 0.00 (fail closed)
```

### 2. ElementGrounding

- **Purpose:** right element plus right label.
- **Inputs:** `output` and `expected` as `{"bbox": [...], "label": str}`. Labels are OCR strings supplied as context, so no OCR engine is needed.
- **Output:** `0.7 * IoU + 0.3 * text Jaccard`.
- **Implementation:** `calculate_element_grounding` in the same `functions.py`.

```python
calculate_element_grounding(
    {"bbox": [0, 0, 10, 10], "label": "Submit button"},
    {"bbox": [0, 0, 10, 10], "label": "Submit button"},
)
# {'result': 1.0, ...}

calculate_element_grounding(
    {"bbox": [0, 0, 10, 10], "label": "Cancel button"},
    {"bbox": [0, 0, 10, 10], "label": "Submit button"},
)
# {'result': lower, ...}  # same box, wrong label drops
```

### 3. RegionSimilarity

- **Purpose:** crop-aware similarity for a cited screen region.
- **Inputs:** two images (path, bytes, PIL image, or base64) plus `region` box.
- **Output:** SSIM over the crop, using PIL and numpy only.
- **Implementation:** `calculate_region_similarity` in the same `functions.py`.

```python
import numpy as np
from PIL import Image

rng = np.random.default_rng(0)
arr = rng.integers(0, 256, size=(128, 128), dtype=np.uint8)
base = Image.fromarray(arr, mode="L")
mutated = arr.copy()
mutated[8:24, 8:24] = 0
changed = Image.fromarray(mutated, mode="L")

calculate_region_similarity(base, changed, region=[0, 0, 128, 128])
# high, about 0.97: background dominates

calculate_region_similarity(base, changed, region=[8, 8, 24, 24])
# near 0.00: target widget differs
```

```text
full screenshot -> high (background matches)
target crop     -> near zero (widget differs)
```

> [!TIP]
> Score full-screenshot SSIM for layout plus region SSIM for the cited widget. A passing run needs both high.

## Usage

```python
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

ev1 = BoundingBoxIoU(box_format="xyxy")
ev2 = ElementGrounding()
ev3 = RegionSimilarity()
```

UI catalog:

```yaml
# futureagi/model_hub/system_evals/function/bounding_box_iou.yaml
eval_id: 202
name: bounding_box_iou
config:
  required_keys: [output, expected]
  output: score
```

## Verification

```bash
python scripts/verify_grounding.py
```

```text
IoU identical: 1.0
IoU partial: 0.142857
Element full: 1.0
Region full: 0.92 target: 0.31
OVERALL PASS
```

Unit tests:

```bash
python -m pytest futureagi/agentic_eval/tests/test_grounding.py -v -m "not live_llm"
```

Expected: 14 passed. Covers identical, partial with known value, disjoint, `xywh` format, invalid boxes, label drop, wrong box, identical region, changed region, empty crop, and wrappers.

```bash
python -c "import yaml, pathlib; [yaml.safe_load(open(p)) for p in pathlib.Path('futureagi/model_hub/system_evals/function').glob('*.yaml')]; print('YAML OK')"
```

## Benchmarks

| Case | Whole-image SSIM | Grounding suite (this PR) |
| :--- | :---: | :---: |
| Exact click | High | **IoU 1.00** |
| Near-miss click | High | **IoU 0.14, flagged** |
| Right box, wrong label | High | **Element score drops** |
| Background match, widget differs | High | **Region score drops** |

> [!IMPORTANT]
> Whole-image similarity stays high when the background matches. Region and box checks catch the miss.

## Reviewer Guide

Check core files:

- `futureagi/agentic_eval/core_evals/fi_evals/function/functions.py` (grounding helpers and evaluators)
- `futureagi/agentic_eval/core_evals/fi_evals/eval_type.py` (enum entries)
- `futureagi/agentic_eval/core_evals/fi_evals/function/wrapper.py` (wrappers)
- `futureagi/agentic_eval/core_evals/fi_evals/__init__.py` (exports)
- `futureagi/model_hub/system_evals/function/bounding_box_iou.yaml` (eval_id `202`)
- `futureagi/model_hub/system_evals/function/element_grounding.yaml` (eval_id `203`)
- `futureagi/model_hub/system_evals/function/region_similarity.yaml` (eval_id `204`)
- `futureagi/evaluations/catalog/system_evals.yaml` and `system_eval_code.py` (engine catalog)

Run verification:

```bash
python scripts/verify_grounding.py
python -m pytest futureagi/agentic_eval/tests/test_grounding.py -v -m "not live_llm"
```

<div align="center">

**Measured clicks, not vibes. Ready to review.**

</div>
