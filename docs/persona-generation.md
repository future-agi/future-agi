<div align="center">

# Persona Generation: From 18 Static Personas to Taxonomy Coverage

**Deterministic, seeded expansion of simulation personas with edge-case attribution.**

[![deterministic](https://img.shields.io/badge/deterministic-seeded-brightgreen?style=flat-square)](./persona-generation.md)
[![offline](https://img.shields.io/badge/offline-no--LLM-lightgrey?style=flat-square)](./persona-generation.md)
[![coverage](https://img.shields.io/badge/taxonomy-8--of--8-success?style=flat-square)](./persona-generation.md)

</div>

---

## Contents

- [Overview](#overview)
- [Taxonomy](#taxonomy)
- [Usage](#usage)
- [Verification](#verification)
- [CUA Stub](#cua-stub)
- [Reviewer Guide](#reviewer-guide)

---

## Overview

`simulate/services/system_personas.py` ships 18 static `SYSTEM` personas. They are high quality but fixed, so failure discovery plateaus.

This change adds a deterministic generator that crosses demographics with an edge-case taxonomy and emits `WORKSPACE` persona payloads. With `n=50` and the default seed, all 8 taxonomy categories are covered.

> [!NOTE]
> No LLM and no GPU required for v1. Output is seeded and reproducible.

## Taxonomy

Defined in `futureagi/simulate/constants/edge_case_taxonomy.py`:

| Key | Label | Weight |
| :--- | :--- | :---: |
| `payment_dispute` | Payment dispute | 3 |
| `auth_failure` | Auth failure | 2 |
| `interruption` | Interruption heavy | 2 |
| `multi_intent` | Multi intent | 2 |
| `vague_request` | Vague request | 2 |
| `policy_refusal` | Policy refusal | 1 |
| `accent_noise` | Accent and noise | 1 |
| `escalation` | Escalation demand | 1 |

## Usage

```python
from simulate.services.persona_generator import generate_personas, taxonomy_coverage

personas = generate_personas("refund and billing support", n=50, seed=42)
print(len(personas))  # 50
print(taxonomy_coverage(personas))  # 1.0
```

```text
Generated: 50
Coverage: 1.0
payment_dispute: 12
auth_failure: 7
interruption: 8
multi_intent: 6
vague_request: 7
policy_refusal: 3
accent_noise: 4
escalation: 3
```

Each payload includes `name`, `description`, demographics (`gender`, `age_group`, `occupation`, `location`, `personality`, `communication_style`), `edge_case`, and `additional_instruction` with the taxonomy prompt fragment.

> [!TIP]
> Pass the scenario source text to bias names and descriptions toward the flow under test. Same `seed` always returns the same list.

## Verification

```bash
python scripts/verify_personas.py
```

```text
Generated: 50
Coverage: 1.0
Time for 50: 0.010s avg 0.20ms
Determinism: PASS
OVERALL PASS
```

Unit tests:

```bash
python -m pytest futureagi/simulate/tests/test_persona_generator.py -v -m "not live_llm"
```

Expected: 8 passed. Tests cover count, full taxonomy coverage, determinism, uniqueness, payload shape, and empty source handling.

## CUA Stub

`futureagi/simulate/models/agent_definition.py` adds a reserved type:

```python
class AgentTypeChoices(models.TextChoices):
    VOICE = "voice", "Voice"
    TEXT = "text", "Text"
    CUA = "cua", "CUA (reserved, not yet runnable)"
```

Migration `futureagi/simulate/migrations/0079_agentdefinition_cua_type.py` alters `agent_type` choices. The serializer in `futureagi/simulate/serializers/agent_definition.py` rejects CUA creation with a clear validation error. This unblocks roadmap discussion for computer-use agents without shipping an untested browser sandbox.

> [!IMPORTANT]
> CUA runs are intentionally blocked at validation. Full execution tracing for agents remains future work.

## Reviewer Guide

Check core files:

- `futureagi/simulate/constants/edge_case_taxonomy.py` (taxonomy)
- `futureagi/simulate/services/persona_generator.py` (generator)
- `futureagi/simulate/tests/test_persona_generator.py` (tests)
- `futureagi/simulate/models/agent_definition.py` (CUA choice)
- `futureagi/simulate/migrations/0079_agentdefinition_cua_type.py` (migration)
- `futureagi/simulate/serializers/agent_definition.py` (CUA guard)

Run verification:

```bash
python scripts/verify_personas.py
python -m pytest futureagi/simulate/tests/test_persona_generator.py -v -m "not live_llm"
```

<div align="center">

**Deterministic personas. Full taxonomy coverage. Ready to review.**

</div>
