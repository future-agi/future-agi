---
id: "027"
title: "CanManageTargetUser returns True (not False) when target has no org membership"
status: accepted
date: 2026-05-08
---

## Context

`CanManageTargetUser` is a DRF permission class that checks whether the actor's level is above the target's. When the target `user_id` has no `OrganizationMembership` in the actor's org, there are two choices:

1. Return `False` (deny) — safe but gives a generic 403 with no explanation.
2. Return `True` — let the view receive the request and return a descriptive 400.

## Decision

Return `True` when `OrganizationMembership.DoesNotExist`. The view is responsible for re-validating that the target belongs to the organization and returning a meaningful error.

**Why:** DRF permission classes are intended to gate based on the actor's capabilities, not validate the target's existence. Returning `False` would produce a 403 "You cannot manage a user at or above your own level" — a misleading message when the real issue is "that user doesn't exist in this org."

## Security note

This design creates a defense-in-depth gap: if a view using `CanManageTargetUser` fails to re-validate org membership of the target, a cross-org operation could slip through. All views that use this permission class **must** verify the target's org membership explicitly. See `accounts/views/rbac_views.py` for the pattern.

## Consequences

- Views get descriptive error messages for non-existent targets
- Views using `CanManageTargetUser` carry the responsibility to re-check org membership
- The guard is documented in `docs/specs/accounts_rbac.md` and tested in `accounts/formal_tests/`
