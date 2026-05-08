# ADR 007 — Preprocessing keyed on `eval_template.name` — known fragility

**Status**: Superseded — tracked in issue [#301](https://github.com/future-agi/future-agi/issues/301)
**Evidence**: `evaluations/engine/preprocessing.py:47,140` (decorator registrations); `evaluations/engine/runner.py:151` (lookup call); `agentic_eval/core_evals/fi_evals/__init__.py:160-161` (class names `"ClipScore"`, `"FidScore"`)

## Context

`evaluations/engine/preprocessing.py` registers preprocessors with a string key:

```python
@register_preprocessor("clip_score")
def _preprocess_clip(inputs): ...

@register_preprocessor("fid_score")
def _preprocess_fid(inputs): ...
```

The runner looks them up by passing `eval_template.name`:

```python
run_params = preprocess_inputs(eval_template.name, run_params)
```

## What happened

When the preprocessing module was extracted from `EvaluationRunner`, the lookup key was
copied from an existing pattern that used the template's human name. The eval class names
(`ClipScore`, `FidScore`) and the registered keys (`"clip_score"`, `"fid_score"`) were not
reconciled against `eval_type_id`.

## Why this is fragile

`eval_template.name` is a user-editable string. If a user renames their ClipScore template to
anything other than `"clip_score"`, preprocessing silently stops. The evaluator runs without
`_image_embeddings` and produces incorrect results with no warning.

The stable identifier is `eval_type_id` (stored in `eval_template.config["eval_type_id"]`),
which is set at template creation and never changes.

## Intended fix (issue #301)

Change the registry key and lookup to use `eval_type_id`:

```python
@register_preprocessor("ClipScore")   # matches eval_type_id, not template name
@register_preprocessor("FidScore")
```

And in `runner.py` and `model_hub/views/eval_runner.py`:

```python
run_params = preprocess_inputs(eval_type_id, run_params)
```

## Consequences of the current state

- Works correctly only when `eval_template.name` exactly matches the registered key.
- System eval templates created by `apply_catalog.py` use the registered keys, so the default
  install works. Custom templates with different names silently skip preprocessing.
