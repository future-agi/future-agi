# ADR 001 — Evaluations engine extracted from EvaluationRunner monolith

**Status**: Accepted
**Evidence**: `eval_runner.py:1606,1729,2018,2299,2303,2304,2536` (import cross-references); commit `ce5bb32` (Initial Commit)

## Context

`model_hub/views/eval_runner.py` is a ~2400-line class (`EvaluationRunner`) that handles
dataset eval runs end-to-end: input resolution, instance creation, param preparation, execution,
formatting, cell/column persistence, cost tracking, and error reporting. Every non-dataset eval
caller (tracer, simulate, SDK, playground) either duplicated its logic or reached into
`EvaluationRunner` as a side-effectful utility.

The same `_prepare_eval_config`, `_create_eval_instance`, `format_output`, and
`_get_few_shot_examples` were being reimplemented across at least seven call sites, with subtle
divergences between them.

## Decision

Extract the stateless core into `evaluations/engine/`:

- `runner.py` — single entry point `run_eval(request) → result`
- `registry.py` — evaluator class lookup
- `instance.py` — evaluator instantiation
- `params.py` — run param preparation
- `preprocessing.py` — input preprocessing (CLIP, FID)
- `formatting.py` — output formatting

`EvaluationRunner` keeps dataset-specific concerns (cell/column persistence, row iteration,
mapping) and delegates to the extracted functions for the shared core. Non-dataset callers
use `run_eval()` directly.

## Consequences

- **Good**: one canonical implementation of eval logic; divergences between callers eliminated.
- **Good**: engine is testable without Django ORM setup.
- **Good**: new eval callers get the full pipeline for free.
- **Watch**: `EvaluationRunner.format_output()` and `formatting.format_eval_value()` are now
  parallel implementations. When `row=None`, `format_output` delegates to `format_eval_value`.
  When `row` is provided it runs its own copy (dataset column creation is interleaved). These
  must be kept in sync manually until the dataset path is also fully extracted.
- **Watch**: the extraction is documented in comments like
  `"Extracted from EvaluationRunner._prepare_eval_config (eval_runner.py:1760)"` — these line
  numbers will drift as `eval_runner.py` changes.
