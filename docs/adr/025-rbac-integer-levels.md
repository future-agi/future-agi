---
id: "025"
title: "Integer levels for RBAC instead of string comparison"
status: accepted
date: 2026-05-08
---

## Context

Early RBAC code compared roles as strings (`role == "Owner"`, `role in ["Owner", "Admin"]`). This required maintaining explicit allowlists for every permission check and made it impossible to express "Admin or above" without enumerating every higher role.

## Decision

Replace string comparison with integer levels:

```
OWNER = 15,  ADMIN = 8,  MEMBER = 3,  VIEWER = 1
WORKSPACE_ADMIN = 8,  WORKSPACE_MEMBER = 3,  WORKSPACE_VIEWER = 1
```

Permission checks become `level >= Level.ADMIN`. New intermediate roles (e.g., "Billing Admin" at 10) can be inserted without changing any existing guard.

**Why:** The integer scale uses gaps (2, 4–7, 9–14) so future roles slot in without renumbering existing ones. The workspace and org roles intentionally share the same scale so `max(org_level, ws_level)` computes the effective workspace level without a type conversion.

## Consequences

- All guards use `>=` or `>` — unambiguous, unit-testable with Z3
- `STRING_TO_LEVEL` map converts legacy string roles to integers on read
- The legacy `User.organization_role` string field remains for fallback only; `OrganizationMembership.level` is the source of truth
- OWNER=15 is intentionally higher than WORKSPACE_ADMIN=8 so `max(org_level, ws_level)` for an Owner is 15, not 8 — callers check `>= WORKSPACE_ADMIN` (not `== WORKSPACE_ADMIN`)
