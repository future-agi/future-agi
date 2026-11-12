package auth

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

// grantRecord mirrors accounts_service_grant. No credential is stored there.
// The column contract is exercised against the issuer migration and vectors.
type grantRecord struct {
	GrantClaims
	RevokedAt *string `json:"revoked_at"`
	AgentID   *string `json:"agent_id"`
	AttemptID *string `json:"attempt_id"`
	BatchRef  *string `json:"batch_ref"`
}

func (r grantRecord) matches(c *GrantClaims) bool {
	if c == nil || r.GrantClaims != *c || r.RevokedAt != nil || !c.validKind() || c.Kind != "sandbox" {
		return false
	}
	subject := strings.Split(c.Sub, ":")
	return r.AgentID != nil && *r.AgentID == subject[1] && r.AttemptID != nil && *r.AttemptID == subject[2] && r.BatchRef == nil
}

type grantLiveness struct {
	OrgActive       bool   `json:"org_active"`
	WorkspaceActive bool   `json:"workspace_active"`
	UserActive      bool   `json:"user_active"`
	OrgMember       bool   `json:"org_member"`
	WorkspaceMember bool   `json:"workspace_member"`
	AgentActive     bool   `json:"agent_active"`
	AttemptActive   bool   `json:"attempt_active"`
	AgentID         string `json:"agent_id"`
	AttemptID       string `json:"attempt_id"`
	AgentOrg        string `json:"agent_org"`
	AgentWorkspace  string `json:"agent_workspace"`
}

func (l grantLiveness) allows(c *GrantClaims) bool {
	if !l.OrgActive || (c.Workspace != "-" && !l.WorkspaceActive) {
		return false
	}
	if c.Kind == "platform-emit" {
		return true
	}
	if c.Kind != "sandbox" || !l.UserActive || !l.OrgMember || (c.Workspace != "-" && !l.WorkspaceMember) || !l.AgentActive || !l.AttemptActive {
		return false
	}
	sub := strings.Split(c.Sub, ":")
	return len(sub) == 3 && l.AgentID == sub[1] && l.AttemptID == sub[2] && l.AgentOrg == c.Org && l.AgentWorkspace == c.Workspace
}

// Organization is hard-deleted (it has no deleted/is_active columns). Its
// presence in the inner join establishes liveness; workspaces are soft-deleted.
const grantMemberQuery = `
 SELECT o.id IS NOT NULL, COALESCE(w.is_active AND NOT w.deleted, false), u.is_active,
        m.is_active AND NOT m.deleted, COALESCE(wm.is_active AND NOT wm.deleted, false), NOT a.deleted,
        NOT att.deleted AND att.state IN ('registered','provisioning','running','finalizing','cleaning_up')
          AND att.expires_at + interval '10 minutes' > to_timestamp($6)
          AND NOT j.deleted AND j.cancel_requested_at IS NULL AND j.terminal_at IS NULL
          AND j.organization_id=$1 AND j.workspace_id IS NOT DISTINCT FROM $2::uuid,
        a.id::text, att.id::text, a.organization_id::text, COALESCE(a.workspace_id::text, '-')
 FROM accounts_organization_membership m
 JOIN accounts_user u ON u.id=m.user_id
 JOIN accounts_organization o ON o.id=m.organization_id
 LEFT JOIN accounts_workspace w ON w.organization_id=o.id AND w.id=$2
 LEFT JOIN accounts_workspacemembership wm ON wm.workspace_id=w.id AND wm.user_id=m.user_id
 JOIN simulate_agent_definition a ON a.id=$4
 JOIN simulate_hosted_harness_attempt att ON att.id=$5
 JOIN simulate_hosted_harness_job j ON j.id=att.job_id
 WHERE m.organization_id=$1 AND m.user_id=$3
 LIMIT 1`

// ValidateGrant reads both the registry and live eligibility from the primary.
// A read-replica result must never replenish the authentication cache.
func (r *PGResolver) ValidateGrant(ctx context.Context, c *GrantClaims) (*ResolveResult, error) {
	if c == nil || !c.validKind() || time.Now().Unix() >= c.Exp {
		return nil, nil
	}
	workspace := c.Workspace
	if workspace == "-" {
		workspace = ""
	}
	principal := PrincipalPlatform
	var agent SimulationAgent
	switch c.Kind {
	case "sandbox":
		var raw []byte
		err := r.write.QueryRow(ctx, `SELECT to_jsonb(g) || jsonb_build_object('workspace', COALESCE(g.workspace::text, '-'), 'user', COALESCE(g."user"::text, '-')) FROM accounts_service_grant g WHERE g.jti=$1 AND g.revoked_at IS NULL AND g.exp > $2`, c.ID, time.Now().Unix()).Scan(&raw)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, nil
		}
		if err != nil {
			return nil, fmt.Errorf("resolve grant: %w", err)
		}
		var row grantRecord
		if err := json.Unmarshal(raw, &row); err != nil {
			return nil, fmt.Errorf("decode grant record: %w", err)
		}
		if !row.matches(c) {
			return nil, nil
		}
		var live grantLiveness
		err = r.write.QueryRow(ctx, grantMemberQuery, c.Org, nullableClaim(c.Workspace), c.User, *row.AgentID, *row.AttemptID, time.Now().Unix()).Scan(
			&live.OrgActive, &live.WorkspaceActive, &live.UserActive, &live.OrgMember, &live.WorkspaceMember,
			&live.AgentActive, &live.AttemptActive, &live.AgentID, &live.AttemptID, &live.AgentOrg, &live.AgentWorkspace)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, nil
		}
		if err != nil {
			return nil, fmt.Errorf("resolve grant eligibility: %w", err)
		}
		if !live.allows(c) {
			return nil, nil
		}
		principal = PrincipalSandbox
		agent = SimulationAgent{ID: *row.AgentID, OrgID: c.Org}
	case "platform-emit":
		var live grantLiveness
		err := r.write.QueryRow(ctx, `SELECT
          EXISTS (SELECT 1 FROM accounts_organization o WHERE o.id=$1),
          EXISTS (SELECT 1 FROM accounts_workspace w WHERE w.id=$2 AND w.organization_id=$1 AND w.is_active AND NOT w.deleted)`, c.Org, nullableClaim(c.Workspace)).Scan(&live.OrgActive, &live.WorkspaceActive)
		if err != nil {
			return nil, fmt.Errorf("resolve platform grant: %w", err)
		}
		if !live.allows(c) {
			return nil, nil
		}
	default:
		return nil, nil
	}
	if workspace == "" {
		var err error
		workspace, err = r.resolveDefaultWorkspace(ctx, c.Org)
		if err != nil {
			return nil, err
		}
	}
	user := c.User
	if user == "-" {
		user = ""
	}
	copyClaims := *c
	return &ResolveResult{OrgID: c.Org, WorkspaceID: workspace, UserID: user, Principal: principal, Grant: &copyClaims, SimulationAgent: agent, Projects: make(map[string]string)}, nil
}

func nullableClaim(s string) any {
	if s == "-" {
		return nil
	}
	return s
}
