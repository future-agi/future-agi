# Choosing retrieval and answer checks

Reuse the existing evaluators when they answer the question:

| Question | Evaluator |
| --- | --- |
| Were these exact reference chunks retrieved? | `non_llm_context_precision`, `non_llm_context_recall` |
| How well are exact relevant items ranked? | `precision_at_k`, `recall_at_k`, `mean_average_precision` |
| Does the context help answer the query? | `context_relevance` (model judged) |
| Does the context support the answer? | `groundedness` (model judged) |

This change does not add duplicate `rag_context_precision` or `rag_faithfulness`
model judges. The existing judges remain unchanged. No model calls are needed for
the four new **lexical diagnostics** below, but their results do not establish
truth, relevance, entailment, or causal influence.

## Lexical score contracts

- `context_precision_score` / `ContextPrecisionScore`: average precision over
  lexical matches among the evaluated top K chunks, divided by the number of
  matches **in that evaluated list**, not the number of reference chunks. A match
  has token precision or recall at least `min_token_overlap` (default **0.6**), or
  one lowercased text contains the other. This is not exact-item MAP. `k=None`
  evaluates the whole list; integer-coercible cutoffs are clamped to at least 1;
  unparseable cutoffs retain the whole list.
- `context_recall_score` / `ContextRecallScore`: fraction of references with token
  coverage at least `min_token_overlap` (default **0.7**) in any **one** retrieved
  chunk, or reference text contained in that chunk. It does not combine partial
  matches across chunks. Python and catalog defaults and overrides are identical.
- `faithfulness_score` / `FaithfulnessScore`: fraction of sentence-like segments
  with at least 70% token overlap with the combined context, or text containment.
  Segments of three characters or fewer are discarded unless none remain.
  **Contradictions can score 1.0.** For example, “Paris is not the capital of
  France” shares 7/8 tokens with “Paris is the capital of France”. The score is
  lexical overlap, not semantic support; use `groundedness` for the latter.
- `noise_sensitivity` / `NoiseSensitivity`: **1 minus** the fraction of output
  tokens found in context but absent from the expected answer. Higher is better,
  matching the catalog's percentage-score convention. The reason also reports
  raw `sensitivity` (lower is better). The reference is required in the catalog;
  an explicitly empty reference counts all context-matching output tokens as
  extra. This does not prove that context caused an answer or that extra tokens
  are factually wrong.

Tokenization is case-insensitive Unicode word matching; counts include repeated
tokens. Inputs accept native lists, JSON arrays, or newline-separated/plain text.
Other strings, including Python literal syntax, are treated as text, not executed
or parsed as Python. `None`, blank text, and empty lists mean empty input.
Both retrieval inputs empty score 1; one empty scores 0. Empty answer scores 1 for
sentence overlap; nonempty answer with empty context scores 0. No output tokens
or no context tokens means raw noise sensitivity 0 and normalized score 1.
Punctuation-only references/segments retain the empty-token convention and are
not meaningful factual evidence.

Named `hypothesis`/`reference` inputs take precedence over `output`/`expected`
aliases unless the named input is `None`. Thresholds must be finite numbers from
0 to 1. Existing exact-match and model-judged evaluators are not modified.

## Maintaining parity

The implementation is `agentic_eval/core_evals/fi_evals/function/rag_scores.py`.
The catalog needs self-contained code for its existing execution service, so the
four YAML `code` blocks are generated from those functions and shared helpers:

```sh
# From futureagi/
python -m model_hub.system_evals.render_lexical_scores
python -m model_hub.system_evals.render_lexical_scores --check
pytest model_hub/system_evals/tests/test_rag_lexical_scores.py
```

Edit descriptions and parameter schemas in YAML; edit algorithms only in the
shared Python module. The tests cover generated-body freshness, Python wrappers,
score/parameter parity, contradictory text, and the local code-eval sandbox.
The generator uses only standard-library source parsing; no application imports
or provider calls are needed.

The four new catalog IDs are 221–224. Seeding version 31 installs them on an
existing version-30 database without replacing existing templates. The proposed
unmerged Python recall default changes from 0.6 to 0.7, and its raw noise return
changes to the normalized higher-is-better score described above. Callers testing
the original proposal should update thresholds and noise comparisons accordingly.
