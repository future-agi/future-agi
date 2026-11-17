# Remote scorer · Future AGI eval example

`server.py` is a stdlib scorer for a **Remote scorer** eval. Future AGI sends it each row and the output, signed, and stores its answer as the eval's score. It checks the signature, rejects a timestamp older than 5 minutes, accepts either signature while a secret rotates, and scores by one documented rule: an order id like `A-1234` in the output gives `0.7`, anything else `0.2`.

## Run it

```bash
python3 -I server.py --secret <the secret you copied>             # http://127.0.0.1:8787/score
python3 -I server.py --secret <new> --secret <old>                # while a secret rotates
python3 -I server.py --secret <secret> --fail-first 2             # two 503s, then answers
python3 -I server.py --secret <secret> --answer 1.4               # an answer Future AGI rejects
```

Then Evals › your remote scorer › **Test on one row** with `http://127.0.0.1:8787/score` (called from your browser). Runs need an https URL Future AGI can reach.

## The request

`POST <your URL>` with:

| Header                  | Value                                                                                            |
| ----------------------- | ------------------------------------------------------------------------------------------------ |
| `Content-Type`          | `application/json`                                                                               |
| `X-FutureAGI-Event`     | `eval.score`                                                                                     |
| `X-FutureAGI-Delivery`  | one id per (scorer version, run, row, the run's try), the same on every HTTP retry: dedupe on it |
| `X-FutureAGI-Signature` | `t=<unix>,v1=<hex>`: HMAC-SHA256 of `"<t>.<raw body>"` with your signing secret                  |
| `Authorization`         | `Bearer …` instead of the signature, for a bearer or OAuth scorer                                |

`t` is taken on each try, so a retry carries a fresh one. A call that failed (an error status, no answer in time, an answer that is not valid) is never kept as your answer: the next Score a run asks again under the same delivery id, so dedupe on the id only once you have answered it. Each **Test on one row** is its own delivery. After **Rotate secret**, Future AGI signs with the new and the old secret for 24 hours: the header carries two `v1` values, and either one matching is enough.

The body (`sample_request.json`):

```json
{"scorer": {"id", "name", "version"}, "run": {"id", "label"} | null, "row": {"id", "label"},
 "inputs": {"<name>": "<column value>"}, "output": "<text>", "trace_id": "<id>" | null}
```

With **Send: the row, the output and the trace steps**, the body also has `steps: {span_type: [...], name: [...], input: [...], output: [...]}`, index-aligned with `null` kept.

## The answer

`200` with `{"score": <number>, "passed"?: <bool>, "reason"?: <text>, "label"?: <text>}`, checked against what the scorer answers by the platform's one score check (the same rule an uploaded score meets); an answer that fails it is kept as a **Score error** cell with the reason, never coerced into a score:

| Answers      | Valid                                  | Stored as a score error otherwise      |
| ------------ | -------------------------------------- | -------------------------------------- |
| Score 0 to 1 | `0 ≤ score ≤ 1`                        | `score error: 1.4 > 1`                 |
| Pass / fail  | `passed` true/false, or `score` 0 or 1 | `score error: 0.4 is not pass or fail` |
| Choice       | `label` is one of the scorer's choices | `score error: "maybe" is not a choice` |
| any          | the field is there                     | `score error: no score in the answer`  |

A non-2xx answer reads `HTTP 503 from <host>` on the cell, no answer in the timeout reads `No answer from <host> in 15 s`, and a connection that fails reads `Couldn't reach <url>`. These cells count as failed, never as scored: the run note reads `18 cells scored by <host>, 2 failed: HTTP 503 from <host>`. Retries (0 to 3, your choice) happen only on the classes you pick (429, 5xx, no answer in time); the wait is `Retry-After` when you send one, else 1 s, 2 s, 4 s. A `413` is never retried. The timeout is 5 to 30 s, 15 s unless you set it.

## What each part does

| Part                                                                                        | Status                                                                           |
| ------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| Signature check, 5-minute window, either `v1` during a rotation                             | Runs as written                                                                  |
| The scoring rule (`A-\d{4}` → 0.7, else 0.2)                                                | Runs as written                                                                  |
| `--answer 1.4` (shows a score error), `--fail-first 2` (two 503s, so the third try answers) | Runs as written                                                                  |
| CORS for `Test on one row` from your browser on localhost                                   | Runs as written                                                                  |
| Runs on Future AGI                                                                          | Needs an https URL Future AGI can reach: private and local addresses are blocked |

## Self-test (stdlib only)

```bash
python3 -I server.py --self-test   # good signature, rotation, tampered body, stale t
```

## Time budget

| Where                                                           | Step                    | Time                                   |
| --------------------------------------------------------------- | ----------------------- | -------------------------------------- |
| macOS 26.5 · Apple silicon · Python 3.14 (2026-10-23)           | `server.py --self-test` | 690 ms                                 |
| Linux, 8 vCPU (GCP n2d-standard-8) · Python 3.12.3 (2026-10-23) | `server.py --self-test` | 580 ms                                 |
| any                                                             | install                 | none (stdlib)                          |
| any                                                             | a run from Future AGI   | not measured: needs a public https URL |
