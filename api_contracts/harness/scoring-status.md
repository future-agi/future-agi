# Scoring status on the v3 run calls — frontend contract (v1)

**Date:** 2026-10-07 · **For:** the run detail screens ·
**Endpoints:** `GET /simulate/v3/test-executions/<id>/calls/` and
`GET /simulate/v3/call-executions/<id>/`.

Every call in a run is scored after it ends: its evaluations are graded and,
for some runs, a CSAT score is computed. Scoring can take minutes, can fail,
and can get stuck. This contract tells you, for each call and each eval,
whether scoring is still running, finished, failed, timed out or was not
done, so a loading cell never looks like an empty one and an error never
looks like "no data".

## What changed in the responses

All fields below are always present.

### Each call row (`results[]`, and the call detail response)

| Field            | Type           | Values                                                                                                 |
| ---------------- | -------------- | ------------------------------------------------------------------------------------------------------ |
| `scoring_status` | enum           | `not_applicable` \| `pending` \| `succeeded` \| `failed` \| `timed_out`                                |
| `csat_status`    | enum           | `not_applicable` \| `pending` \| `succeeded` \| `failed` \| `timed_out` \| `skipped`                   |
| `csat_reason`    | string \| null | `null` unless `csat_status` is `failed`, `skipped` or `timed_out`; then one of the fixed strings below |

`csat_reason` is `null` on most calls. The generated type
(`frontend/src/generated/api-contracts/api.schemas.ts`) declares it a
non-empty `string`, and the generated zod schema (`api.zod.ts`) is
`zod.string().min(1)`, because the client generator drops the nullable flag.
Check for `null` before using it, and do not parse these responses with that
schema until the generator is fixed.

`scoring_status` sums up the call's evals and CSAT: `pending` if anything is
still being scored, else `timed_out` if anything timed out, else `failed` if
anything failed, else `succeeded` if anything was scored. Evals and CSAT that
were `skipped` or `not_applicable` do not count, so a call with nothing to
score (or whose scoring was all skipped) reads `not_applicable`.

### Each eval entry (`results[].evaluations[]`)

| Field    | Type                 | Values                                                                                                                                                               |
| -------- | -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `status` | enum (was free text) | `pending` \| `succeeded` \| `failed` \| `timed_out` \| `skipped`                                                                                                     |
| `reason` | string               | `""` while `pending`; one of the fixed strings below for a timeout, a cancelled run or an evaluator that returned nothing; otherwise the evaluator's own explanation |

**New: an eval that is expected but not scored yet is listed as its own
`pending` entry.** Before, it was simply missing until it was scored. A
pending entry looks like this:

```json
{
  "id": "cfg-b",
  "name": "Tone",
  "type": "",
  "value": null,
  "score": null,
  "passed": null,
  "reason": "",
  "status": "pending"
}
```

An eval column with no entry at all on a call still means that eval does not
apply to that call: show "—".

### The summary (`summary.scoring`)

| Field                            | Type |
| -------------------------------- | ---- |
| `summary.scoring.not_applicable` | int  |
| `summary.scoring.pending`        | int  |
| `summary.scoring.succeeded`      | int  |
| `summary.scoring.failed`         | int  |
| `summary.scoring.timed_out`      | int  |

The counts cover every call that matches the current search and filters, not
only the page you loaded, and they add up to `summary.total`. The run's own
summary (`execution.summary`) has no `scoring` object.

### The run (`execution.status`)

`execution.status` can now be `evaluating` for harness, SDK and chat runs:
the calls have finished, but scoring is still going. The run becomes
`completed` only when every call's scoring has finished (succeeded, failed
or timed out). Adding an eval to a completed run puts it back to
`evaluating` until the new grading is done; a `failed` run stays `failed`
while its calls show the new eval `pending`. A run that was being stopped
ends `cancelled`.

## How to render it

| Eval `status` | Cell                                                                                 |
| ------------- | ------------------------------------------------------------------------------------ |
| no entry      | "—"                                                                                  |
| `pending`     | loading skeleton                                                                     |
| `succeeded`   | the result, as today                                                                 |
| `failed`      | an error state showing `reason` (no generic "Uh-oh" when `reason` is set)            |
| `timed_out`   | "Timed out", with `reason` as the detail. It can still turn into a real result later |
| `skipped`     | a neutral "Not scored", with `reason`                                                |

CSAT works the same way from `csat_status` and `csat_reason`, and
`not_applicable` shows "—". Use `scoring_status` for a per-row badge.
Show every `reason` and `csat_reason` exactly as sent. `csat_reason` and the
fixed eval reasons (the timed-out, did-not-finish, run-cancelled and
no-result strings below) are written for people and never contain internal
error text. Any other eval `reason` is the evaluator's own text: its
explanation on a result, or the error message on a `failed` entry, which can
name configs, scenarios or columns, as it does today.

## When to poll

Keep polling while `execution.status` is one of the active statuses
(`pending`, `running`, `cancelling`, `evaluating` — `ACTIVE_EXECUTION_STATUSES`
in `frontend/src/sections/simulate/environments/workspace/runs/runs.constants.js`)
**or** `summary.scoring.pending > 0`. `liveEvalCell` in
`frontend/src/api/simulate-environments/runCalls.js` carries the entry's
`status` to the cell. The API now always sends one of the five values above:
`succeeded` where it sent `completed`, `failed` where it sent `error`, and the
new `timed_out`. Anything that compared an eval's `status` with `completed` or
`error` must switch to the new values. The runs table does not yet have a
`timed_out` case: `UnscoredEval` (`traceCells.jsx`) shows such an eval as
"N/A" with the tooltip "Not applicable to this scenario" and does not show its
`reason`, until it gets one (TH-8098).

Three things to expect:

1. Once the run is finished and nothing is `pending`, polling stops. A late
   result that replaces a `timed_out` entry then shows on the next load, not
   live. On a finished run its row shows it at once, but `summary.scoring`
   can take up to five minutes to count it unless that call is on the page
   you loaded.
2. While a call is still in progress or being analysed (call status
   `pending`, `queued`, `ongoing` or `analyzing`), its CSAT reads `pending`
   even on runs that never score CSAT; it turns `not_applicable` once the
   call is done.
3. A `pending` cell can turn `timed_out` between two polls with nothing else
   changing: each call's scoring has a time limit (10 minutes without
   progress, or 30 minutes without starting).

## Fixed reason strings

These are the only non-evaluator reasons you will see, word for word.

| Where         | Text                                                            | Meaning                                          |
| ------------- | --------------------------------------------------------------- | ------------------------------------------------ |
| eval `reason` | `Scoring timed out: no progress for 10 minutes.`                | grading started, then went silent                |
| eval `reason` | `Scoring timed out: no progress within 30 minutes of dispatch.` | grading never started                            |
| eval `reason` | `Scoring did not finish.`                                       | grading was cut short and nothing will finish it |
| eval `reason` | `Not scored: the run was cancelled.`                            | the run was stopped before this eval was scored  |
| eval `reason` | `The evaluator returned no result.`                             | the evaluator ran but gave nothing back          |
| `csat_reason` | `CSAT timed out: no result within 10 minutes.`                  | CSAT started, then went silent                   |
| `csat_reason` | `CSAT timed out: scoring did not start within 30 minutes.`      | CSAT never started                               |
| `csat_reason` | `CSAT did not finish.`                                          | CSAT was cut short                               |
| `csat_reason` | `Nothing to score: the call has no transcript or recording.`    | CSAT had no evidence; not an error               |
| `csat_reason` | `Not scored: the run was cancelled.`                            | the run was stopped first                        |
| `csat_reason` | `CSAT could not be scored.`                                     | CSAT failed                                      |
| `csat_reason` | `CSAT was not scored.`                                          | CSAT was skipped for another reason              |

## Example rows

Still scoring: one eval waiting, CSAT waiting.

```json
{
  "scoring_status": "pending",
  "csat_status": "pending",
  "csat_reason": null,
  "evaluations": [
    {
      "id": "cfg-b",
      "name": "Tone",
      "type": "",
      "value": null,
      "score": null,
      "passed": null,
      "reason": "",
      "status": "pending"
    }
  ]
}
```

Timed out: the eval's grading went silent and CSAT never started.

```json
{
  "scoring_status": "timed_out",
  "csat_status": "timed_out",
  "csat_reason": "CSAT timed out: scoring did not start within 30 minutes.",
  "evaluations": [
    {
      "id": "cfg-a",
      "name": "Resolution",
      "type": "",
      "value": null,
      "score": null,
      "passed": null,
      "reason": "Scoring timed out: no progress for 10 minutes.",
      "status": "timed_out"
    }
  ]
}
```

Nothing to score: no evals apply, and CSAT had no transcript or recording.

```json
{
  "scoring_status": "not_applicable",
  "csat_status": "skipped",
  "csat_reason": "Nothing to score: the call has no transcript or recording.",
  "evaluations": []
}
```

The scoring counts for a run of seven calls with one still scoring (the other `summary` keys are left out here):

```json
{
  "summary": {
    "total": 7,
    "scoring": {
      "not_applicable": 2,
      "pending": 1,
      "succeeded": 1,
      "failed": 1,
      "timed_out": 2
    }
  }
}
```
