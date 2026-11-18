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
	allow              policyNames
	deny               policyNames
	tenantStore        *tenant.Store
}

// New creates a new tool policy plugin from config.
func New(cfg config.ToolPolicyConfig, tenantStore *tenant.Store) *Plugin {
	p := &Plugin{
		enabled:            cfg.Enabled,
		maxToolsPerRequest: cfg.MaxToolsPerRequest,
		defaultAction:      cfg.DefaultAction,
		allow:              newPolicyNames(cfg.Allow),
		deny:               newPolicyNames(cfg.Deny),
		tenantStore:        tenantStore,
	}
	if p.defaultAction == "" {
		p.defaultAction = "strip"
	}
	return p
}

// policyNames is one allow or deny list, held as two sets because a name means
// a different thing depending on what it is matched against.
//
// exact holds the names as the operator wrote them, and an application
// function is matched by that alone — two spellings of a function name are two
// different functions, and an application may legitimately call one of its own
// "codeExecution".
//
// builtin holds the canonical form of the entries that name a tool the
// provider runs itself, and such a tool is matched by that alone — two
// spellings of google_search are one capability, so a list naming either
// covers it.
//
// Merging the two is what let a rule about the built-in code_execution reach a
// custom function of the same name, in both directions.
type policyNames struct {
	exact   map[string]bool
	builtin map[string]bool
}

func newPolicyNames(names []string) policyNames {
	p := policyNames{
		exact:   make(map[string]bool, len(names)),
		builtin: make(map[string]bool, len(names)),
	}
	for _, name := range names {
		p.add(name)
	}
	return p
}

func (p policyNames) add(name string) {
	p.exact[name] = true
	if models.IsBuiltinCapability(name) {
		p.builtin[models.CanonicalCapability(name)] = true
	}
}

// has reports whether this list covers the tool.
func (p policyNames) has(tool toolRef) bool {
	if tool.builtin {
		return p.builtin[tool.name]
	}
	return p.exact[tool.name]
}

// empty reports a list that was never configured, which an allowlist check
// reads as "no allowlist" rather than "allow nothing".
func (p policyNames) empty() bool { return len(p.exact) == 0 }

// toolRef is one tool to decide about, and whether alias equivalence applies.
type toolRef struct {
	name    string
	builtin bool
}

func (p *Plugin) Name() string  { return "toolpolicy" }
func (p *Plugin) Priority() int { return 60 } // After guardrails (50), before validation (70).

// ProcessRequest filters tools based on policy (global → org → per-key).
func (p *Plugin) ProcessRequest(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	// Embeddings, images, audio, ocr, rerank and search run the pipeline with
	// rc.Request nil; none can carry tools, and dereferencing here 500'd them.
	if rc.Request == nil || len(rc.Request.Tools) == 0 {
		return pipeline.ResultContinue()
	}

	// Resolve per-org tool policy.
	var orgAllow policyNames
	var orgDeny policyNames
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
					orgAllow = newPolicyNames(orgCfg.ToolPolicy.Allow)
				}
				if len(orgCfg.ToolPolicy.Deny) > 0 {
					orgDeny = newPolicyNames(orgCfg.ToolPolicy.Deny)
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
		// An entry that declares a non-function type and a function name asks
		// for two things at once. Enforcing either one would leave the other
		// unchecked, so it is refused rather than resolved.
		if tool.ContradictsItsType() {
			return pipeline.ResultError(models.ErrBadRequest(
				"contradictory_tool",
				fmt.Sprintf("tool declares type %q and function %q; a tools entry may name only one capability",
					tool.Type, tool.Function.Name),
			))
		}

		// The capability the provider will actually forward, and whether alias
		// equivalence applies to it. Reading Function.Name here instead let a
		// policy approve one capability while the provider sent another.
		ref := toolRef{name: tool.Capability(), builtin: tool.NamesBuiltinCapability()}
		name := ref.name

		// Check global deny list first (highest precedence).
		if p.enabled && p.deny.has(ref) {
			if effectiveAction == "reject" {
				return pipeline.ResultError(models.ErrForbidden(
					fmt.Sprintf("tool %q is not allowed by policy", name),
				))
			}
			continue // strip
		}

		// Check org deny list.
		if orgDeny.has(ref) {
			if effectiveAction == "reject" {
				return pipeline.ResultError(models.ErrForbidden(
					fmt.Sprintf("tool %q is not allowed by org policy", name),
				))
			}
			continue // strip
		}

		// Check per-key deny list.
		if keyDeny.has(ref) {
			if effectiveAction == "reject" {
				return pipeline.ResultError(models.ErrForbidden(
					fmt.Sprintf("tool %q is not allowed for this API key", name),
				))
			}
			continue // strip
		}

		// Check global allow list (if set, only listed tools pass).
		if p.enabled && !p.allow.empty() && !p.allow.has(ref) {
			if effectiveAction == "reject" {
				return pipeline.ResultError(models.ErrForbidden(
					fmt.Sprintf("tool %q is not in the allowed list", name),
				))
			}
			continue // strip
		}

		// Check org allow list (if set, only listed tools pass).
		if !orgAllow.empty() && !orgAllow.has(ref) {
			if effectiveAction == "reject" {
				return pipeline.ResultError(models.ErrForbidden(
					fmt.Sprintf("tool %q is not in the org allowed list", name),
				))
			}
			continue // strip
		}

		// Check per-key allow list (if set, only listed tools pass).
		if !keyAllow.empty() && !keyAllow.has(ref) {
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
func parseCSV(s string) policyNames {
	if s == "" {
		return policyNames{}
	}
	parts := strings.Split(s, ",")
	m := newPolicyNames(nil)
	for _, p := range parts {
		if t := strings.TrimSpace(p); t != "" {
			m.add(t)
		}
	}
	return m
}
