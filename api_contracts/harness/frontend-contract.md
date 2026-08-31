# Harness API — frontend contract (v0)

How the frontend talks to the simulation harness. The harness is an independent
container (like agentcc-gateway); the frontend never reaches it directly. All
calls go to the Django backend, which authenticates them and proxies to the
harness verbatim.

```
frontend ──(normal backend auth: same JWT/cookie as every /simulate/ call)──▶
  Django  /simulate/harness/<path>  ──(internal network)──▶  harness  /api/<path>
```

Base path: **`/simulate/harness/`**. Everything after it maps 1:1 onto the
harness's `/api/` routes. Example: `GET /simulate/harness/sessions` →
harness `GET /api/sessions`.

## The frontend calls the backend only

The frontend never talks to the harness directly — not in production, not in
local dev. The harness port (8777) is not published outside the compose
network, on purpose: the harness has no auth, and the backend is its only
door. All frontend calls go to `/simulate/harness/<path>` with standard
backend auth.

Local dev setup: run the stack (backend + harness), and give the Vite dev
server a proxy entry so the relative base reaches the backend:

```js
server: { proxy: { "/simulate": "http://localhost:8000" } }
```

`VITE_ALK_API_BASE` (direct harness base) is only for working inside the
harness repo itself with a self-run server — never for the platform frontend,
and any code path that skips auth for an absolute base should be removed.

## Global behaviour

- **One open session at a time.** The harness holds a single active
  conversation. Opening/creating/deleting a session while work is running
  returns **409** `{"error": "still working on the last thing"}`.
- **`busy`** in the status object is the source of truth for "can I send
  something right now". A page refresh must re-fetch status.
- Errors are `{"error": "<message>"}` with 400/404/409.
- Two endpoints stream **SSE** (`text/event-stream`) over **POST** — use
  `fetch` + ReadableStream, not `EventSource` (which is GET-only).

## The status object

Returned by `GET status`, by every session mutation, and as the final SSE
event of every stream.

```jsonc
{
  "session": {            // null when no session is open
    "id": "…", "agent": "…", "source": "…", "kind": "repo",
    "created": 1755500000.0, "updated": 1755500100.0,   // epoch floats
    "stage": "…", "title": "…"
  },
  "stage": "understand",  // current stage name, "" if none
  "stages": {             // every stage → why it cannot be opened; "" = openable
    "reception": "",
    "understand": "",
    "build": "needs a contract first",
    "scenarios": "needs a built environment first",
    "run": "needs scenarios first"
  },
  "agent": "…",
  "model": "claude-sonnet-4-6",
  "credentials": "…",      // one-line hint about which creds are active
  "spent_usd": 0.1234,
  "have": {                // what the session folder actually contains
    "contract": true, "world": false, "simulator_prompt": false,
    "sub_goals": 0, "scenarios": 0,   // counts, not booleans
    "validated": null, "runs": 0, "runs_passed": 0, "messages": 4
  },
  "out": "/app/artifacts/sessions/<id>",
  "busy": false,
  "run_test_id": "…",     // platform only — added by the backend proxy, null
  "execution_id": "…"     // when the session isn't linked. Absent locally.
}
```

The five stages, in order: `reception → understand → build → scenarios → run`.

**Platform navigation IDs.** The harness itself knows nothing about platform
entities. On the platform, `POST sessions` accepts optional `run_test_id` /
`execution_id`; the backend stores the link (stripping them before forwarding)
and adds both fields to every **non-streaming** response that carries a status
object (`GET status`, the session mutations) and to each entry in
`GET sessions` / `GET environments`. The **SSE-terminal `status` event is not
enriched** — the proxy streams it verbatim, so re-fetch `GET status` after a
stream ends if you need the IDs. Hitting the harness directly on localhost,
these fields are always absent — treat them as nullable.

## Endpoints

### Sessions

| Method & path | Body | Returns |
|---|---|---|
| `GET sessions` | — | `{"sessions": [{…meta, "has": {…}, "path": "…"}], "open": "<id>\|null"}` |
| `POST sessions` | `{"agent": ""}` (optional name) | status object. 409 if busy |
| `POST sessions/open` | `{"id": "<session-id>"}` | status object. 404 unknown id, 409 busy |
| `DELETE sessions/<id>` | — | status object. 404 unknown, 409 busy |
| `GET history` | — | `{"messages": [{"role": "you"\|"harness", "text": "…", "stage": "…", "at": <epoch float>, "tools": […]}]}` |
| `GET status` | — | status object |

### Driving the conversation

| Method & path | Body | Returns |
|---|---|---|
| `POST say` | `{"text": "…"}` — empty text advances to the next stage | **SSE stream** (below). 404 no session, 409 busy |
| `POST stage` | `{"stage": "reception"\|"understand"\|"build"\|"scenarios"\|"run"}` | status object. Opens a stage without starting it. 400/404/409 |
| `POST stop` | — | `{"stopped": true}` or `{"stopped": false, "why": "nothing is running"}` |

### Running scenarios

| Method & path | Body | Returns |
|---|---|---|
| `POST run` | `{"text": ""}` — or space-separated scenario names to run a subset | **SSE stream**. 400 if no contract/scenarios yet, 409 busy |

### Environments list (powers the RL-environments table view)

Cross-session, so it works without opening anything.

| Method & path | Returns |
|---|---|
| `GET environments` | `{"environments": […]}` — every session with at least a contract, newest first. `state` is `"building"` until the world is saved, then `"ready"` |

Each entry (cheap fields only — read off the folder, no world restore):

```jsonc
{
  "session_id": "…",
  "state": "ready",                      // or "building" — world not saved yet
  "agent": "…", "title": "…",           // from the session / contract
  "one_liner": "…",                      // from contract.json
  "created": 1755500000.0, "updated": 1755500100.0,
  "tools": 12,                           // count, from manifest.json
  "sub_goals": 9, "scenarios": 14,       // counts
  "runs": 3, "runs_passed": 2,           // simulation runs (the /simulations
                                         // list), not the legacy chat runs
  "run_test_id": "…", "execution_id": "…"  // platform only, nullable
}
```

Row click → `POST sessions/open` with the `session_id`, then the per-session
endpoints (`world`, `scenarios`, `simulations`…) serve the detail. The
agent-definition / scenario / persona tabs retire in favour of this view plus
the existing per-session endpoints.

### Reading what a session has produced

All of these read the **open** session; 404 or empty shapes when none is open.

| Method & path | Returns |
|---|---|
| `GET contract` | the contract JSON, or `{}` |
| `GET world` | `{"tables": [{"name","count","columns","rows"}], "tools": […], "tool_specs": […], "handlers": [{"name","source"}], "sequences": […], "notes": ""}`. Rows capped at 200 per table |
| `GET scenarios` | array of scenario objects: `{…scenario fields, "folder", "files": […], "checks": [{"name","settled_by": "code"\|"a judge","what","source"}], "gates": {…}, "validated": true\|false\|null, "why": ""}` |
| `GET scenario-file?name=<scenario>&path=<rel>` | `{"path", "source"}` — one file from a scenario folder |
| `GET subgoals` | `{"sub_goals": [{"name","what","settled_by","check","judged"}], "simulator_prompt": "…"}` |
| `GET runs` | legacy run list (chat path) |
| `GET simulations` | `{"runs": […]}` — every simulation run, newest first |
| `GET simulations/<run_id>` | one run in full: every scenario's verdict, conversation, tool calls. 404 unknown |
| `GET recording/<run_id>/<scenario>?track=<stereo\|combined\|caller\|agent>` | audio file (`audio/wav` etc.). Omit `track` for best available (stereo → combined → largest). 404 when none |

## SSE stream format

Both `POST say` and `POST run` emit events as:

```
data: {"kind": "…", "text": "…", "tool": "…", "detail": {…}}\n\n
```

| kind | meaning | detail |
|---|---|---|
| `text` | the harness speaking (assistant prose, streamed per chunk) | — |
| `tool` | a tool call started | `{"label", "target", "arguments"}` |
| `result` | that tool's result | `{"is_error": bool}` |
| `artifact` | a stage saved an artifact (contract, world, scenarios…) | `{"path": "…"}` |
| `exchange` | (`run` only) one line of simulated conversation | `{"speaker": "agent"\|"user"}` |
| `result_card` | (`run` only) one scenario finished | `{"scenario", "passed", "met", "of", "checkpoints": […], "ended", "turns", "calls", "transcript", "actions"}` |
| `done` | a stage's work ended | `{"outcome", "turns", "cost_usd", "error"?, "unexpected_model"?}`. Server-synthesized on interrupt/crash: `{"outcome": "stopped"\|"failed", "error"}` |
| `status` | **always the final event** — fresh status object | the status object |

Render `text` chunks as one message; a turn can speak once per stage it passes
through. The platform frontend's stream handling
(`frontend/src/api/al-environment/`) is the reference renderer.

## Building without credentials

Every GET endpoint serves artifacts already on disk — no model keys needed.
Finished sessions (incl. a full 30-scenario run) ship in `artifacts/sessions/`,
so nearly the whole UI can be built against real data. Keys are only needed to
trigger new work (`POST say` / `POST run`).

## Not in v0

- **Single-user.** Session state is one module-level global with one process
  lock; two concurrent users would move each other's session between turns.
  v0 ships as-is; multi-user needs a harness server change (open question with
  Karthik/Rishav). Design the FE so the open-session assumption is isolated.
- No workspace/project scoping — the harness is a single shared instance per
  stack. Scoping arrives with a later iteration.
- No pagination anywhere; `world` rows cap at 200 per table.
- The harness's own static UI (`GET /`) is not exposed.
