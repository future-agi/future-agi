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
	if orgLevel == nil {
		switch orgRole {
		case "Owner", "Admin":
			return true, nil
		}
	} else if *orgLevel == 15 || *orgLevel == 8 {
		return true, nil
	}
	// No legacy home-org fallback, workspace auto-assignment, or default role.
	// An explicit level overrides a stale role string, including demotions.
	if wsRole == nil {
		return false, nil
	}
	if wsLevel != nil {
		return *wsLevel == 8 || *wsLevel == 3, nil
	}
	switch *wsRole {
	case "Owner", "Admin", "Member", "workspace_admin", "workspace_member":
		return true, nil
	default:
		return false, nil
	}
}
