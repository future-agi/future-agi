# Handoff: simulation "Debug failures" (Omega)

Updated 2026-09-28. Built for the cab pilot. This file is meant for whoever (and whatever agent)
picks this up next. It is harness-neutral: every step is a plain shell command or a file path.
Delete this file before the branch goes up for review.

## 1. Current state

- The feature works end to end on a local stack. It was re-verified on 2026-09-28 against a real
  50-call run (numbers in §4).
- All code is pushed. **No PR is open for either feature branch, and nothing is merged.**
- Still to do. This is more than testing:
  1. End-to-end test on a **fresh** simulation run (§5.1).
  2. Pre-PR chores: bring the branch up to its base, fold migrations, regenerate contracts, open
     PRs (§5.2).
  3. The simulator PIN-loop fix in ALK exists only on one laptop. Without it, fresh runs are mostly
     garbage (§5.3).
  4. Latency and silence into Omega: the direction is decided, nothing is built (§5.4).
  5. Product decisions that are Kartik's call (§5.5), and a prod gateway timeout that needs
     authorization (§5.6).

## 2. Branches

| Repo                                 | Branch                                   | Head        | Base                  | State                                                  |
| ------------------------------------ | ---------------------------------------- | ----------- | --------------------- | ------------------------------------------------------ |
| `future-agi/future-agi`              | `feat/simulation-debug-analysis-dev`     | `90367c9fc` | `feat/environment-v3` | pushed · **598 commits behind base** · no PR           |
| `future-agi/omega-error-feed-worker` | `feat/TH-8054-simulation-omega`          | `e9be2cb`   | `main`                | pushed · 3 behind `main` · no PR                       |
| `future-agi/future-agi`              | `fix/TH-8068-run-group-by-scenario-axes` | `0fa935eb6` | `feat/environment-v3` | PR #3074 open, mergeable · `gate` check red (see §5.2) |
| `future-agi/agent-learning-kit`      | none                                     | none        | `feat/environment-v3` | PIN-loop fix **uncommitted, local only** (§5.3)        |

Feature commits on the future-agi branch (the merge commits are omitted):

```
bea498afb feat: add execution-scoped simulation debug analysis
ba3a5bfe0 fix(simulate): group run calls by the Scenarios tab's axes      <- same diff as PR #3074
4eab6f11b feat(simulate): explain a run's failures goal by goal
ec7d77d05 fix(simulate): read a call again once when its first analysis fails
90367c9fc feat(simulate): hide the unread and errored call lines in the debug drawer
```

Omega commits: `c2ad413 feat: investigate simulation test executions with Omega` and
`e9be2cb feat(simulation): investigate each call against its authored goals`.

## 3. What it is, and the rules it follows

The run page's **Debug failures** button opens a drawer. The drawer lists the goals the run's evals
say broke, the ways each one broke (as Omega explained them, grouped by F6), and one-off agent
issues.

Kartik set these rules. They are binding, so don't relitigate them:

1. **One Omega job per call.** A call is treated like a trace. A whole run is never read in one
   pass: that measured badly, because the model stops at the first failure or compacts and cites
   one example.
2. **Clustering scope is one run**, not an environment.
3. **Evals are the source of truth.**
   - A goal is broken on a call only when the harness says so: the receipt has
     `sub_goals[].held is False`.
   - Omega explains _how_ a goal broke. It never decides _whether_.
   - A finding filed under a goal the eval passed is hidden.
   - There is no "missed by your evals".
4. **Simulator (our caller) faults are ours** and are never shown as the customer agent's issues.
5. **Self Improvement is beta.** The CTA is disabled, carries a BETA chip, and has the tooltip "In
   beta, send early access request". The Trials tab is gated the same way, and it only renders when
   the run has optimization runs.
6. **The pilot UI hides** example quotes (audio transcription is unreliable), any "problem from our
   side" text, and the errored/unanalysed call lines. The API still returns
   `excluded_call_ids`/`unanalyzed_call_ids`.

### Flow

1. `POST /simulate/test-executions/{id}/debug-analysis/`
   - The body must be `{}`: the FE contract layer rejects anything else before sending.
   - `ensure_simulation_investigation` creates, or re-arms, one `TraceInvestigationJob` per call.
     The job has a `call_execution` FK.
2. The Omega investigator daemon claims a job, then reads its evidence from
   `GET internal/error-feed-v2/attempts/{attempt_id}/simulation-evidence/`.
   - The evidence is one call: its transcript with per-turn times, its authored goals, and the
     run's eval verdicts.
   - Omega uses sub-goal names verbatim as `requirement_id`.
3. Omega publishes a report. In `publish_investigation`, if that call's **first** read ended unread
   (failed or lease-expired), `retry_unread_call_once` re-arms it once. A repeat failure stays
   unread until someone retries manually.
4. F6 grouping.
   - Grouping work for a simulation report is **gated until the run settles**: every Omega job and
     every feature job must be done.
   - The gate is `_simulation_run_settling` in `tracer/services/grouping/control.py`. It pushes
     `not_before` back by 15 s.
   - Once the run has settled, every pending work is set due now, so a single claim takes the
     whole run as one cohort.
5. `GET …/debug-analysis/` runs `debug_analysis_state`, which calls `build_diagnosis` and returns
   `{summary, goals[], one_offs[]}`. Every count is a count of calls from eval verdicts or cluster
   sizes, never a model number.
6. FE:
   - The drawer shows one `GoalCard` per broken goal, then an "Also seen" list.
   - "View N calls" filters the run table server-side (`filters.call_execution_id`) and shows an
     "N affected calls" chip. The status-chip facets are scoped to those calls.

### Code map

| Where                                                                                  | What                                                                                                                              |
| -------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `futureagi/tracer/services/simulation_investigation.py`                                | jobs per call, `_unread`/`_rearm`/`retry_unread_call_once`, `debug_analysis_state`, `simulation_evidence_page`, `_authored_goals` |
| `futureagi/tracer/services/simulation_diagnosis.py`                                    | `build_diagnosis`: goals, ways, one-offs; caller-fault regex; errored calls via `call_outcome`                                    |
| `futureagi/tracer/services/trace_investigation.py`                                     | `publish_investigation`, which calls the one-shot retry                                                                           |
| `futureagi/tracer/services/grouping/control.py`                                        | `claim_grouping_work` settle gate, `SIMULATION_SETTLE_SECONDS`                                                                    |
| `futureagi/simulate/views/debug_analysis.py`, `simulate/serializers/test_execution.py` | the API and its serializers                                                                                                       |
| `futureagi/simulate/views/run_results_v3.py`                                           | facets scoped by a `call_execution_id` filter                                                                                     |
| `futureagi/tracer/migrations/0108`–`0110`                                              | the new job/report shape (fold them, see §5.2)                                                                                    |
| `frontend/src/sections/simulate/environments/workspace/runs/detail/fixmyagent/`        | `DiagnosisPane`, `GoalCard`, `FixMyAgentDrawer`, `BetaChip`, `selfImprovement.js`                                                 |
| `frontend/src/api/simulate-environments/debugAnalysis.js`                              | API hook and mapper                                                                                                               |
| `frontend/.../runs/detail/trace/RunTraceTable.jsx`, `RunDetail.jsx`                    | affected-calls filter; Trials tab gate                                                                                            |
| Omega `workers/error-feed-node/simulation-evidence.mjs`                                | strict evidence schema (`exactKeys`); optional `evaluations`, `goals`                                                             |
| Omega `workers/error-feed-node/investigation.mjs`                                      | prompt; the goals rule is at line ~42                                                                                             |
| `futureagi/tracer/tests/test_simulation_investigation.py`                              | the main test file (diagnosis, retry, grouping gate, caller faults)                                                               |

## 4. Verified on 2026-09-28 (local stack)

- **Backend: 199 passed, 1 skipped.** The run covered `tracer/tests/test_simulation_investigation.py`,
  `tracer/tests/test_grouping_*.py`, `tracer/tests/test_trace_investigation_*.py`,
  `tracer/tests/test_investigation_error_envelope.py`, `tracer/tests/test_feed_investigation_reel.py`
  and `simulate/tests/test_analytics_functional.py`.
- **Frontend:** 336/336 vitest for `runs/detail` and `api/simulate-environments`. eslint reported 0
  warnings, and `contracts:check` is clean.
- **Live run `4683acb5-9af1-4ee8-b6a2-4bb8edafadff` (50 calls).**
  - The drawer and the run page reconcile:
    - 39 measured + 11 errored = 50.
    - 11 broken calls = the "Failing 11" chip.
    - 28 Passing.
  - Unanalysed = 0: 16 jobs were read twice by the silent retry, and all 7 failed reads sit on
    errored calls.
  - Cards:
    - `exact_greeting`: 8 of 39 calls.
    - `full_address_retention`: 3 of 5.
    - 4 one-offs.
  - "View 8 calls" sends the server filter and scopes the chips to 8/8/0/0/0. Clearing it resets.
  - The beta gate and its tooltip render.
- **Omega:** the running investigator image is byte-identical to `e9be2cb` on its runtime files.
  Omega → gateway → Vertex (`gemini-3.8-flash`) returns 200 in about 3 s.
- **Not verified:** a fresh simulation run through the whole pipeline after these commits.

## 5. What's left

### 5.1 End-to-end test on a fresh run

Prerequisites: the §6 setup, **the §5.3 PIN fix in the E2B template**, and working evals. Check that
`eval_outputs` are non-null before trusting anything.

1. Run a hosted simulation of about 10–20 calls on the cab-booking env and wait for `completed`.
2. If any evals errored, rerun only the evals:
   `POST /simulate/test-executions/{id}/rerun-calls/` with
   `{"rerun_type":"eval_only","call_execution_ids":[…]}`.
3. Open the run page and click **Debug failures**, which POSTs `{}`. Watch the investigator logs
   (`omega_investigation_*` events).
4. Expect the following:
   - One job per call.
   - Failed first reads re-armed once.
   - Exactly one grouping attempt per run, claiming all reports with findings. Check with
     `TraceGroupingAttempt.claimed_work_ids`.
   - Drawer counts that reconcile with the run page, as in §4.
   - No card for a goal the evals passed.
   - No finding about our caller.
5. Check the edges:
   - A call that fails twice shows as unread only in the API: `summary.unanalyzed_call_ids`.
   - The full-failure screen still offers Try again.
   - The Trials tab appears (disabled, BETA) only after an optimization run exists.

### 5.2 Before opening PRs

Nothing below has been done. Kartik asked for no merges in this handoff.

1. **Merge `origin/feat/environment-v3` into the branch** (it is 598 commits behind). A dry run
   (`git merge-tree`) predicts conflicts in 9 files:
   - Regenerate these four; never hand-merge them:
     - `api_contracts/openapi/management-api-contract-debt.generated.json`
     - `api_contracts/openapi/runtime-management-api-contract-debt.generated.json`
     - `frontend/src/api/contracts/api-surface.generated.js`
     - `frontend/src/api/contracts/openapi-contract.generated.js`
   - Merge these by hand:
     - `…/runs/detail/RunDetail.jsx` and `__tests__/RunDetail.test.jsx`
     - `…/fixmyagent/DiagnosisPane.jsx`
     - `…/trace/RunTraceTable.jsx` and `trace/__tests__/RunTraceTable.test.jsx`
2. **Fold tracer migrations `0109_simulation_debug_analysis_per_call` and
   `0110_simulation_current_report_per_call` into `0108_simulation_debug_analysis`.** None of the
   three is merged anywhere, and the base's tracer migrations stop at `0107`.
3. Regenerate contracts and run the full chain, then `contracts:check`. See §6 for the commands.
4. Rerun the §4 test set.
5. **PR #3074 should land first.** This branch carries the same diff as `ba3a5bfe0`.
   - Its `gate` check fails on the repo's admission policy, and was already failing before the
     merge fix:
     - it has no linked issue;
     - it targets `feat/environment-v3`;
     - its body's test-count claim doesn't trace to the diff.
   - It needs a body edit and/or a maintainer override.
6. Omega: update the branch from `main` (3 behind), then open a PR to `main`.
7. PR titles must be conventional commits, e.g. `feat(simulate): …`, because release-please reads
   them.
8. **Never commit the local-only hacks** that sit dirty in Kartik's working tree:
   - `agentcc-gateway/internal/providers/gemini/gemini.go` (ProxyFromEnvironment)
   - `futureagi/model_hub/utils/utils.py` (NLTK)
   - `futureagi/simulate/services/hosted_sandbox/e2b.py` (HTTPS_PROXY)
   - about 69 Prettier-only FE files
   - `docker-compose.local.yml`
   - anything under `futureagi/ee/cloud/`

### 5.3 ALK simulator PIN loop: the fix is local only

- **Where:** `src/fi/simulate/simulation/engines/livekit.py::_pinned_credential_reply` in
  `agent-learning-kit` (from commits `150d82c6`/`8ca77c71` on `feat/environment-v3`).
- **Bug:** the caller bypassed its LLM and read out the PIN whenever the agent's line _contained_
  "pin" as a substring.
  - Result: 44/49 calls looped on "Seven six eight two" and hung up, so scenarios never played out.
- **Local fix:**
  - Match "pin" as a whole word.
  - Repeat the PIN only if it hasn't been given yet, or if the agent says it didn't get it.
  - Tests are in `tests/runtime/test_livekit_engine.py`. Run them with `uv run --extra livekit`.
- **Its only copy is uncommitted changes on Kartik's laptop** (4 files; the other two changes are
  an authoring-credential fallback in `harness/backends/vertex_gemini.py` and
  `Dockerfile.hosted`).
- The hosted E2B template must be rebuilt with the fix. Kartik's template reference is in his
  `.env` (`ALK_E2B_TEMPLATE_REFERENCE`/`_BUILD_ID`/`HARNESS_PARALLEL_SNAPSHOT_DIGESTS`).
- Earlier Kartik chose not to PR this. For anyone else to run §5.1, it has to reach a branch.
  **Ask Kartik.**

### 5.4 Latency and silence into Omega (decided, not built)

**PM feedback:**

- The agent takes 5–10 s to open, with silence meanwhile.
- Turn latency is abnormally high.
- The drawer shows none of it.

**Why the drawer shows none of it:** only eval-broken goals and Omega's explanations reach the
drawer. No eval judges timing, and Omega was never pointed at timing.

**Kartik's decision:** don't hard-code thresholds. "Let Omega decide… pass all the attributes, let
Omega read it."

**Measured on run `4683acb5`:**

| Signal                                    | Value                                                     | Source                                          |
| ----------------------------------------- | --------------------------------------------------------- | ----------------------------------------------- |
| Agent turn latency, per-call average      | 1.7–6.2 s (median about 2.1 s)                            | `CallExecution.avg_agent_latency_ms`            |
| Worst single agent gap                    | 11–11.5 s on calls 707e2597, 16cf10da, 10d2db6c, 9699e6e6 | transcript gaps                                 |
| First agent word after `started_at`       | 8.7–22.4 s (median 10.4 s), all 42 calls over 5 s         | raw ALK transcript                              |
| Recording start after `started_at`        | about 8 s (both side recordings have equal length)        | recording length vs duration                    |
| Agent's first sound after recording start | 2.8–7.7 s (8-call sample)                                 | `ffmpeg silencedetect` on `recording_assistant` |
| **Our** caller answering the greeting     | about 4.1 s on almost every call; median 2.4 s            | raw transcript `e2e_latency` (user turns)       |

Why those numbers need care:

- **Opening silence is invisible today.** `CallTranscript.start_time_ms` is re-zeroed at the first
  utterance, so every transcript starts at 0.
- **The raw ALK transcript keeps what ingestion throws away.** The artifact
  `hosted_harness_artifacts.transcript` (schema `futureagi.call-transcript.v1`) has absolute
  `started_speaking_at`/`stopped_speaking_at`, `e2e_latency` and `interrupted` for each turn.
  Ingestion drops all four.
- **`started_at` can't be used as "call connected".** ALK stamps it before spawning the call-worker
  process (`src/fi/alk/harness/call_runner.py` around line 1276). So the roughly 8 s before
  recording is our process start, room creation and agent dispatch.
- **ALK doesn't timestamp when their agent joined.** It logs `livekit_target_joined` but records
  no time. Without that time, the agent's share of the opening silence can't be separated from
  ours. That's rule 4.

**Proposed plan.** Kartik hadn't said yes to or trimmed this; confirm with him before building:

1. **Backend.** In `simulation_evidence_page`, add an `attributes` block to each call. It holds:

   - Per-turn timing from the raw ALK transcript (absolute started/stopped speaking, `e2e_latency`,
     `interrupted`).
   - `started_at`/`ended_at`/`duration_seconds`.
   - `avg_agent_latency_ms`.
   - User and AI interruption counts and rates, and `avg_stop_time_after_interruption_ms`.
   - `user_wpm`, `bot_wpm` and `talk_ratio`.
   - `conversation_metrics_data`.
   - Our simulator's settings from `call_metadata` (`conversation_speed`,
     `interrupt_sensitivity`, `finished_speaking_sensitivity`, `initial_message`,
     `initial_message_delay`).
   - The agent's prompt.

   Put `attributes` before `transcript`, because Omega reads the call JSON as raw byte ranges, in
   order.

2. **Omega worker.**
   - Add `attributes` to `optionalCallKeys`, with a bounded validator, in
     `simulation-evidence.mjs`. Evidence validation is strict, so an unknown key is rejected.
   - Add one prompt line in `investigation.mjs`: "judge responsiveness as a caller would hear it
     (opening delay, turn latency, dead air, interruptions); delays on the simulated caller's side
     are ours, never the agent's".
   - Rebuild the image (§6).
3. **ALK.** Record wall-clock times for these four events in the transcript artifact or the
   receipt:

   - our caller joined;
   - their agent joined (`livekit_target_joined`);
   - their audio track subscribed;
   - recording started.

   Then the agent's opening delay = their first `started_speaking_at` − their join time. **Open:**
   PR, or local like §5.3? Ask Kartik.

4. **Drawer.** Timing issues have no authored goal, so they fall into "Also seen, 1 call each".
   That heading is wrong once F6 clusters one issue across many calls. Change it to an "Other
   issues" section with count rows. `build_diagnosis` already groups one-offs by cluster.

**Risks to measure, not guess:**

- A bigger evidence payload per call may exhaust Omega's per-call read budget. The earlier
  `read_complete=false` problem came from this. After building, re-read a 40+ call run and check
  that every call reads completely.
- `_CALLER_FAULT` in `simulation_diagnosis.py` drops any finding whose statement says "simulated
  caller" or similar. A real agent-latency finding worded as "…while the simulated caller waited"
  would be dropped too.
- `inspect_audio` exists in Omega but is disabled for simulation (`investigation.mjs` about line
  181). It returns fallible model observations. Measured timings are better evidence for
  durations.

### 5.5 Product decisions pending (Kartik)

- **The greeting card is eval noise.**
  - Every call opens with a branded "this call is being recorded" line from the phone layer;
    it isn't in the agent prompt.
  - The `exact_greeting` judge flips on word-identical openings: 29 pass / 10 fail on run
    `a047827f` and 29 / 3 on `4683acb5`. The drawer currently shows 8 of 39.
  - Fix: one line in the environment's `exact_greeting` criterion allowing a recording disclosure,
    then an eval-only rerun. The card then disappears.
- **"Passes when …" confused the PM.** It's the eval criterion (`criteria` from the sub-goal
  catalogue `claim`), printed verbatim.
  - Proposed copy: "Should: …".
  - Way titles should come from the specific ways, not restate the goal.
- **F6 isn't stable across re-reads.**
  - On 2026-09-26 the greeting split into two clusters: "mutates wording" (`448a70da`) and
    "prepends recording disclaimer" (`046c67ff`).
  - After the silent-retry re-reads, F6 regrouped into one generic 8-call cluster, `3bdfd16d`
    "Opening turn deviates from mandatory verbatim greeting requirement". The old clusters are
    soft-deleted.
  - The drawer lost the useful distinction.
- **F6 deferral is terminal.** Nothing re-offers a `deferred` finding, and that's also true for the
  live trace feed. Not fixed.
- **Header wording.** "11 failing of 39 measured": keep "measured" or not?
- **Omega burns a job on every errored (infra-failed) call.** It always fails and is never shown.
  Skip those calls?
- **A read that succeeds after its lease expired is thrown away** (it counts as unread, then gets
  retried). Should it be accepted instead?

### 5.6 Prod gateway timeout (needs explicit authorization for any prod change)

- `agentcc-gateway/internal/providers/gemini/gemini.go` (about lines 32–34) falls back to a
  **hard-coded 120 s** when a provider block has no `default_timeout`. The gateway-wide 300 s never
  reaches provider clients.
- Omega's thinking calls take 65–130 s, so some of them 502 as `gateway_upstream_error`.
- The local fix is `default_timeout: 180s` under `vertex_ai_global` in the git-ignored gateway
  config. With it, zero timeouts since.
- Prod US and EU configmaps (`agentcc-gateway-cm.yaml` in the deployment repo) have the same gap.
- Options (both are Kartik's call):
  - a deployment PR adding `default_timeout: 180s`;
  - a code fix so a provider without `default_timeout` inherits the gateway's request or
    per-attempt timeout.

### 5.7 Adjacent issues noticed, not chased

- Every 15 s the gateway's org-config sync gets a 400 from `backend:80/agentcc/org-configs/bulk/`:
  `alerting.rules[].enabled/severity` is rejected (extra forbidden).
- The FE contract warning on `GET /simulate/v3/test-executions/{id}/calls/` (`RunCallsV3Response`):
  `evaluations[].value` is typed as an object but arrives as a string or number, and
  `error_message` arrives as `""`. This predates the branch.
- `ws://localhost:8000/ws/connect/` fails locally.
- The run header renders "Run 4 · agent —".
- One one-off is titled only "Omitted information". That's the finding-kind fallback when a cluster
  has no title.
- `property-catalog-supervisor` crash-loops because the image lacks `clickhouse-cityhash`. Its
  rootfs is read-only, so pip can't fix it; stop it locally.

## 6. Local setup and commands

The stack comes from the root compose files. Container names are `futureagi-<svc>-1`, and the
backend is at `backend:80` inside the network.

- **Gateway config (git-ignored overlay):**
  - `vertex_ai/gemini-3.8-flash` must be listed under a `vertex_ai_global` provider, because Gemini
    3.x is only available on the global endpoint.
  - Set `default_timeout: 180s` on that provider (§5.6).
  - Restart the gateway after any change.
- **Evals:** route every `turing_*`/`falcon_*` model through `vertex_ai_global`:

  - `FALCON_AI`/`FALCON_MULTIMODAL`/`TURING_LARGE(_XL)` = `vertex_ai/gemini-3.7-flash`
  - `TURING_SMALL` = `gemini-3.5-flash-lite`
  - `TURING_FLASH` = `gemini-3.1-flash-lite`

  A regional `vertex_ai` with `GOOGLE_CLOUD_LOCATION=global` 404s silently.

- **Grouping:**
  - Set `ERROR_FEED_GROUPING_PROJECT_BUDGET_USD`, `…_WORK_BUDGET_USD` and `…_TENANT_BUDGET_USD` to
    `10`. The default is `0`, which silently disables grouping.
  - Provision the grouping CH tables once, in the backend container:
    `SERVICE_TYPE=bootstrap STARTUP_DB_MUTATION_MODE=operator python manage.py provision_grouping_features --apply`.
  - Embeddings go through `serving` (`all-MiniLM-L6-v2` from HF, cached in a volume).
- **Omega image:** run this from the omega repo root:
  ```sh
  NODE_AUTH_TOKEN="$(gh auth token)" node workers/error-feed-node/fetch-runtime.mjs
  node workers/error-feed-node/prepare-image.mjs
  docker buildx build --load -t omega-error-feed:<tag> .artifacts/node-worker
  ```
- **Omega containers:** attach them to the `futureagi_default` network with restart
  `unless-stopped`, and mount a secrets volume at `/run/omega-secrets` holding three files:

  - `gateway-key`: a gateway API key.
  - `internal-api-key`: must equal the backend's `INTERNAL_API_SECRET`.
  - `clickhouse-password`: for a CH user that neither repo provisions; locally it's
    `omega_error_feed_local`.

  Investigator: `node worker/daemon.mjs`, with these environment variables:

  - `OMEGA_DJANGO_URL=http://backend:80`
  - `AGENTCC_BASE_URL=http://agentcc-gateway:8090/v1`
  - `OMEGA_MODEL_ID=vertex_ai/gemini-3.8-flash`
  - `OMEGA_CONCURRENCY=4` (8 OOMs a 10 GiB VM)
  - `OMEGA_CLICKHOUSE_URL=http://clickhouse:8123`
  - `OMEGA_CLICKHOUSE_DATABASE=default`
  - `OMEGA_KAFKA_BROKERS=property-catalog-kafka:9092`. **Omega needs this Kafka; don't stop it.**
  - `*_FILE` variables pointing at the secrets.

  Grouping: `node worker/grouping-daemon.mjs`, with these environment variables:

  - `GROUPING_MODEL_ID=vertex_ai/gemini-3.8-flash`
  - `GROUPING_EMBEDDING_URL=http://serving:8080/model/v1/embed`
  - `GROUPING_CALL_RESERVATION_USD=0.25`
  - the same Django, gateway and secret variables.

- **Hosted runs:**
  - The E2B sandbox reaches the local backend and gateway through one ngrok tunnel, configured by
    `HARNESS_PUBLIC_BASE_URL`, `ALK_HOSTED_AGENTCC_BASE_URL` and `AGENTCC_BASE_URL` in `.env`. If
    the ngrok URL changes, update all three and restart with `down && up`, not `restart`.
  - Keep `ALK_E2B_MAX_TTL_SECONDS` high enough for the call count. 7200 caps runs at about 20
    calls; 21600 works for 50.
  - `HARNESS_MAX_WORLD_SLOTS=4`.
- **Traps that each cost time:**
  - The dev backend image lacks `clickhouse-cityhash`. Every CH client fails until you run
    `pip install clickhouse-cityhash==1.0.2.5` in the backend and worker containers. The install
    is lost whenever compose recreates a container.
  - Bind-mounted code doesn't hot-reload under Colima; restart the container after edits.
  - The backend container refuses `manage.py shell`. Run `django.setup()` scripts in the worker
    container.
  - Never name a scratch script after a stdlib module (`grp.py`, `attr.py`).
  - A FAILED grouping feature job is terminal. Re-arm it by resetting state and `attempt_number`.
  - Omega's failure reasons appear only in its container logs. The report's `error_message` is
    empty.
- **Tests:**

  ```sh
  # backend (needs ClickHouse; run inside the backend container with the CH env redirected)
  docker exec -w /app/backend -e DJANGO_SETTINGS_MODULE=tfc.settings.test \
    -e CH_HOST=clickhouse -e CH_PORT=9000 -e CH25_HOST=clickhouse -e CH25_HTTP_PORT=8123 -e CH25_TCP_PORT=9000 \
    -e CH_DATABASE=test_tfc -e CH25_DATABASE=test_tfc -e FI_ALLOW_NONLOCAL_CH25_TEST_MUTATIONS=true \
    -e REDIS_URL=redis://redis:6379/0 -e REDIS_LOCK_URL=redis://redis:6379/2 \
    futureagi-backend-1 sh -c 'python -m pytest tracer/tests/test_simulation_investigation.py \
      tracer/tests/test_grouping_*.py tracer/tests/test_trace_investigation_*.py \
      tracer/tests/test_investigation_error_envelope.py tracer/tests/test_feed_investigation_reel.py \
      simulate/tests/test_analytics_functional.py --reuse-db -q -p no:randomly'

  # frontend (node 22)
  cd frontend && npx vitest run src/sections/simulate/environments/workspace/runs/detail src/api/simulate-environments
  D=src/sections/simulate/environments/workspace/runs/detail
  npx eslint --max-warnings 0 $D/fixmyagent $D/trace $D/RunDetail.jsx $D/__tests__/RunDetail.test.jsx \
    src/api/simulate-environments/debugAnalysis.js src/api/simulate-environments/__tests__/debugAnalysis.test.jsx

  # contracts (after any serializer change): swagger first, then the full chain, then check
  UV_PYTHON=3.13 scripts/generate-openapi-schema.sh
  yarn --cwd frontend contracts:generate && yarn --cwd frontend contracts:check

  # commits: lint-staged's `uv run --with ruff` can hang offline; UV_OFFLINE=1 git commit avoids it
  ```
