# Architecture Decision Records

ADRs record significant decisions made in the design and implementation of this platform.
Each ADR captures the context, options considered, the decision taken, and its consequences.

## Index

| ADR | Title | Status | PR |
|-----|-------|--------|----|
| [ADR-032](ADR-032-dataset-auto-eval-config.md) | Dataset auto-eval configuration with debounce protocol | Accepted | #356 |
| [ADR-033](ADR-033-agent-template-composition.md) | Agent template system with formal composition proofs | Accepted | #358 |

## Format

Each ADR follows the [Nygard template](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions):
`Status` / `Context` / `Decision` / `Consequences` / `Formal verification`

## Formal verification convention

Executable proofs live next to the code they verify:

| Tool | Location pattern | Execution |
|------|-----------------|-----------|
| Z3 (SMT) | `*/formal_tests/test_*_z3.py` | `make test-unit` (no Docker) |
| Hypothesis | `*/formal_tests/test_*_hypothesis.py` | `make test-unit` (no Docker) |
| TLA+ specs | `docs/tla/*.tla` / `*.cfg` | Manual — see note below |

> **TLA+/TLC note**: The `.tla` specs are correctness arguments and living documentation.
> TLC (the Java model checker) is not wired into CI. Run it manually:
> ```
> tlc docs/tla/TemplateVersioning.tla -config docs/tla/TemplateVersioning.cfg
> ```
> The Z3 and Hypothesis suites are what gate CI.
