---
id: ADR-029
title: Property-based testing for gateway pure functions with gopter
status: accepted
date: 2026-05-08
related_issues: []
---

## Context

The gateway's registry and key-rotation modules contain pure, side-effect-free
functions that operate over large or unbounded input domains:

- `ResolveModelName(model string) string` — strips the `"provider/"` prefix from
  model strings; must be idempotent and never produce an empty string for a
  non-empty input.
- `Registry.ResolveWithRouting(model string)` — implements prefix-match resolution;
  for any registered provider ID `p` and model name `m`, the key `"p/m"` must
  resolve to provider `p`.
- `maskKey(key string) string` — produces a short, readable mask; must output
  `"***"` for keys ≤ 8 chars and exactly 11 chars (`first4...last4`) for keys
  > 8 chars.
- `KeyState.MaskedState()` — must never expose the full key for long keys.

Example-based unit tests cannot exhaustively cover these invariants because the
input space is effectively infinite. A single wrong case (e.g., a 9-character
key that produces a longer mask than the input) would pass all hand-written
examples yet violate the documented contract.

## Decision

Use **[gopter](https://github.com/leanovate/gopter)** for property-based testing
of pure gateway functions.

gopter is the established Go property-based testing library. It integrates with
`testing.T`, supports regex-constrained generators (`gen.RegexMatch`), and
produces shrunk counterexamples on failure.

Property tests live alongside the units they exercise:
- `internal/providers/registry_prop_test.go` — 7 properties for
  `ResolveModelName` and `ResolveWithRouting`.
- `internal/rotation/rotation_prop_test.go` — 7 properties for `maskKey` and
  `MaskedState`.

## Properties documented

| Function | Property |
|----------|----------|
| `ResolveModelName` | Strips `"p/m"` → `"m"` |
| `ResolveModelName` | Idempotent: applying twice = once |
| `ResolveModelName` | Non-empty input → non-empty output |
| `ResolveWithRouting` | `"known/model"` resolves to the registered provider |
| `ResolveWithRouting` | `"unknown/model"` (2+ providers) → error |
| `ResolveWithRouting` | Single-provider registry is the default for unknown models |
| `maskKey` | Empty key → `""` |
| `maskKey` | Keys 1–8 chars → `"***"` |
| `maskKey` | Keys > 8 chars → exactly 11 chars |
| `maskKey` | Output for long keys always contains `"..."` |
| `maskKey` | Non-empty input → non-empty output |
| `MaskedState` | Long primary key is never exposed verbatim |
| `MaskedState` | Empty `OldKey` stays empty |

A discovered invariant: `maskKey` output for a 9-character key is 11 chars,
which is **longer** than the input. This is correct behavior (always outputs
`first4...last4`) but is easy to misread as "output is always shorter than
input." The property test `TestProp_MaskKey_LongKeyOutputIsFixed11` documents
this precisely.

## Alternatives considered

- **table-driven examples only** — Cannot cover the invariant about output
  length for 9-char keys or the idempotency of `ResolveModelName` across
  arbitrary strings.
- **fuzzing (`go test -fuzz`)** — Finds panics and crashes but requires manual
  oracle assertions that are awkward to express as precondition/postcondition
  pairs. gopter's `prop.ForAll` is cleaner for algebraic properties.
- **Z3** — Appropriate for constraint satisfaction and theorem proving (used
  elsewhere in this repo for Python modules), but has no idiomatic Go binding
  and adds heavy external tooling. For pure function properties over finite
  machine integers, gopter suffices.

## Consequences

- `go.mod` gains `github.com/leanovate/gopter v0.2.11` (test-only transitive).
- Each property test runs 100 random samples by default; easily tuned via
  `gopter.NewProperties(gopter.DefaultTestParameters())`.
- Property tests run as part of `make test` / `go test ./...` — no separate
  step.
- Future contributors adding pure functions with large input domains should add
  corresponding properties to the adjacent `_prop_test.go` file.
