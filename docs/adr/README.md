# Architecture Decision Records

ADRs record significant decisions made in the design and implementation of this platform.
Each ADR captures the context, options considered, the decision taken, and its consequences.

## Index

| ADR | Title | Status | PR |
|-----|-------|--------|----|
| [ADR-029](029-gopter-property-based-testing-gateway.md) | Property-based testing for gateway pure functions with gopter | Accepted | #338 |
| [ADR-030](030-centroid-ttl-expiry.md) | Expire stale cluster centroids via ClickHouse TTL | Accepted | #339 |
| [ADR-031](031-end-user-dedup-non-nullable-user-id-type.md) | Make EndUser.user_id_type non-nullable to fix dedup uniqueness | Accepted | #340 |
| [ADR-032](ADR-032-dataset-auto-eval-config.md) | Dataset auto-eval configuration with debounce protocol | Accepted | #356 |
| [ADR-033](ADR-033-agent-template-composition.md) | Agent template system with formal composition proofs | Accepted | #358 |
| [ADR-034](ADR-034-fi-simulate-cli-mcp.md) | fi-simulate CLI and MCP tool for headless simulation runs | Accepted | #80/#81 |

> ADRs 001–028 are documented on their respective feature branches and will be
> consolidated here once those branches merge.

## Format

Each ADR follows the [Nygard template](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions):
`Status` / `Context` / `Decision` / `Consequences` / `Formal verification`

## Five-step verification methodology

Every non-trivial feature follows this sequence — each step targets a different kind of error:

| Step | Artifact | What it proves | Execution |
|------|----------|---------------|-----------|
| 1 | **TLA+ spec** (`docs/tla/*.tla`) | The *model* is coherent: all reachable states satisfy invariants | Manual — `tlc docs/tla/<Name>.tla -config docs/tla/<Name>.cfg` |
| 2 | **ADR** (`docs/adr/*.md`) | The design decision is documented with context, alternatives, and consequences | — (living doc) |
| 3 | **Z3 proofs** (`*/formal_tests/test_*_z3.py`) | The *model's invariants* are internally consistent (UNSAT checks) | `make test-unit` |
| 4 | **Hypothesis tests** (`*/formal_tests/test_*_hypothesis.py`) | Individual methods satisfy properties under arbitrary inputs | `make test-unit` |
| 5 | **Integration probes** (`*/formal_tests/test_*_integration.py`) | The *full state machine* end-to-end satisfies all TLA+ invariants simultaneously | `make test-unit` |

Steps 3–5 all gate CI. The gap between steps 4 and 5 is significant:
Hypothesis tests exercise individual methods in isolation; integration probes run the
real implementation end-to-end with a scripted fake backend and check every invariant
on the final state. This catches emergent bugs where individually-correct methods
interact incorrectly — wrong phase ordering, execution IDs set too late, summaries
fetched before terminal status, etc.

> **TLA+/TLC note**: The `.tla` specs are correctness arguments and living documentation.
> TLC (the Java model checker) is not wired into CI. Run it manually:
> ```
> tlc docs/tla/<Name>.tla -config docs/tla/<Name>.cfg
> ```
