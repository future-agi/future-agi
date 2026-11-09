# An engineering team that survives a kill -9

## What it does

A feature request (three small helper functions) goes to a verified task tree. One engineer agent writes each helper, and the maintainers' checks, run for real by Node, decide whether each result counts. Verified work is saved to disk. The demo runs the team in a child process whose third engineer never gets an answer from its model, kills that process with SIGKILL once two helpers are verified, and starts the team again in the same folder: the two verified helpers come back from disk with no model call (their checks run again), and only the third is built.

## Run it with no key

```bash
omega hub run engineering-team
```

Scripted models play the three engineers. The task tree, the checks (a real `node` run of each check script), the verified memory and the crash and resume are real.

It prints:

```text
run 1: killed with SIGKILL while the third engineer waited for its model
verified on disk after the kill: 2 of 3 helpers
run 2: CI slugify: pass
run 2: CI truncate: pass
run 2: CI readingTime: pass
run 2: CI all helpers together: pass
run 2: feature verified: true; model calls: 1 (only the unfinished helper)
```

## Run it with your key

```bash
export OPENAI_API_KEY=...
omega hub run engineering-team --live openai:gpt-5.4
```

Your model writes each helper from its goal. A live run takes its own path, so a helper may fail its check and be retried; the cap is thirty model calls, billed to your account.

## Recommended models

Two live runs each on 9 October 2026 (the feature verified):

| Model | Verified |
| --- | --- |
| `openai:gpt-5.4` | 2 of 2 (recommended) |
| `openai:gpt-4o-mini` | 0 of 2 |

A small model can fail a helper's check three times and end with a feature that is not verified; the cap of thirty model calls still holds.

## How it works

- `decompose` splits the feature into one subgoal per helper; `solve` asks an engineer agent for the code; `certify` writes the file and runs the helper's check with `node`; `compose` joins the files and runs every check once more.
- `FileVerifiedTreeMemory` keeps each verified result in `verified.json`, keyed by goal, so a restarted run recalls it instead of redoing it.
- `runTeam` runs one pass; `crashAndResume` starts pass one as a child process, waits for two verified results, kills it, then runs pass two here and reports the model calls each pass made.
- Teaching version with a planner, sandboxed checkouts and approvals: the engineering team showcase in the Omega docs.

## Make it yours

- Replace the three helpers and their checks with your own task and tests.
- Add a `kill -9` of your own: a restarted run needs the memory file and nothing else.

After a change, run the test and record the demo again:

```bash
omega hub check engineering-team --record
```
