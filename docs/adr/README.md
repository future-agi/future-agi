# Architecture Decision Records

One file per significant non-obvious design decision. Format: **context → decision → consequences**.

These are not "what the code does" — read the SPEC.md files for that. These are "why it was built this way and what you'd break if you changed it."

| # | Title | Status |
|---|-------|--------|
| [001](001-evaluations-engine-extraction.md) | Evaluations engine extracted from EvaluationRunner monolith | Accepted |
| [002](002-few-shots-futureagi-only.md) | Few-shot RAG injection is FutureAGI-eval-only | Accepted |
| [003](003-result-failure-string.md) | `EvalResult.failure` is a string, not a bool | Accepted |
| [004](004-cost-none-not-empty-dict.md) | `EvalResult.cost` is `None` when absent, not `{}` | Accepted |
| [005](005-registry-lazy-singleton.md) | Evaluator registry is a lazy singleton | Accepted |
| [006](006-protect-shortcut-in-runner.md) | Protect model shortcut lives in the runner, not the template | Accepted |
| [007](007-preprocessing-keyed-on-template-name.md) | Preprocessing keyed on `eval_template.name` — known fragility | Superseded by #301 |
| [008](008-oss-stubs-not-none-fallbacks.md) | OSS billing stubs replace `except ImportError: X = None` fallbacks | Accepted |
