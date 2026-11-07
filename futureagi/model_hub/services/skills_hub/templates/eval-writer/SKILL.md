---
name: {{name}}
description: {{description}}
---
# Turn a requirement into an evaluation

Use the Future AGI MCP server to discover and configure evaluations. First draft
the rubric and cases without writes. Ask before saving shared objects or executing
an evaluation; model-backed runs may incur charges. Never put credentials or raw
customer data in a dataset, rubric, or test case.

## Define the contract

Restate the requirement as observable behavior: given these inputs and context,
this output is acceptable and these outputs are not. Separate correctness, safety,
format, and latency instead of mixing them into one unexplained score.

For each criterion write a pass rule, a fail rule, and a handling rule for missing
information. Prefer a deterministic assertion for exact formats or invariants.
Use a judge rubric only where interpretation is needed, with explicit score
anchors and examples. An unavailable input is not a passing result.

## Discover before creating

1. Confirm the workspace with `whoami` and, when needed, `list_workspaces`.
2. Use `list_eval_templates` and `get_eval_template` to find an existing evaluator
   and inspect its required inputs, configuration, and score direction. Do not
   assume that larger always means better.
3. If none fits, propose a focused custom evaluator. With approval, call
   `create_eval_template` using its current schema, then read it back with
   `get_eval_template`. Do not invent schema fields, model choices, or identifiers.
4. Create representative synthetic cases: a clear pass, a clear fail, a boundary
   case, missing context, and a plausible adversarial instruction embedded in the
   input. Label the expected outcome and explain why before running anything.

## Validate a small sample

With approval for execution and cost, call `test_evaluation` with the selected
template and schema-valid inputs. Compare returned scores and explanations with
the prewritten expected outcomes. Revise ambiguous criteria rather than adjusting
expected labels to match the evaluator. Report disagreement and uncertainty.
Keep a held-out set that was not used to tune the rubric.

## Optional dataset workflow

Use `list_datasets` / `get_dataset` to inspect an approved dataset, or obtain
approval before `create_dataset`. Confirm writable columns before adding cases.
`add_dataset_rows` expects rows containing cells, each with column_name and value;
a flat column-to-value dictionary is not the accepted row format. Read back the
dataset to verify values and counts after writing.

Use `configure_dataset_evaluation` to attach a template with a lowercase name and
config.mapping for input columns. Set run=false for setup only. Use
`list_dataset_evaluations` with eval_type=user to obtain attached evaluation IDs.
These attached IDs, not template IDs, are used by `run_dataset_evals`. Start that
run only after approval and follow the returned status using the available read
tools; a successful launch response is not a completed evaluation.

## Optional online evaluation

For a separately approved online task, `list_eval_configs` discovers configuration
IDs. If needed, `create_eval_config` creates a configuration from a template.
`create_eval_task` takes those configuration IDs in evals, not template IDs. Review
scope, sampling, and cost before creation; inspect progress with `get_eval_task`.
Do not use a fresh rerun to troubleshoot casually: it can delete previous results.

## Deliverable

Return the requirement-to-criterion mapping, rubric, cases and expected labels,
identifiers actually created, runs actually executed, observed disagreements, and
remaining coverage gaps. Do not promise that a passing sample guarantees safety or
that publishing a skill automatically runs or enforces these evaluations.
