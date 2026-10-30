# TH-8899 Fix my agent: working notes

**Delete this file before the branch merges.** It keeps the state of the work in the branch, so
nothing lives only on one laptop. Last updated 2026-10-30.

## Branches

| Repo                    | Branch                             | Base             | Holds                                                                                                  |
| ----------------------- | ---------------------------------- | ---------------- | ------------------------------------------------------------------------------------------------------ |
| future-agi (public)     | `feat/TH-8899-fix-my-agent`        | `dev`            | Platform: fix runs, engine API, check runs, held-back split, GitHub pull request, launch options, read |
| future-agi (public)     | `feat/TH-8901-protected-scenarios` | the branch above | Protected cases (must still pass)                                                                      |
| omega-error-feed-worker | `feat/TH-8899-fix-my-agent`        | `dev`            | Engine `workers/agent-fix` and its E2B template build                                                  |
| omega-error-feed-worker | `feat/TH-8901-protected-scenarios` | the branch above | Engine side of protected cases                                                                         |
| ee                      | `feat/TH-8899-agent-fix-billing`   | `dev`            | `billing.yaml`: `agent_fix` engine calls bill AI credits                                               |

Not pushed, on purpose: `test/TH-8899-e2e` (a local merge for end-to-end runs) and
`test/TH-8955-ui-e2e` (the FE branch `feat/th-8955-improvements-page` merged with the stacked
branch, for UI testing only). The FE itself is on `feat/th-8955-improvements-page`.

Each platform fix goes on the base branch first and is cherry-picked to the stacked branch.

## Ship order

1. ee `billing.yaml` first. The usage consumer skips unknown types, so the platform without it
   bills nothing for the engine.
2. The platform and the engine template ship together. The template is pinned by env
   `AGENT_FIX_E2B_TEMPLATE_REFERENCE`, not by code. A template older than worker `6321d17` with this
   platform searches on the cases the read says are held back.
   - `agent-improvement:2e13bb44-02c7-4223-b661-dda87c7f8b7f` is built from worker `0a36d71`
     (stacked branch). The base branch head `0c3a639` has no template of its own yet.
3. The gateway that the engine reaches must serve the engine's model. The profile
   `workers/agent-fix/profiles/platform.json` pins `deepseek/deepseek-v4.1-flash` through
   OpenRouter. The dev box gateway had no OpenRouter provider and a dead key; fixed by hand on
   30 Oct. Prod: not checked. The OpenRouter key in use is the shared team key with a $300/month
   workspace budget.
4. Prod needs harness versioning and an ALK template that can certify (`--certify-only`). See
   TH-8957 and TH-8958.

## What is verified end to end

On the dev box, a throwaway backend from the stacked branch, its own database and Temporal
namespace, against a real GitHub repository through the GitHub App:

- An environment built from a GitHub agent (28 min).
- A push made a new agent version through the head observer, and the version kept the
  App installation (fix `0e03ca5d4`).
- A source run: 5 of 24 scenarios passed.
- launch-options, then a launch: 18 targets, 6 held back, 5 protected.
- Reproduce: 12 targets reproduced, 6 must-pass checks, baseline spread 0; baseline 14 of 72
  conversations passed.
- Diagnose, Propose and Screen after the gateway fix. On 30 Oct at 23:41 IST attempt 1 screened
  3 of 6 held-back cases passing.

Not yet verified: Verify, the winner, and the pull request against GitHub itself (the pull request
path is tested against a local bare repository only). Locally the run never got past the baseline:
the local sandbox TTL (`ALK_E2B_MAX_TTL_SECONDS=21600`) is shorter than a 72-conversation baseline.

Tests: 184 pass on the base branch and 187 on the stacked branch
(`simulate/tests/test_agent_fix_{services,api,sandbox,workflow}.py`). Engine: `node --test
workers/agent-fix/test/*.test.mjs`.

## Next: failures by owner, and a retry

Kartik's call on 30 Oct: our transient failures heal themselves; deterministic failures fail fast
and offer the user a retry; the checkpoint resume goes.

The facts it rests on:

- Any engine error ends the fix: `improve.mjs` writes "Failed for infrastructure reasons" and
  `finishedAt`, the engine reports the `failed` ending, and the platform closes the run. Since
  `302fbc242` such a run carries failure `fix_engine_failed` with fixed copy.
- A crash already relaunches (`AGENT_FIX_MAX_SANDBOX_ATTEMPTS=2`), at once, with no pause.
- The engine takes its launch number from the checkpoint, and attempt numbers run per launch
  (`launch * 100`). A checkpoint dropped after a failure in mid-search reuses attempt numbers: they
  collide with the `(fix_run, attempt)` constraint, and labels replay the old check runs.
  `start_check_run` replays a run with the same label whatever state it ended in.
- A checkpoint saved after the ending makes a relaunch resend that ending.
- Receipt failure domains seen on a dev database: `infrastructure`, `platform_sync`,
  `environment`, `simulator`, `agent`. The owner does not follow the domain:
  `platform_sync|usage_exhausted` is the user's plan limit.
- A receipt with domain `agent` counts as the agent failing every goal it left undecided. A
  customer key that dies inside their agent probably shows as `agent|target_agent_failed`, so
  every candidate is rejected and the fix ends "no improvement": a wrong verdict, not an error.

The plan under review:

| Class               | Examples                                                                                                                              | What happens                                                                              |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Ours, transient     | a check run failed `infrastructure` or `platform_sync` after its own retries; gateway 429/5xx or timeouts; platform 5xx; sandbox gone | The engine exits non-zero; the platform relaunches after about 2, then 10 minutes; cap 3. |
| Ours, deterministic | gateway 4xx on our key (the 30 Oct 404), an engine bug                                                                                | `fix_platform_error`, a Sentry error, fixed copy, Retry.                                  |
| The user's          | a check run failed `environment` (credentials, source, repository access); plan limit                                                 | `fix_environment_error` with the typed message, Retry.                                    |

- The platform classifies by code and gives the engine the class in the check-run status.
- The brief carries the launch number, so the checkpoint and its endpoint go.
- Retry: `POST agent-fix-runs/<id>/retry/` on a failed fix except `fix_deadline`; the clock
  restarts; a completed check run replays, a failed or canceled one runs again; `retryable` on the
  read.
- Open: how to tell a dead customer key from a candidate that breaks the agent.

An architect review of this plan was running when this file was written.

## Open questions

- Should Improve wait for the Omega diagnosis? The FE drawer gates the button on it; it was
  removed locally only, for testing.
- The FE shows `report.summary` before `failure.message`. For a failed fix the summary is the
  engine's error text; the FE should show `failure.message` first.
- Credits spent on a run that failed on our side: refund or not.

## Tickets

- TH-8956 (Azain): plain `environment_values` are not declared to authoring.
- TH-8957 (Jayasurya): certify needs `--certify-only`; the prod and local ALK templates lack it.
  The dev ALK template (0.3.3) lacks it too while dev has certify on.
- TH-8958 (Jayasurya): environments built by an older ALK cannot certify.

## The dev box harness

Throwaway, for end-to-end runs only. Container `th8899-backend` (image `futureagi/future-agi:dev`
with the stacked branch mounted from `~/th8899-wt`), database `tfc_th8899`, Temporal namespace
`th8899`, a cloudflared quick tunnel for the sandboxes. Scripts and logs in `~/th8899/`:
`run.sh`, `api.py`, `pipeline*.sh`, `pipeline.log`, `RESULT*.md`.

Changed on the shared dev box for this work (backups next to each file):

- `~/future-agi/futureagi/ee/cloud/agentcc_config/config.yaml`: an OpenRouter provider with
  `deepseek/deepseek-v4.1-flash` and its price (`.bak-2026-10-30-th8899`).
- `~/future-agi/.env`: `OPENROUTER_API_KEY` replaced, the old one was dead
  (`.env.bak-2026-10-30-th8899`).

Clean up after the work: the container, the database, the Temporal namespace, the tunnel,
`~/th8899-wt` and its local branch.
