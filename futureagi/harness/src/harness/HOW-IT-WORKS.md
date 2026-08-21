# How the harness actually works

What happens between you typing a sentence and a graded result appearing. Written to be read
alongside the code, so every claim below names the file it lives in.

The shape is the same at every stage, and worth holding onto:

> **A stage is a model session with a small set of tools and its instructions in a markdown file.
> The model decides what to do; the tools do anything that must be exact and refuse anything that
> must not happen. Nothing reaches disk except through a tool that checked it first.**

There is no pipeline. Each stage is a conversation you can interrupt, correct, and resume.

---

## The pieces

| Piece | Where | What it is |
|---|---|---|
| Stage | `session.py` | A live model session, held open across turns, emitting typed events |
| Instructions | `skills/<stage>/SKILL.md` | How that stage works, in prose. Editable without touching code |
| Tools | `tools.py`, `world/tools.py`, `scenario_tools.py`, `run/tools.py` | The exact half: they execute, validate, and refuse |
| Artifacts | `artifacts/sessions/<id>/` | What each stage leaves behind for the next |
| Conversation | `chat.py` | Holds one agent's journey through the stages |
| Session | `sessions.py` | One conversation, one folder: chat and artifacts together |

The artifacts, each the input to the next stage:

```
contract.json → world.sqlite + handlers/ + simulator_prompt.md + sub_goals.json
             → scenarios/<name>/ (+ scenarios.json as the index) → runs.json
```

All of it, plus the conversation that produced it, lives in one folder per session. There is
nothing held in memory that is not also on disk, so closing the page, restarting the server or
coming back tomorrow all resume the same way: by reading the folder.

---

## 1. Reception — which agent is this?

`reception.py`

A stage with `Read`, `Glob`, `Grep` and one tool, `point_at_agent`. You say where your agent
lives; it looks, confirms the path exists, picks a short name, and calls that tool.

It looks from the **workspace root** (the directory holding your repos), not from inside
`agent-learning-kit`, because the agent under test is almost never inside the harness.

`point_at(name, path, kind)` refuses a path that does not exist, and refuses an unknown kind.
`kind` selects an `AgentSource` from `sources.py` — `repo` (code on disk, gets file tools) or
`spec` (a prompt and tool schema pasted in, gets no file tools). **A new kind of agent is one
registration, not a new code path.**

---

## 2. Understand — what is this agent, verifiably?

`understand.py`, `skills/understand-agent/SKILL.md`, gate in `tools.py`

The session gets read-only file tools and one submission tool. It reads the agent's source and
calls `submit_contract`.

**The contract** (`contract.py`) is the anti-hallucination device for everything downstream:

| Field | Why it matters |
|---|---|
| `tools[]` — name, `args`, `arg_types`, `arg_values`, description | The agent's action space. `arg_values` are the real permitted values — the menu, the enum, the lookup |
| `hard_constraints[]` | Rules the agent must follow. Told to the agent under test, and graded by the judge |
| `base_environment` | Its real starting data, reproduced row for row |
| `real_use_cases[]` | What it is actually for |
| `notes` | Free-form: whatever else the reader judged worth carrying forward |
| `amendments[]` | Anything **not** read from source — see below |

**How it is written:** `accept_contract` in `tools.py` validates before anything reaches disk. It
refuses a contract with no tools, no use cases, duplicate tool names, types for arguments that do
not exist, or — the one that mattered most in practice — *every* tool having no arguments, which
means the arguments were read and then not recorded. Problems are returned **into the
conversation**, so the model corrects and resubmits rather than a bad contract landing.

**How it is changed later** (`amend.py`). The contract is not frozen, but every change is
recorded with a reason in `amendments[]`, so months later you can still tell what came from the
agent and what came from us:

- `amend_contract` — let an argument accept a value it did not before
- `add_rule` / `drop_rule` — a hard constraint the source did not state, or one misread
- `fix_tool` — correct argument names, types, description, or remove a tool that does not exist

Each demands a `why`. A contract that can be rewritten invisibly is no longer evidence.

---

## 3. Build — the world its tools run against

`build.py`, `skills/build-environment/SKILL.md`, tools in `world/tools.py`

**This is the part that makes the whole thing worth doing.** Not mocked tool responses: a real
SQLite database with real handlers, so a call for something that is not there is *refused*, and
the agent has to cope.

It builds three things, all shared by every scenario: **the world**, **the simulator prompt** for
a conversational agent, and **the sub-goal catalogue**. The stage has sixteen tools and no file
access at all:

| Tool | Does |
|---|---|
| `create_schema` | Run the CREATE TABLE statements |
| `seed` | Insert rows — the agent's real catalogue |
| `change_data` | One UPDATE or DELETE, for fixing a row put in wrong |
| `define_handler` | One tool's implementation, **executed the moment it is defined** |
| `run_tool` | Call a defined tool and see what the world does |
| `declare_sequence` / `drop_sequence` | A series of calls whose end state must hold |
| `inspect_world` | Look at what is in the world |
| `amend_contract`, `add_rule`, `drop_rule`, `fix_tool` | Correct the contract |
| `check_world` | Run every probe, report without saving |
| `save_world` | Freeze it — refused unless it holds up |

**A handler** is Python: `def handle(args, db)`, with `db.query` / `db.one` / `db.execute` and
`ToolError` in scope. Nothing else — no filesystem, no network, because a world that depends on
the outside is not reproducible. It is `exec`'d per call in `world/runtime.py`.

**The distinction the whole design turns on** (`runtime.py`):

- `ToolError` — the world saying *no*. The id does not exist; the item is unavailable. **This is
  the world working.**
- Any other exception — our bug.

They are recorded differently and never confused. `_is_refusal` matches `ToolError` by name
across the class hierarchy, because generated handlers often declare their own.

### The gate: what `check_world` and `save_world` actually run

`world/probe.py`. Every probe restores the world to a frozen baseline first, so probes cannot
inherit each other's rows.

| Probe | Asks |
|---|---|
| `happy` | A valid call built from the contract's permitted values. A refusal is acceptable; a crash never is |
| `edge` | A nonexistent id → must refuse, not succeed and not crash. A missing required argument → must refuse |
| `coverage` | Every contract tool has a handler; no handler for a tool the agent lacks; **and each handler actually reads the arguments the contract says it takes** |
| `data` | Every identifier the contract permits exists in the world — catches a whole category left unseeded, which otherwise looks exactly like correct strictness |
| `sequence` | Each declared sequence, run from the frozen world, leaves the state it claims |
| unknown tool | Calling a tool that does not exist must refuse |

`save_world` refuses on three counts: **score below 0.85**; **no declared sequence** (calls that
each work alone can still forget what the last one did); and **a dirty world** — rows left over
from building, which would otherwise appear in every scenario as somebody else's order already
in the cart.

Then `world/snapshot.py` writes `world.sqlite`, `handlers/*.py`, `world.py` and `manifest.json`.
**The snapshot is the base state every scenario restores from.**

---

## 4. Scenarios — the conversations worth having

`scenarios.py`, `skills/write-scenarios/SKILL.md`, tools in `scenario_tools.py`

A scenario is a **change on the base environment**, and it owns a folder (`folder.py`):

```
scenarios/<name>/
    scenario.json     the instruction, the reference solution, which sub-goals it names
    setup.py          def setup(world)     what this scenario changes first
    ready.py          def ready(world)     is the world ready for it
    checks/<goal>.py  def check(world, calls)   one per deterministic sub-goal
```

`scenarios.json` is an index over those folders, regenerated from them, so anything wanting the
whole suite at a glance has it.

Code lives in files, never duplicated into JSON, and the file is the artifact: each check file is
written with a `__main__` block so it runs standalone against what a run left behind, and a test
proves the file and the harness give the same answer.

`setup` is **code** rather than a list of rows: what a scenario changes is not necessarily the
database alone. There is no persona and no opening line. Variability comes from **real
conditions** that live in `setup`, and the base world stays the shared starting point.

`sub_goals` are **names from the catalogue the environment step defined**, not restated wording.
That is what makes results roll up: the same sub-goal failing in seven of twelve scenarios is one
sentence.

`solution` is what a correct agent would do. It is never run against the agent — it exists so the
scenario can be proved.

The writer can **look** (`inspect_world`) and **rehearse** (`try_calls` — run calls against a
throwaway copy and see what state they leave), so a solution is written from what was observed.

### The gate: three proofs, no model involved

`prove.py`, called by `submit_scenario` before a scenario is kept:

| | Run | Must |
|---|---|---|
| **Ready** | reset → `setup` → `ready` | **hold** |
| **Solvable** | reset → setup → **the solution** → the checks | **pass** |
| **Not vacuous** | reset → setup → **nothing** → the checks | **fail** |

If the first fails, the world does not hold what the scenario presumes, and running it would test
us rather than the agent: the agent would fail for a precondition we got wrong, and it would read
as a finding about the agent. If the second fails, either the scenario cannot be passed or the
check is wrong. If the third passes, the checks grade nothing while reporting a result, which is
the failure that makes a suite quietly green.

Vacuity is judged on *all* checks passing, because one check surviving an empty run ("no
unavailable item was ordered") is legitimate. A single check that survives is still reported, in
`Proof.weak`: sub-goals are shared, so a check that cannot fail without calls would roll up as a
pass for an agent that did nothing at all.

`save_scenarios` additionally refuses a suite where no sub-goal is shared by two scenarios,
because nothing would roll up.

---

## 5. Run — put someone in front of it

`run/`

The simulation is **not a loop written here**. ALK already owns placing a call, driving the
synthetic user, and producing a transcript; the harness supplies the world, the instruction, and
the grading. Against the real hosted agent that is `run/live.py` and `run/call.py`:

```
world + setup ──► webhook ──► public url ──► the assistant's OWN tools repointed
                                                       │
                     ALK's voice case places the call ──┘
                                                       │
                       the world afterwards + the calls ──► the sub-goals' checks
```

1. **Restore** the frozen world and apply this scenario's `setup`. Its own copy, so nothing leaks
   between scenarios.
2. **Stand up the webhook** (`run/voice.py`) and bind that world to it. A hosted voice agent
   executes its tools by calling a webhook, so answering that webhook from `handle_tool_call` is
   the entire integration.
3. **Expose it** (`cloudflared`, or `HARNESS_WEBHOOK_URL`), because a hosted agent cannot reach
   loopback.
4. **Repoint the assistant.** `pointed_at` copies the agent's **own** tools and changes only
   `server.url`. Nothing about the agent is redefined — rebuilding its tools would mean testing an
   agent we wrote.
5. **Place the call** through ALK's own voice case, with the scenario's filled simulator prompt
   driving the caller.
6. **Grade** from the world afterwards plus the recorded calls, through the same checks the gates
   used. A sub-goal marked `judged` is reported as judged, never silently counted.

`run/alk.py` is the same story for the text path: the world goes in as `environment=` to ALK's
`ChatEnvironment`, which owns the turn loop.

Running is also a **stage of the conversation**, not only a command (`run/stage.py`,
`skills/run-scenarios/SKILL.md`, tools in `run/tools.py`: `preflight`, `list_scenarios`,
`run_scenario`, `read_results`). The stage exists because reading a failure is judgement: it has
to sort every failure into one of four causes — the agent was wrong, the world wrongly refused,
the check is wrong, or the simulated caller never asked for the thing — and only the first is a
finding about the agent. Each run's record lands in `runs.json` with the instruction, the
per-sub-goal verdicts, every tool call, and the transcript.

This is what the environment was built for. The previous run's known issues — *"mocked tools
always succeed, including removing an item that was never added"*, *"mock responses do not vary by
argument"*, *"world state does not change unless a scenario sets `state_updates`"* — are all the
same defect, and a world that really holds rows and can really refuse answers all three.

---

## What is exact, and what is judgement

The split is deliberate and worth defending:

| Judgement (the model) | Exact (code) |
|---|---|
| Reading unfamiliar source | Whether the contract is structurally usable |
| Designing a schema | Whether a handler crashes or refuses |
| Choosing what is worth testing | Whether an expectation resolves against real tables |
| Whether a claim held | Whether the state matches |

**The model never decides whether something passed.** It decides what to try.

---

## Where to extend it

- A new **agent kind** → a class in `sources.py` and one registration
- A new **world kind** (browser, filesystem, queue) → a class in `world/kinds.py` implementing
  `values_present` / `mutable_state` / `describe`, and one registration. Browser is registered
  and stubbed
- A new **place the agent runs** (Vapi, LiveKit, a hosted endpoint) → a class in `run/targets.py`
  with `open` / `say` / `close`, whose tool calls reach the same `world.handle_tool_call`
- A change to **how a stage works** → edit its `SKILL.md`. No code

## What is not built

- Browser worlds: registered, not implemented
- Snapshots are local files, not S3
- Judged sub-goals are reported as judged, not actually sent to a judge yet
- Results do not post to the platform
- Nothing reports which of the contract's use cases have no scenario
