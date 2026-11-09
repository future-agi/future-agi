package mcp

import (
	"context"
	"fmt"
	"regexp"
	"sort"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// ToolLimits enforces explicitly configured, non-dollar virtual-server limits.
// No configured limits means no Redis access. Configured limits fail closed
// without shared state; there is deliberately no process-local fallback.
type ToolLimits struct {
	store *redisstate.ToolCallBudgetStore
}

func NewToolLimits(store *redisstate.ToolCallBudgetStore) *ToolLimits { return &ToolLimits{store} }

func callLimits(cc *CallContext) tenant.MCPVirtualServerBudgets {
	if v := callViewOf(cc); v != nil {
		return v.vs.Budgets
	}
	if vs, ok := cc.OrgConfig.MCPVirtualServer(cc.VirtualServer); ok {
		return vs.Budgets
	}
	return tenant.MCPVirtualServerBudgets{}
}
func matchingLimits(cc *CallContext, cfg tenant.MCPVirtualServerBudgets) []string {
	names := []string{cc.Upstream.ServerID + "/" + cc.Tool.UpstreamName}
	if view := callViewOf(cc); view != nil && view.tools != nil {
		if entry, ok := view.tools.Lookup(cc.Tool.ExposedName); ok {
			names = view.names(entry).all
		}
	}
	patterns := []string{}
	for pattern, rule := range cfg.PerTool {
		if rule != nil && vserver.Any([]string{pattern}, names...) {
			patterns = append(patterns, pattern)
		}
	}
	sort.Strings(patterns)
	return patterns
}
func limitUnavailable() *Denial {
	return &Denial{Type: models.ErrTypeServer, Code: "policy_unavailable", Message: "Tool limits could not be checked. No tool call was sent."}
}

// limitBlocked refuses a call whose rate limit is configured as zero. Zero is
// a block, not a window: no retry can succeed until an admin changes the
// rule, so the refusal is tool_denied with no retry hint, and it reads or
// writes no shared state.
func limitBlocked(cc *CallContext, rule string) *Denial {
	d := toolDenied(cc, rule)
	d.Message = fmt.Sprintf("Tool %q is blocked: %s is 0 calls per minute. No tool call was sent. Ask an administrator to raise the limit.", cc.Tool.ExposedName, rule)
	if meta, ok := d.Metadata.(map[string]any); ok {
		meta["reason"] = "rate_limit_zero"
	}
	return d
}

// scopeTTL is the run scope's TTL, routing.agents.budget_scope_ttl_seconds
// (86400 by default), which V6's REST capability counters and G19's run
// scopes slide to as well.
func scopeTTL(cc *CallContext) time.Duration {
	if s := cc.OrgConfig.AgentsConfig().BudgetScopeTTLSeconds; s > 0 {
		return time.Duration(s) * time.Second
	}
	return 24 * time.Hour
}
func limitDigest(parts ...string) string {
	// Length framing avoids ambiguous identities containing delimiters.
	text := ""
	for _, part := range parts {
		text += fmt.Sprintf("%d:%s", len(part), part)
	}
	return approvalHash([]byte(text))
}
func (l *ToolLimits) Admit(cc *CallContext) (func(), *Denial) {
	cfg := callLimits(cc)
	limits := []redisstate.ToolCallLimit{}
	scopes, rules := []string{}, []string{}
	prefix := "mrl:{" + cc.Principal.OrgID + "}:"
	if cfg.RPMPerKey != nil {
		who := cc.Principal.KeyID
		if who == "" {
			who = approvalOwner(cc.Principal)
		}
		limits = append(limits, redisstate.ToolCallLimit{Key: prefix + limitDigest("key", cc.VirtualServer, who), Limit: int64(*cfg.RPMPerKey)})
		scopes, rules = append(scopes, "key:"+who), append(rules, "budgets.rpm_per_key")
	}
	for _, p := range matchingLimits(cc, cfg) {
		if rpm := cfg.PerTool[p].RPM; rpm != nil {
			limits = append(limits, redisstate.ToolCallLimit{Key: prefix + limitDigest("tool", cc.VirtualServer, approvalOwner(cc.Principal), cc.Upstream.ServerID, cc.Tool.UpstreamName, p), Limit: int64(*rpm)})
			scopes, rules = append(scopes, "tool:"+cc.Upstream.ServerID+"/"+cc.Tool.UpstreamName), append(rules, "budgets.per_tool."+p+".rpm")
		}
	}
	if len(limits) == 0 {
		return func() {}, nil
	}
	for i, rule := range limits {
		if rule.Limit < 0 {
			return nil, limitUnavailable()
		}
		if rule.Limit == 0 {
			return nil, limitBlocked(cc, rules[i])
		}
	}
	if !validApprovalPrincipal(cc.Principal) || cc.VirtualServer == "" {
		return nil, limitUnavailable()
	}
	index, retry, err := l.store.Rate(cc.Context(), limits)
	if err != nil {
		return nil, limitUnavailable()
	}
	if index >= 0 {
		return nil, &Denial{Type: models.ErrTypeRateLimit, Code: "tool_rate_limited", Message: fmt.Sprintf("Tool rate limit reached. Try again in ~%ds", retry), Metadata: map[string]any{"scope": scopes[index], "limit": limits[index].Limit, "window_s": 60, "retry_after_s": retry}}
	}
	return func() {}, nil
}

var toolCallID = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,256}$`)

func (l *ToolLimits) Hold(cc *CallContext, _ int64) (BudgetHold, *Denial) {
	cfg := callLimits(cc)
	patterns := matchingLimits(cc, cfg)
	limited := cfg.MaxToolCallsPerRun != nil
	for _, pattern := range patterns {
		limited = limited || cfg.PerTool[pattern].MaxCallsPerRun != nil
	}
	// Once this virtual server has any run ceiling, unmatched tools in a
	// named run are activity too. An unmatched call outside a run still has
	// no ceiling to enforce and must not acquire a new scope requirement.
	active := limited
	for _, rule := range cfg.PerTool {
		active = active || (rule != nil && rule.MaxCallsPerRun != nil)
	}
	scope := cc.Agent.BudgetScope
	if scope == "" {
		scope = cc.Agent.RunID
	}
	if !active || (!limited && scope == "") {
		return noHold{}, nil
	}
	if !validApprovalPrincipal(cc.Principal) || !models.ValidAgentID(scope) || !toolCallID.MatchString(cc.ToolCallID) || cc.VirtualServer == "" {
		return nil, limitUnavailable()
	}
	cc.Budget.Scope = scope
	// The run counter is MC.6's mtb:{<org>}:<scope>:calls with the raw scope,
	// exactly the key V6's REST capability calls count on (GE MA5), so one
	// free-tool call budget covers both. A canonical scope (ValidAgentID) is
	// also a valid V6 scope, and the org stays the Redis Cluster hash tag.
	// Holds and per-tool counters are fields in the run's :state hash, so
	// they share run expiry rather than each tool's last-call expiry. Their
	// digest identities remain unambiguous across scopes and tool patterns.
	run := "mtb:{" + cc.Principal.OrgID + "}:" + scope + ":calls"
	max := int64(-1)
	if cfg.MaxToolCallsPerRun != nil {
		max = int64(*cfg.MaxToolCallsPerRun)
		if max < 0 {
			return nil, limitUnavailable()
		}
	}
	limits := []redisstate.ToolCallLimit{{Key: run, Limit: max}}
	for _, p := range patterns {
		if cap := cfg.PerTool[p].MaxCallsPerRun; cap != nil {
			if *cap < 0 {
				return nil, limitUnavailable()
			}
			limits = append(limits, redisstate.ToolCallLimit{Key: run + ":tool:" + limitDigest(cc.VirtualServer, p), Limit: int64(*cap)})
		}
	}
	hold := run + ":hold:" + limitDigest(cc.ToolCallID)
	ttl := scopeTTL(cc)
	index, remaining, err := l.store.Reserve(cc.Context(), hold, limits, ttl)
	if err != nil || index == -2 {
		return nil, limitUnavailable()
	}
	meta := map[string]any{"scope": scope}
	cc.Budget.CallsRemaining = nil
	if remaining >= 0 {
		cc.Budget.CallsRemaining = &remaining
		meta["calls_remaining"] = remaining
	}
	cc.SetResponseMeta("io.futureagi/budget", meta)
	if index >= 0 {
		return nil, &Denial{Type: models.ErrTypeRateLimit, Code: "tool_budget_exhausted", Message: "The run's tool call budget is exhausted. No tool call was sent.", Metadata: map[string]any{"limit_source": "agentcc_tool_budget", "remedy_hint": "Ask an administrator to increase this run's tool call limit.", "scope": "run:" + scope, "resets_at": nil, "kind": "calls", "remaining": 0}}
	}
	return &toolCountHold{store: l.store, id: hold, limits: limits, ttl: ttl}, nil
}

type toolCountHold struct {
	store  *redisstate.ToolCallBudgetStore
	id     string
	limits []redisstate.ToolCallLimit
	ttl    time.Duration
	once   sync.Once
}

func (h *toolCountHold) finish(executed bool) {
	h.once.Do(func() {
		// Cancellation of the incoming request must not cancel its finalization.
		// Failure conservatively leaves the reserved count in place.
		_ = h.store.Finish(context.Background(), h.id, h.limits, executed, h.ttl)
	})
}
func (h *toolCountHold) Settle(int64) { h.finish(true) }
func (h *toolCountHold) Void()        { h.finish(false) }
