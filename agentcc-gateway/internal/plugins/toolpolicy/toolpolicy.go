package toolpolicy

import (
	"context"
	"fmt"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Plugin implements tool/function filtering as a pipeline plugin.
// Priority 60: after guardrails (50), before validation (70).
type Plugin struct {
	enabled            bool
	maxToolsPerRequest int
	defaultAction      string // "strip" or "reject"
	allow              map[string]bool
	deny               map[string]bool
	tenantStore        *tenant.Store
}

// New creates a new tool policy plugin from config.
func New(cfg config.ToolPolicyConfig, tenantStore *tenant.Store) *Plugin {
	p := &Plugin{
		enabled:            cfg.Enabled,
		maxToolsPerRequest: cfg.MaxToolsPerRequest,
		defaultAction:      cfg.DefaultAction,
		allow:              make(map[string]bool, len(cfg.Allow)),
		deny:               make(map[string]bool, len(cfg.Deny)),
		tenantStore:        tenantStore,
	}
	if p.defaultAction == "" {
		p.defaultAction = "strip"
	}
	for _, name := range cfg.Allow {
		p.allow[name] = true
	}
	for _, name := range cfg.Deny {
		p.deny[name] = true
	}
	return p
}

func (p *Plugin) Name() string  { return "toolpolicy" }
func (p *Plugin) Priority() int { return 60 } // After guardrails (50), before validation (70).

// ProcessRequest filters tools based on policy (global → org → per-key).
func (p *Plugin) ProcessRequest(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if len(rc.Request.Tools) == 0 {
		return pipeline.ResultContinue()
	}

	// Resolve per-org tool policy.
	var orgAllow map[string]bool
	var orgDeny map[string]bool
	orgAction := ""
	orgMaxTools := 0
	orgEnabled := false

	if p.tenantStore != nil {
		orgID := rc.Metadata[tenant.MetadataKeyOrgID]
		if orgID != "" {
			orgCfg := p.tenantStore.Get(orgID)
			if orgCfg != nil && orgCfg.ToolPolicy != nil && orgCfg.ToolPolicy.Enabled {
				orgEnabled = true
				orgAction = orgCfg.ToolPolicy.DefaultAction
				orgMaxTools = orgCfg.ToolPolicy.MaxToolsPerRequest
				if len(orgCfg.ToolPolicy.Allow) > 0 {
					orgAllow = make(map[string]bool, len(orgCfg.ToolPolicy.Allow))
					for _, name := range orgCfg.ToolPolicy.Allow {
						orgAllow[name] = true
					}
				}
				if len(orgCfg.ToolPolicy.Deny) > 0 {
					orgDeny = make(map[string]bool, len(orgCfg.ToolPolicy.Deny))
					for _, name := range orgCfg.ToolPolicy.Deny {
						orgDeny[name] = true
					}
				}
			}
		}
	}

	// If neither global nor org policy is enabled, skip.
	if !p.enabled && !orgEnabled {
		return pipeline.ResultContinue()
	}

	toolCount := len(rc.Request.Tools)
	rc.Metadata["tools_requested"] = strconv.Itoa(toolCount)

	// Effective action: org overrides global if set.
	effectiveAction := p.defaultAction
	if orgAction != "" {
		effectiveAction = orgAction
	}

	// Check max tools per request (most restrictive wins).
	effectiveMaxTools := p.maxToolsPerRequest
	if orgMaxTools > 0 && (effectiveMaxTools == 0 || orgMaxTools < effectiveMaxTools) {
		effectiveMaxTools = orgMaxTools
	}
	if effectiveMaxTools > 0 && toolCount > effectiveMaxTools {
		return pipeline.ResultError(models.ErrBadRequest(
			"too_many_tools",
			fmt.Sprintf("too many tools: %d exceeds maximum of %d", toolCount, effectiveMaxTools),
		))
	}

	// Build per-key allow/deny sets from auth metadata.
	keyAllow := parseCSV(rc.Metadata["auth_allowed_tools"])
	keyDeny := parseCSV(rc.Metadata["auth_denied_tools"])

	// Filter tools: global deny → org deny → per-key deny → global allow → org allow → per-key allow.
	filtered := make([]models.Tool, 0, len(rc.Request.Tools))
	for _, tool := range rc.Request.Tools {
		name := tool.Function.Name

		// Check global deny list first (highest precedence).
		if p.enabled && p.deny[name] {
			if effectiveAction == "reject" {
				return pipeline.ResultError(models.ErrForbidden(
					fmt.Sprintf("tool %q is not allowed by policy", name),
				))
			}
			continue // strip
		}

		// Check org deny list.
		if orgDeny[name] {
			if effectiveAction == "reject" {
				return pipeline.ResultError(models.ErrForbidden(
					fmt.Sprintf("tool %q is not allowed by org policy", name),
				))
			}
			continue // strip
		}

		// Check per-key deny list.
		if keyDeny[name] {
			if effectiveAction == "reject" {
				return pipeline.ResultError(models.ErrForbidden(
					fmt.Sprintf("tool %q is not allowed for this API key", name),
				))
			}
			continue // strip
		}

		// Check global allow list (if set, only listed tools pass).
		if p.enabled && len(p.allow) > 0 && !p.allow[name] {
			if effectiveAction == "reject" {
				return pipeline.ResultError(models.ErrForbidden(
					fmt.Sprintf("tool %q is not in the allowed list", name),
				))
			}
			continue // strip
		}

		// Check org allow list (if set, only listed tools pass).
		if len(orgAllow) > 0 && !orgAllow[name] {
			if effectiveAction == "reject" {
				return pipeline.ResultError(models.ErrForbidden(
					fmt.Sprintf("tool %q is not in the org allowed list", name),
				))
			}
			continue // strip
		}

		// Check per-key allow list (if set, only listed tools pass).
		if len(keyAllow) > 0 && !keyAllow[name] {
			if effectiveAction == "reject" {
				return pipeline.ResultError(models.ErrForbidden(
					fmt.Sprintf("tool %q is not allowed for this API key", name),
				))
			}
			continue // strip
		}

		filtered = append(filtered, tool)
	}

	removedCount := toolCount - len(filtered)
	if removedCount > 0 {
		rc.Metadata["tools_filtered"] = strconv.Itoa(removedCount)
	}

	rc.Request.Tools = filtered
	return pipeline.ResultContinue()
}

// ProcessResponse is a no-op.
func (p *Plugin) ProcessResponse(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// parseCSV splits a CSV string into a set, ignoring empty strings.
func parseCSV(s string) map[string]bool {
	if s == "" {
		return nil
	}
	parts := strings.Split(s, ",")
	m := make(map[string]bool, len(parts))
	for _, p := range parts {
		if t := strings.TrimSpace(p); t != "" {
			m[t] = true
		}
	}
	return m
}
