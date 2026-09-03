# Ground Truth, and how to supply `expected_value`

Ground Truth and `expected_value` are two different things. Reading the Ground
Truth setup screen and concluding that enabling it will fill in a missing
`expected_value` is a reasonable mistake, and a common one. It will not.

`expected_value` is a **required eval input**, like `input` or `output`. Evals
that grade against a known answer declare it in their required keys, and the
runner resolves it from the mapping you configure, exactly as it resolves every
other input. It is per-row data, supplied by you at run time. It is not a
setting, not a dataset, and nothing infers it for you.

This page states what Ground Truth does, what it does not do, and how you
actually give an eval an expected value when it runs from Observe.

---

## What Ground Truth is

Ground Truth is **few-shot calibration**. You upload labelled rows, the rows are
embedded, and at eval time the most similar rows are retrieved and attached to
the judge prompt as reference examples, which is how a graded example teaches
the judge what a good answer looks like for your domain.

Concretely, `GroundTruthService.inject_context`
(`model_hub/services/ground_truth_service.py`) puts the retrieved rows on
`mapped["ground_truth_blocks"]`, and the evaluator renders them into the prompt.

That is the whole mechanism. Ground Truth:

- **does** steer the judge with retrieved, similar, human-labelled examples
- **does not** populate any required eval input
- **does not** supply `expected_value`, ever

So an eval that declares `expected_value` as a required key still needs
`expected_value` mapped. If it is not mapped, the run fails with:

```
Missing required input(s) for eval: expected_value.
Required keys: ['generated_value', 'expected_value']. Optional keys: [].
```

That error is correct. It is telling you a required input is unmapped, not that
Ground Truth is broken.

### Where Ground Truth is applied

Everything that runs an eval through `run_eval_func`
(`model_hub/views/utils/evals.py`) gets Ground Truth, because that is where
`inject_context` is called.

| Surface | Ground Truth applied | Why |
| --- | --- | --- |
| Datasets | Yes | `run_eval_func` |
| Prompt playground | Yes | `run_eval_func` |
| SDK evaluations | Yes | `run_eval_func` |
| Simulation | Yes | `run_eval_func` |
| Observe eval tasks, composite evals | Yes | children go `execute_composite_children_sync` to `_execute_child` to `run_eval_func` |
| Observe eval tasks, simple evals | **No** | `_execute_evaluation` and the trace / session equivalents call the evaluator directly |

So the gap is narrower than a plain `grep -rn "ground_truth" futureagi/tracer`
suggests. It is the **simple-eval** Observe path, not Observe as a whole. An
eval task that uses a simple template with Ground Truth switched on runs
**uncalibrated**; the same template inside a composite does not.

This used to be entirely silent. It is not any more: such a run now attaches a
`ground_truth_not_applied` warning to the eval result, which surfaces on the
task's logs view alongside partial-input warnings. The run still succeeds,
because Ground Truth never blocks an eval, but the state is now visible instead
of invisible. The warning keys off whether the run actually carries
`ground_truth_blocks`, so it stops on its own once the simple path is wired.

---

## Supplying `expected_value` on the Observe path

You supply it the same way you supply any other eval input: **emit it as a span
attribute, then map `expected_value` to that attribute name.**

There is no special-casing to work around. `_process_mapping`
(`tracer/utils/eval.py`) walks every key in the mapping and resolves each one
out of the span's attributes; `expected_value` resolves exactly like `input` or
`output` does.

### Worked example

**1. Emit the expected answer as a span attribute.**

```python
with tracer.start_as_current_span("answer_question") as span:
    answer = my_agent(question)
    span.set_attribute("input.value", question)
    span.set_attribute("output.value", answer)
    span.set_attribute("expected.answer", golden_answer)
```

The attribute name is yours to choose. `expected.answer` is used here to keep
it distinct from the eval input key.

**2. Map the eval input to that attribute.**

In the eval task's configuration:

| Eval input | Span attribute |
| --- | --- |
| `generated_value` | `output.value` |
| `expected_value` | `expected.answer` |

**3. Scope the task with attribute filters.**

Filter the task down to the spans that actually carry the attribute. For
example an attribute filter on `expected.answer` being present, or on whatever
marks your evaluation runs.

> **Do not scope the task with `trace_id` or `span_id`.**
> `parsing_evaltask_filters` (`tracer/utils/eval_tasks.py`) handles
> `span_attributes_filters`, `observation_type`, `session_id`, `date_range`,
> `created_at` and `project_id`. `trace_id` and `span_id` are accepted by the
> API and stored on the task, then fall through the branch chain and are
> ignored. The ClickHouse translator (`span_reader.py`) handles the same five
> keys and neither of these two. A task scoped that way silently evaluates, and
> bills for, every span in the project window, which looks exactly like a broken
> mapping. A fix is in review; this page will change when it lands.

### What this approach cannot do

You can only emit `expected_value` where you already know the correct answer at
emit time, which means a test or regression environment. In production you do
not know it,
and retrieval against a labelled dataset is the only mechanism that fits that
case. Ground Truth retrieval on the simple-eval Observe path is not
implemented today; inside a composite it already is.

---

## Summary

- Ground Truth is few-shot calibration attached to `mapped["ground_truth_blocks"]`.
- Ground Truth never supplies `expected_value`.
- Ground Truth is applied wherever an eval runs through `run_eval_func`:
  datasets, playground, SDK, Simulation, and **composite** Observe evals.
- It is **not** applied on the **simple-eval** Observe path, which is the gap.
- On Observe, supply `expected_value` as a span attribute and map to it.
- Scope such a task with attribute filters. `trace_id` / `span_id` are accepted
  and then silently ignored today.
