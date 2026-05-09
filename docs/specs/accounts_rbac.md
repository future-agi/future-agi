# accounts/ RBAC System Specification

## Overview

The RBAC system in `accounts/` controls access across two scopes: **organization** (global) and **workspace** (scoped). All permission checks run through integer level comparisons rather than string equality to enable `>=` comparison and `max()` aggregation.

## Role hierarchy

### Organization roles

| Role    | Level | Notes                          |
|---------|-------|--------------------------------|
| Owner   | 15    | Can manage other Owners        |
| Admin   | 8     | Auto-inherits workspace admin  |
| Member  | 3     |                                |
| Viewer  | 1     | Read-only                      |

### Workspace roles (same integer scale, different scope)

| Role              | Level |
|-------------------|-------|
| Workspace Admin   | 8     |
| Workspace Member  | 3     |
| Workspace Viewer  | 1     |

Levels are **not** a contiguous range — gaps (2, 4–7, 9–14) are reserved for future roles (e.g., "Billing Admin" at 10) that can be inserted without migration.

## Effective workspace level

```
effective_ws_level(user, ws) =
  if org_level >= ADMIN:  max(org_level, WORKSPACE_ADMIN)
  else:                   max(org_level, ws_membership_level)  -- None if no membership
```

**Invariant**: `effective_ws_level(user, ws) >= org_level` for all users with any workspace access.

**Invariant**: Org Admin/Owner never have a workspace-scoped effective level below `WORKSPACE_ADMIN`.

## Invitation rules

`can_invite_at_level(actor_level, target_level)` returns True iff:
- `actor_level >= OWNER`: any target level is allowed (Owner may invite Owners)
- otherwise: `target_level <= actor_level`

**Escalation impossibility**: A non-Owner actor cannot grant a target level higher than their own.

## Manage-target rules

`CanManageTargetUser` (DRF permission class) returns True iff:
- `actor_level >= OWNER`: may manage anyone
- otherwise: `actor_level > target_level` (strictly above)

**Antisymmetry below Owner**: if actor can manage target, target cannot manage actor (for non-Owner actors).

## Permission classes

| Class                            | Required level             |
|----------------------------------|----------------------------|
| `IsOrganizationMember`           | any active membership       |
| `IsOrganizationAdmin`            | `level >= ADMIN (8)`        |
| `IsOrganizationOwner`            | `level >= OWNER (15)`       |
| `IsOrganizationAdminOrWorkspaceAdmin` | org `>= ADMIN` OR ws effective `>= WORKSPACE_ADMIN` |
| `CanManageTargetUser`            | strictly above target (Owner exception) |

## Last-owner protection

Demoting or removing the last Owner is blocked by a race-safe `select_for_update()` count check. Both new `level`-based owners and legacy `"Owner"` string role members are counted.

## API key model

`OrgApiKey` has three types:

| Type     | Effective user                          | Workspace scope |
|----------|----------------------------------------|-----------------|
| `system` | First-created active user in the org   | Optional        |
| `user`   | Explicit `user` FK                     | Optional        |
| `mcp`    | Resolved per-request                   | Optional        |

Only one `system` key per organization (enforced by unique constraint on non-deleted rows).

## Legacy fallback

`OrganizationMembership.level_or_legacy` is the canonical source of truth. The legacy `User.organization_role` string field is only consulted when no active `OrganizationMembership` row exists — a state that should not occur for new accounts.

## Source files

| File | Purpose |
|------|---------|
| `tfc/constants/levels.py` | Integer level definitions, string ↔ level maps |
| `tfc/constants/roles.py` | Role enum, permission matrices |
| `tfc/permissions/rbac.py` | DRF permission classes |
| `tfc/permissions/utils.py` | `get_org_membership`, `get_effective_workspace_level`, `can_invite_at_level` |
| `accounts/models/user.py` | `can_access_workspace`, `can_write_to_workspace`, API key model |
| `accounts/views/rbac_views.py` | Invite/demote/remove endpoints with escalation guards |
