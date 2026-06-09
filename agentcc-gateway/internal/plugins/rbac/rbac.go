package rbac

import (
	"context"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	rbacpkg "github.com/futureagi/agentcc-gateway/internal/rbac"
)

// Plugin enforces role-based access control as a pipeline plugin.
// Priority 30: runs after auth (20), before guardrails and other plugins.
type Plugin struct {
	store   *rbacpkg.Store
	enabled bool
}

// New creates a new RBAC plugin.
func New(store *rbacpkg.Store, enabled bool) *Plugin {
	return &Plugin{
		store:   store,
		enabled: enabled,
	}
}

func (p *Plugin) Name() string  { return "rbac" }
func (p *Plugin) Priority() int { return 30 } // After auth (20), before budget/guardrails.

// ProcessRequest checks role-based permissions.
func (p *Plugin) ProcessRequest(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !p.enabled || p.store == nil {
		return pipeline.ResultContinue()
	}

	// Resolve user identity from auth metadata.
	team := rc.Metadata["key_team"]
	userID := rc.Metadata["auth_key_owner"]
	keyRole := rc.Metadata["key_role"]

	// Determine effective role.
	role := p.store.ResolveRole(team, userID, keyRole)
	rc.Metadata["rbac_role"] = role
	rc.Metadata["rbac_team"] = team

	// Check model access permission.
	if rc.Model != "" {
		modelPerm := rbacpkg.Permission("models:" + rc.Model)
		if !p.store.HasPermission(role, modelPerm) {
			return pipeline.ResultError(models.ErrForbidden(
				"RBAC: role '" + role + "' does not have access to model '" + rc.Model + "'",
			))
		}

		// Also check team-level model restrictions (admins bypass).
		if team != "" && !p.store.HasPermission(role, "*") && !p.store.TeamModelAllowed(team, rc.Model) {
			return pipeline.ResultError(models.ErrForbidden(
				"RBAC: team '" + team + "' does not have access to model '" + rc.Model + "'",
			))
		}
	}

	// Check provider access permission.
	if rc.Provider != "" {
		providerPerm := rbacpkg.Permission("providers:" + rc.Provider)
		if !p.store.HasPermission(role, providerPerm) {
			return pipeline.ResultError(models.ErrForbidden(
				"RBAC: role '" + role + "' does not have access to provider '" + rc.Provider + "'",
			))
		}
	}

	// Check guardrail override permission.
	if rc.Metadata["x-guardrail-policy"] != "" {
		if !p.store.HasPermission(role, "guardrails:override") {
			return pipeline.ResultError(models.ErrForbidden(
				"RBAC: role '" + role + "' cannot override guardrail policies",
			))
		}
	}

	return pipeline.ResultContinue()
}

// ProcessResponse is a no-op for RBAC.
func (p *Plugin) ProcessResponse(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
