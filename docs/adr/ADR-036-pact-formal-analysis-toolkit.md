# ADR-036: pact — Python + Go formal static analysis toolkit

**Status**: Accepted  
**Date**: 2026-05-11  
**PR**: #379  

---

## Context

AI agents and LLM-backed applications fail in ways that are structurally
predictable: optional values dereferenced without guards, async calls missing
`await`, mutable default arguments that accumulate state across calls, LLM
response fields indexed without guard checks. These are not random bugs — they
are violations of invariants that can be expressed as formal constraints.

Existing static analysis tools (pylint, mypy, ruff) operate on idiomatic
patterns. They do not model the *semantic contracts* of agent code: what
it means for an LLM response to be safely consumed, what Z3 can prove about a
function's call sites given its signature constraints, or whether a Go goroutine
has a reachable sync mechanism.

We need a tool that:
- Is grounded in formal methods (Z3 SMT, Datalog fixedpoint) not heuristics
- Is extensible: new failure modes are new data, not new code
- Covers both Python and Go (the two languages in this stack)
- Can eat real codebases at scale to generate formally-labeled training data
- Is independent of any specific framework — Future AGI is dogfood, not the scope

---

## Decision

**pact** (Python AST Constraint Tool) — a two-layer static analysis toolkit.

### Layer 1: FailureMode plugin system (`failure_mode.py`)

Each failure mode is a declarative `FailureMode` dataclass:

```python
@dataclass
class FailureMode:
    name: str
    description: str
    check: Callable[[CallSite, ModelIndex, FunctionIndex], list[FailureEvidence]]
    file_check: Optional[Callable[[str], list[FailureEvidence]]] = None
```

`check` operates on call sites extracted from the AST. `file_check` operates
on files directly — covering modules with no outgoing calls (e.g. a file that
only defines functions with mutable defaults). New modes require no changes to
the checker or orchestration — they are pure data.

Shipped failure modes (9 total):

| Mode | Scope | What it catches |
|------|-------|-----------------|
| `optional_dereference` | call | `.attr` on a value that may be None without a guard |
| `bare_except` | file | `except:` / `except Exception:` swallowing all errors silently |
| `required_arg_missing` | call | `Model.objects.create()` missing a required field |
| `save_without_update_fields` | call | `.save()` without `update_fields` — silent full-row overwrite |
| `mutable_default_arg` | file | `def f(x=[])` — shared mutable default across calls |
| `missing_await` | file | Async call inside `async def` without `await` |
| `format_arg_mismatch` | call | `"{} {}".format(x)` — placeholder/arg count mismatch |
| `llm_response_unguarded` | file | `response.choices[0]` without length/None guard |
| `model_constraint` | call | Django model field constraints violated at creation |

### Layer 2: Z3 Fixedpoint / Datalog engine (`z3_engine.py`)

`PactEngine` encodes model field constraints as Z3 Datalog relations and queries
fixedpoint to find unsatisfied required fields at each call site. This gives
formally-verifiable results: Z3 either proves safety (UNSAT — no violation
possible) or produces a witness (SAT — concrete missing fields).

Current limitation: `z3_engine` and `checker.py` run on parallel paths.
`checker.py` uses the imperative `failure_mode.check()` path; `z3_engine`
uses the Datalog path. Unification is tracked as technical debt.

### Go checker (`go/checker/main.go` + `go_checker.py`)

A standalone Go binary (`pact-go`) walks Go ASTs via `go/ast` and detects:

| Mode | What it catches |
|------|-----------------|
| `go_ignored_error` | `x, _ := f()` discarding an error return |
| `go_bare_recover` | `defer func() { recover() }()` swallowing all panics |
| `go_unchecked_assertion` | `v := x.(T)` without the `, ok` two-result form |
| `go_goroutine_no_sync` | `go func(){...}()` with no WaitGroup/channel/context |

The Python bridge (`go_checker.py`) invokes `pact-go` via subprocess and
converts JSON output to `FailureEvidence` objects. Degrades gracefully when
Go is absent — Python analysis continues unaffected.

### GitHub corpus scanner (`scan_github.py`)

Searches GitHub for Python repositories, fetches source, runs all failure modes,
and streams JSONL records:

```json
{"repo": "owner/repo", "stars": 1200, "file": "src/agent.py",
 "line": 42, "mode": "llm_response_unguarded", "call": "choices[0]",
 "message": "...", "code_context": "...", "scanned_at": "..."}
```

Z3-grounded violations are formally labeled (not heuristic guesses). At scale
this produces `(code, bug)` pairs that don't exist in any existing training set —
the signal that makes pact self-improving.

### CI Action (`.github/actions/pact/action.yml`)

Composite GitHub Action: sets up Python + Go, builds `pact-go`, runs analysis,
supports `text | json | sarif` output, optional build-fail gate. Allows any
repo to add pact as a CI check with three lines of YAML.

### Alternatives considered

| Option | Rejected because |
|--------|-----------------|
| Extend ruff/pylint | Heuristic pattern-matching; no formal verification contract; not extensible as data |
| Full type checking (mypy/pyright) | Type safety ≠ semantic contract safety; doesn't model LLM response consumption patterns |
| Semgrep rules | Regex-level; no Z3 backing; false negative rate on complex patterns |
| Build on top of existing SMT tools (e.g. CrossHair) | Designed for function-level pre/postconditions; not codebase-scale call-site analysis |

---

## Consequences

- **Independence**: pact has zero runtime dependencies on Future AGI's Django
  stack. Future AGI is the first consumer (dogfood). The tool is designed to
  be extracted to a standalone repository and distributed as a pip package.
- **Extensibility**: new failure modes are `FailureMode(name, description, check)`
  instances — no core changes. The LLM can author new modes; Z3 verifies them.
- **Go coverage gap**: `go_checker.py` invokes `pact-go` via subprocess.
  The Go modes are heuristic (AST-based) not Z3-verified. A future `pact-go`
  Z3 backend would close this gap.
- **Z3 engine disconnect**: `z3_engine.PactEngine` and `checker.py` run on
  parallel code paths. Unifying them — so the Datalog engine is the verification
  layer for all failure modes — is the primary remaining architectural debt.
- **Training data flywheel**: `scan_github.py` creates formally-labeled corpus
  at scale. This is the mechanism by which pact becomes self-improving: violations
  found by Z3 ground-truth new FailureModes; new modes find more violations;
  loop.

---

## Formal verification

### TLA+ spec: `docs/tla/Pact.tla`

Invariants:
- `TypeInvariant` — state variables typed throughout
- `DeduplicationInvariant` — violations set contains at most one entry per
  `(file, line, mode, call)` key
- `CoverageInvariant` — when analysis is complete, every `(mode, site)` pair
  and every `(file_mode, file)` pair has been visited

Properties (liveness):
- `EventuallyTerminates` — `<>done` under weak fairness
- `MonotonicViolations` — violations set only grows: `□(violations ⊆ violations')`

> **TLC note**: Not wired into CI. Run manually:
> ```
> tlc docs/tla/Pact.tla -config docs/tla/Pact.cfg
> ```

### Z3 engine: `tools/pact/z3_engine.py`

- `PactEngine` — Datalog fixedpoint over required-field constraints
- Encodes `required(model, field)` and `provided(call_site, field)` as relations
- Queries `missing(call_site, field)` — fields required but not provided
- Produces formally-verified `FailureEvidence`; Z3 UNSAT = provably safe

### Tests: `tools/pact/test_checker.py`, `tools/pact/test_z3_engine.py`, `tools/pact/test_go_checker.py`

59 tests total across three suites:

| Suite | Count | What it covers |
|-------|-------|----------------|
| `test_z3_engine.py` | 10 | Z3 Datalog fixedpoint: required fields, cross-file violations |
| `test_checker.py` | 28 | All 9 Python failure modes; file_check coverage; deduplication |
| `test_go_checker.py` | 21 | Go checker bridge: JSON→FailureEvidence, subprocess plumbing, integration |
