---
id: "026"
title: "Org Admin/Owner automatically inherits Workspace Admin in every workspace"
status: accepted
date: 2026-05-08
---

## Context

Organizations contain many workspaces. Org Admins and Owners must be able to manage any workspace without requiring an explicit `WorkspaceMembership` row for each one. Creating membership rows automatically on every workspace creation would require complex cascade logic and still leave race conditions when new workspaces are created.

## Decision

`get_effective_workspace_level(user, ws)` implements:

```python
if org_level >= Level.ADMIN:
    return max(org_level, Level.WORKSPACE_ADMIN)
# else: look up explicit WorkspaceMembership
```

Org-level Admins and Owners are treated as having at least `WORKSPACE_ADMIN` (8) in every workspace, with no membership row required. For an Owner (level 15), the effective level is 15 — higher than WORKSPACE_ADMIN — so all `>= WORKSPACE_ADMIN` checks still pass.

**Why:** This is simpler and more correct than maintaining implicit rows. Revoking org Admin access automatically revokes workspace admin everywhere, with no membership rows to clean up.

## Consequences

- No `WorkspaceMembership` rows are created for Org Admins — the effective level is computed on every check
- `effective_ws_level >= org_level` is a guaranteed invariant (proved by Z3 in `accounts/formal_tests/test_rbac_z3.py`)
- `effective_ws_level` for Owner is 15, not 8 — callers must use `>=` not `==` to check workspace admin status
- Workspace-scoped API keys for Org Admins still pass the `>= WORKSPACE_ADMIN` check via this mechanism
