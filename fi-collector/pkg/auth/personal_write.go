package auth

import (
	"context"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"
)

// Mirror the WRITE boundary used by accounts authentication, not a guessed
// collector role: accounts/models/user.py:188-207,233-279 (global Admin/Owner,
// otherwise workspace role); accounts/api_key_policy.py:41-49 (level precedence);
// tfc/constants/levels.py:16-49 and roles.py:28-36 (the accepted roles/levels).
// Keep L2's separate, stricter explicit-membership requirement on pinned keys.
const personalWriteQuery = `
 SELECT m.role, m.level, wm.role, wm.level
 FROM accounts_organization_membership m
 JOIN accounts_user u ON u.id=m.user_id AND u.is_active
 JOIN accounts_workspace w ON w.id=$3 AND w.organization_id=m.organization_id
   AND w.is_active AND NOT w.deleted
 LEFT JOIN accounts_workspacemembership wm ON wm.user_id=m.user_id
   AND wm.workspace_id=w.id AND wm.is_active AND NOT wm.deleted
 WHERE m.user_id=$1 AND m.organization_id=$2 AND m.is_active AND NOT m.deleted
 LIMIT 1`

func (r *PGResolver) personalWriteAllowed(ctx context.Context, user, org, workspace string) (bool, error) {
	var orgRole string
	var wsRole *string
	var orgLevel, wsLevel *int
	err := r.write.QueryRow(ctx, personalWriteQuery, user, org, workspace).Scan(&orgRole, &orgLevel, &wsRole, &wsLevel)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("personal write eligibility: %w", err)
	}
	return personalWriteRolesAllowed(orgRole, orgLevel, wsRole, wsLevel), nil
}

func personalWriteRolesAllowed(orgRole string, orgLevel *int, wsRole *string, wsLevel *int) bool {
	if orgLevel == nil {
		switch orgRole {
		case "Owner", "Admin":
			return true
		}
	} else if *orgLevel == 15 || *orgLevel == 8 {
		return true
	}
	// No legacy home-org fallback, workspace auto-assignment, or default role.
	// An explicit level overrides a stale role string, including demotions.
	if wsRole == nil {
		return false
	}
	if wsLevel != nil {
		return *wsLevel == 8 || *wsLevel == 3
	}
	switch *wsRole {
	case "Owner", "Admin", "Member", "workspace_admin", "workspace_member":
		return true
	default:
		return false
	}
}

// Mirror accounts/services/workspace_landing.py's preference order, but apply
// the collector's WRITE boundary to every candidate rather than mere access.
// Preferences never grant membership. JSON access tolerates older row shapes.
const personalWorkspaceQuery = `
 SELECT w.id, m.role, m.level, wm.role, wm.level
 FROM accounts_organization_membership m
 JOIN accounts_user u ON u.id=m.user_id AND u.is_active
 JOIN accounts_workspace w ON w.organization_id=m.organization_id
   AND w.is_active AND NOT w.deleted
 LEFT JOIN accounts_workspacemembership wm ON wm.user_id=m.user_id
   AND wm.workspace_id=w.id AND wm.is_active AND NOT wm.deleted
 WHERE m.user_id=$1 AND m.organization_id=$2 AND m.is_active AND NOT m.deleted
 ORDER BY CASE
   WHEN w.id::text=lower(to_jsonb(u)->'config'->'orgWorkspaceMap'->>($2::text)) THEN 0
   WHEN w.id::text=lower(to_jsonb(u)->'config'->>'currentWorkspaceId') THEN 1
   WHEN w.id::text=lower(to_jsonb(u)->'config'->>'defaultWorkspaceId') THEN 2
   WHEN w.is_default THEN 3 ELSE 4 END,
   (to_jsonb(wm)->>'created_at')::timestamptz ASC NULLS LAST, w.id`

func (r *PGResolver) resolvePersonalWorkspace(ctx context.Context, user, org string) (string, error) {
	rows, err := r.write.Query(ctx, personalWorkspaceQuery, user, org)
	if err != nil {
		return "", fmt.Errorf("resolve personal workspace: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var workspace, orgRole string
		var wsRole *string
		var orgLevel, wsLevel *int
		if err := rows.Scan(&workspace, &orgRole, &orgLevel, &wsRole, &wsLevel); err != nil {
			return "", fmt.Errorf("scan personal workspace: %w", err)
		}
		if personalWriteRolesAllowed(orgRole, orgLevel, wsRole, wsLevel) {
			return workspace, nil
		}
	}
	return "", rows.Err()
}
