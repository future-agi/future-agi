Run it on your agent: `python3 server.py` then the drawer's `Test on one row` with `http://127.0.0.1:8787/reply`

# HTTP endpoint · Future AGI experiment example

`server.py` is a small stdlib HTTP agent that answers the drawer's HTTP body template (`body_template.json`, byte for byte the drawer's default for this task suite) with `{"reply": …}`; the response path is `$.reply`. It sends CORS headers so the drawer's `Test on one row` (your browser calls the URL) works on localhost.

## What each part does

| Part                                      | What happens                                                                                                                                                                                                                                     |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| URL, method, body template, output path   | Every try sends the request the drawer's preview shows for that row; the output path (or an output template over several paths) is the run output the evals score                                                                                |
| Headers from a workspace secret           | Future AGI adds the value when it calls your endpoint. A browser Test to localhost cannot read secrets, so it sends the request without them and says which headers it left out                                                                  |
| A secret name the workspace does not have | Test and Run are blocked: "No workspace secret named … Pick one from the list."                                                                                                                                                                  |
| `Test on one row`                         | Sends exactly what the run's try 1 sends for that row                                                                                                                                                                                            |
| `traceparent`                             | Every try sends a W3C `traceparent` (its trace and root span). This server echoes it in a `traceparent` response header and in the reply, and the Test result shows both                                                                         |
| Response · tool calls and usage           | Paths in the response (for example `$.actions`, `$.usage.input_tokens`): each try's trace records the tool calls and usage it read                                                                                                               |
| `--verify-hmac SECRET`                    | For a sender that signs its requests: the server answers 401 unless `X-FutureAGI-Signature: t=<unix>,v1=<hex HMAC-SHA256 of "<t>.<body>">` verifies with SECRET (signatures older than 5 minutes are refused). The drawer does not sign requests |

## Dry run (stdlib only)

```bash
python3 -I server.py --dry-run                       # sample_rows.jsonl through reply(), no socket
python3 -I server.py --dry-run --verify-hmac s3cret  # also signs each body and checks verify() accepts it and refuses a changed body
```

## Time budget

Measured 2026-10-23 on macOS 26.5 · Apple silicon · Python 3.12 (Homebrew).

| Step                       | Time                                   |
| -------------------------- | -------------------------------------- |
| `server.py --dry-run`      | 60 ms                                  |
| install                    | none (stdlib)                          |
| a full run from Future AGI | not measured: needs a public https URL |
