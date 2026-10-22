package tenant

import (
	"sort"
	"time"
)

// This file holds the org config mcp v2 types and the accessors that apply
// their defaults. The admin
// schema states each default in its definition's description; read v2
// settings through these accessors so every reader agrees on them.
// TestMCPAccessorDefaults pins them.

// MCPAccessConfig is what a key bound to no virtual server may use.
type MCPAccessConfig struct {
	Default string `json:"default,omitempty"` // "deny" (default) or "allow"
}

// MCPServerCredential is a server's upstream credential.
type MCPServerCredential struct {
	Mode         string `json:"mode,omitempty"` // none (default), shared, per_user, exchange, id_jag, vendor_end_user
	CredentialID string `json:"credential_id,omitempty"`
	Header       string `json:"header,omitempty"`
	State        string `json:"state,omitempty"` // active (default), revoked, expired, needs_reauth

	// Secret exists only in a pushed payload: assembled by the control plane
	// at push time and never stored there.
	Secret *MCPCredentialSecret `json:"secret,omitempty"`

	// Vendor and IdentityBinding are for mode vendor_end_user.
	Vendor          string              `json:"vendor,omitempty"`
	IdentityBinding *MCPIdentityBinding `json:"identity_binding,omitempty"`
}

// MCPCredentialSecret is a static upstream secret (push-only).
type MCPCredentialSecret struct {
	Type   string `json:"type"`
	Header string `json:"header,omitempty"`
	Value  string `json:"value"`
}

// MCPIdentityBinding is where a vendor_end_user server takes the end user's id.
type MCPIdentityBinding struct {
	In   string `json:"in"` // header, query or path
	Name string `json:"name"`
}

// MCPServerOAuthConfig selects the upstream OAuth adapter.
type MCPServerOAuthConfig struct {
	Adapter string `json:"adapter,omitempty"` // "auto" (default)
}

// MCPVirtualServerConfig is one virtual server, keyed by its slug.
type MCPVirtualServerConfig struct {
	ID           string                      `json:"id,omitempty"` // vs_<ULID>, assigned by the control plane
	Title        string                      `json:"title,omitempty"`
	Description  string                      `json:"description,omitempty"`
	Members      []string                    `json:"members,omitempty"`
	Tools        *MCPToolFilter              `json:"tools,omitempty"`
	Overrides    map[string]*MCPToolOverride `json:"overrides,omitempty"`
	Naming       string                      `json:"naming,omitempty"` // "prefixed" (default) or "flat"
	Mode         string                      `json:"mode,omitempty"`   // "direct" (default) or "search"
	StepProfiles map[string]*MCPStepProfile  `json:"step_profiles,omitempty"`
	Access       *MCPVirtualServerAccess     `json:"access,omitempty"`
	Approval     *MCPApprovalConfig          `json:"approval,omitempty"`
	Budgets      *MCPVirtualServerBudgets    `json:"budgets,omitempty"`
	CacheScope   string                      `json:"cache_scope,omitempty"` // "private" (default)
}

// MCPToolFilter narrows a virtual server's tools by glob.
type MCPToolFilter struct {
	Include []string `json:"include,omitempty"`
	Exclude []string `json:"exclude,omitempty"`
}

// MCPToolOverride changes how one tool is presented, keyed <server_id>/<tool>.
type MCPToolOverride struct {
	Name        string   `json:"name,omitempty"`
	Title       string   `json:"title,omitempty"`
	Description string   `json:"description,omitempty"`
	Hidden      *bool    `json:"hidden,omitempty"`
	Labels      []string `json:"labels,omitempty"`
}

// MCPStepProfile is the tool set offered at one agent step.
type MCPStepProfile struct {
	Include []string `json:"include,omitempty"`
}

// MCPVirtualServerAccess says who may use a virtual server.
type MCPVirtualServerAccess struct {
	Keys          []string         `json:"keys,omitempty"`
	Agents        []string         `json:"agents,omitempty"`
	Users         []string         `json:"users,omitempty"`
	DefaultForOrg *bool            `json:"default_for_org,omitempty"`
	Rules         []*MCPAccessRule `json:"rules,omitempty"`
}

// MCPAccessRule narrows tools for the principals it matches.
type MCPAccessRule struct {
	Match *MCPAccessRuleMatch `json:"match"`
	Allow []string            `json:"allow,omitempty"`
	Deny  []string            `json:"deny,omitempty"`
}

// MCPAccessRuleMatch selects principals by key, agent or user.
type MCPAccessRuleMatch struct {
	KeyIDs   []string `json:"key_ids,omitempty"`
	AgentIDs []string `json:"agent_ids,omitempty"`
	UserIDs  []string `json:"user_ids,omitempty"`
}

// MCPApprovalConfig is a virtual server's approval policy.
type MCPApprovalConfig struct {
	Require    string   `json:"require,omitempty"`  // "none" (default), "destructive" or "listed"
	Tools      []string `json:"tools,omitempty"`    // globs, for require=listed
	Validity   string   `json:"validity,omitempty"` // "once" (default), "ttl" or "step"
	TTLSeconds *int     `json:"ttl_seconds,omitempty"`
}

// MCPVirtualServerBudgets caps tool calls per run and per key.
type MCPVirtualServerBudgets struct {
	MaxToolCallsPerRun *int                         `json:"max_tool_calls_per_run,omitempty"`
	MaxToolUSDPerRun   *float64                     `json:"max_tool_usd_per_run,omitempty"`
	RPMPerKey          *int                         `json:"rpm_per_key,omitempty"`
	PerTool            map[string]*MCPPerToolBudget `json:"per_tool,omitempty"`
}

// MCPPerToolBudget caps one tool glob.
type MCPPerToolBudget struct {
	MaxCallsPerRun *int `json:"max_calls_per_run,omitempty"`
	RPM            *int `json:"rpm,omitempty"`
}

// MCPManagedToolsConfig is the org's one managed opt-in for paid tools.
type MCPManagedToolsConfig struct {
	Enabled           *bool                                `json:"enabled,omitempty"`
	Allow             []string                             `json:"allow,omitempty"` // fagi.<id>/<tool> and <capability>/<vendor> globs
	Deny              []string                             `json:"deny,omitempty"`
	MaxCallCostUSD    *float64                             `json:"max_call_cost_usd,omitempty"`
	FallbackToManaged *bool                                `json:"fallback_to_managed,omitempty"`
	AllowUserTokens   *bool                                `json:"allow_user_tokens,omitempty"`
	Credentials       map[string]*MCPManagedToolCredential `json:"credentials,omitempty"`
}

// MCPManagedToolCredential is the org's own credential for a fagi.* server.
type MCPManagedToolCredential struct {
	Mode         string               `json:"mode,omitempty"` // shared or per_user
	CredentialID string               `json:"credential_id,omitempty"`
	State        string               `json:"state,omitempty"`
	Secret       *MCPCredentialSecret `json:"secret,omitempty"` // push-only
}

// MCPPinningConfig is tool pinning.
type MCPPinningConfig struct {
	Mode         string            `json:"mode,omitempty"`      // "quarantine" (default), "warn" or "off"
	NewTools     string            `json:"new_tools,omitempty"` // "quarantine" (default) or "allow"
	AutoBaseline *bool             `json:"auto_baseline,omitempty"`
	Pins         map[string]string `json:"pins,omitempty"` // <server_id>/<tool> -> sha256
}

// MCPScanningConfig is description, argument and result scanning.
type MCPScanningConfig struct {
	Descriptions string   `json:"descriptions,omitempty"` // "warn" (default), "off" or "block"
	Arguments    []string `json:"arguments,omitempty"`
	Results      []string `json:"results,omitempty"`
	Shadow       *bool    `json:"shadow,omitempty"`
}

// MCPFlowControlConfig is run-level information-flow control.
type MCPFlowControlConfig struct {
	Mode               string `json:"mode,omitempty"`                  // "shadow" (default), "off" or "enforce"
	OnEgressAfterTaint string `json:"on_egress_after_taint,omitempty"` // "approve" (default) or "deny"
}

// MCPPolicyConfig is tool policy v2.
type MCPPolicyConfig struct {
	Rules       []*MCPPolicyRule      `json:"rules,omitempty"`
	Combos      []*MCPPolicyCombo     `json:"combos,omitempty"`
	DefaultTier *MCPPolicyDefaultTier `json:"default_tier,omitempty"`
}

// MCPPolicyRule allows, approves or denies matching calls.
type MCPPolicyRule struct {
	ID     string          `json:"id"`
	Match  *MCPPolicyMatch `json:"match"`
	When   string          `json:"when,omitempty"`
	Action string          `json:"action"` // allow, approve or deny
	Reason string          `json:"reason"`
	Shadow *bool           `json:"shadow,omitempty"`
}

// MCPPolicyMatch selects the calls a rule covers.
type MCPPolicyMatch struct {
	Tools   []string `json:"tools"`
	Servers []string `json:"servers,omitempty"`
	Agents  []string `json:"agents,omitempty"`
	Steps   []string `json:"steps,omitempty"`
	Env     []string `json:"env,omitempty"`
}

// MCPPolicyCombo acts on a sequence of two tools within one run.
type MCPPolicyCombo struct {
	ID       string   `json:"id"`
	Sequence []string `json:"sequence"`
	Within   string   `json:"within"` // run
	Action   string   `json:"action"` // deny or approve
}

// MCPPolicyDefaultTier is the action per risk tier when no rule matches.
type MCPPolicyDefaultTier struct {
	ReadOnly        string `json:"read_only,omitempty"`
	Undeclared      string `json:"undeclared,omitempty"`
	Destructive     string `json:"destructive,omitempty"`
	DestructiveProd string `json:"destructive_prod,omitempty"`
}

// MCPShapingConfig is tool result shaping.
type MCPShapingConfig struct {
	PerTool map[string]*MCPShapingToolConfig `json:"per_tool,omitempty"`
}

// MCPShapingToolConfig shapes the results of one tool glob.
type MCPShapingToolConfig struct {
	BudgetBytes *int     `json:"budget_bytes,omitempty"`
	Project     []string `json:"project,omitempty"` // JSON pointers
}

func (c *OrgConfig) mcp() *MCPOrgConfig {
	if c == nil || c.MCP == nil {
		return &MCPOrgConfig{}
	}
	return c.MCP
}

// MCPAccessSettings is mcp.access with its default applied.
type MCPAccessSettings struct {
	Default string // "deny" or "allow"
}

// MCPAccess returns what a key bound to no virtual server may use.
func (c *OrgConfig) MCPAccess() MCPAccessSettings {
	var a MCPAccessConfig
	if m := c.mcp(); m.Access != nil {
		a = *m.Access
	}
	return MCPAccessSettings{Default: stringOr(a.Default, "deny")}
}

// MCPServerSettings is one mcp.servers entry with its defaults applied.
type MCPServerSettings struct {
	ID                   string
	URL                  string
	Transport            string
	Era                  string // "auto" unless pinned
	CredentialMode       string // "none" unless set
	CredentialState      string // "active" unless set
	Credential           MCPServerCredential
	Auth                 *MCPAuthOrgConfig // legacy inline credential, nil when unset
	TrustedAnnotations   bool
	TimeoutMs            int
	ToolsCacheTTLSeconds int
	ForwardHeaders       []string
	PropagateDepth       bool
	Enabled              bool
	OAuthAdapter         string // "auto" unless set
	Labels               []string
}

// MCPServer returns one org server with its defaults applied, and whether
// the org configures it.
func (c *OrgConfig) MCPServer(id string) (MCPServerSettings, bool) {
	s, ok := c.mcp().Servers[id]
	if !ok || s == nil {
		return mcpServerSettings(id, &MCPServerOrgConfig{}), false
	}
	return mcpServerSettings(id, s), true
}

func mcpServerSettings(id string, s *MCPServerOrgConfig) MCPServerSettings {
	var cred MCPServerCredential
	if s.Credential != nil {
		cred = *s.Credential
	}
	adapter := ""
	if s.OAuth != nil {
		adapter = s.OAuth.Adapter
	}
	return MCPServerSettings{
		ID:                   id,
		URL:                  s.URL,
		Transport:            s.Transport,
		Era:                  stringOr(s.Era, "auto"),
		CredentialMode:       stringOr(cred.Mode, "none"),
		CredentialState:      stringOr(cred.State, "active"),
		Credential:           cred,
		Auth:                 s.Auth,
		TrustedAnnotations:   boolOr(s.TrustedAnnotations, false),
		TimeoutMs:            intOr(s.TimeoutMs, 30000),
		ToolsCacheTTLSeconds: toolsCacheTTLSeconds(s),
		ForwardHeaders:       s.ForwardHeaders,
		PropagateDepth:       boolOr(s.PropagateDepth, false),
		Enabled:              boolOr(s.Enabled, true),
		OAuthAdapter:         stringOr(adapter, "auto"),
		Labels:               s.Labels,
	}
}

// toolsCacheTTLSeconds is tools_cache_ttl_seconds when set, else the
// tools_cache_ttl duration string when it parses, else 300.
func toolsCacheTTLSeconds(s *MCPServerOrgConfig) int {
	if s.ToolsCacheTTLSeconds != nil {
		return *s.ToolsCacheTTLSeconds
	}
	if d, err := time.ParseDuration(s.ToolsCacheTTL); err == nil && d >= 0 {
		return int(d / time.Second)
	}
	return 300
}

// MCPApprovalSettings is a virtual server's approval policy with defaults.
type MCPApprovalSettings struct {
	Require    string // "none", "destructive" or "listed"
	Tools      []string
	Validity   string // "once", "ttl" or "step"
	TTLSeconds int
}

// MCPVirtualServerSettings is one virtual server with its defaults applied.
type MCPVirtualServerSettings struct {
	Slug         string
	ID           string
	Title        string
	Description  string
	Members      []string
	Include      []string
	Exclude      []string
	Overrides    map[string]*MCPToolOverride
	Naming       string // "prefixed" or "flat"
	Mode         string // "direct" or "search"
	StepProfiles map[string]*MCPStepProfile
	Access       MCPVirtualServerAccess
	Approval     MCPApprovalSettings
	Budgets      MCPVirtualServerBudgets
	CacheScope   string // "private"
}

// DefaultForOrg reports whether this is the org's default virtual server.
func (v MCPVirtualServerSettings) DefaultForOrg() bool {
	return boolOr(v.Access.DefaultForOrg, false)
}

func mcpVirtualServerSettings(slug string, v *MCPVirtualServerConfig) MCPVirtualServerSettings {
	out := MCPVirtualServerSettings{
		Slug:         slug,
		ID:           v.ID,
		Title:        v.Title,
		Description:  v.Description,
		Members:      v.Members,
		Overrides:    v.Overrides,
		Naming:       stringOr(v.Naming, "prefixed"),
		Mode:         stringOr(v.Mode, "direct"),
		StepProfiles: v.StepProfiles,
		CacheScope:   stringOr(v.CacheScope, "private"),
		Approval:     MCPApprovalSettings{Require: "none", Validity: "once", TTLSeconds: 900},
	}
	if v.Tools != nil {
		out.Include, out.Exclude = v.Tools.Include, v.Tools.Exclude
	}
	if v.Access != nil {
		out.Access = *v.Access
	}
	if v.Approval != nil {
		out.Approval = MCPApprovalSettings{
			Require:    stringOr(v.Approval.Require, "none"),
			Tools:      v.Approval.Tools,
			Validity:   stringOr(v.Approval.Validity, "once"),
			TTLSeconds: intOr(v.Approval.TTLSeconds, 900),
		}
	}
	if v.Budgets != nil {
		out.Budgets = *v.Budgets
	}
	return out
}

// sortedVirtualServerSlugs gives a stable order, so a lookup that could
// match more than one virtual server always picks the same one.
func (m *MCPOrgConfig) sortedVirtualServerSlugs() []string {
	slugs := make([]string, 0, len(m.VirtualServers))
	for slug, v := range m.VirtualServers {
		if v != nil {
			slugs = append(slugs, slug)
		}
	}
	sort.Strings(slugs)
	return slugs
}

// MCPVirtualServer returns the virtual server with this id (vs_<ULID>) or,
// failing that, this slug, with its defaults applied; false when the org has
// neither. The id is tried first because it is the immutable address
// (/mcp/v/{vs_id}); a slug can be renamed.
func (c *OrgConfig) MCPVirtualServer(idOrSlug string) (MCPVirtualServerSettings, bool) {
	m := c.mcp()
	if idOrSlug == "" {
		return MCPVirtualServerSettings{}, false
	}
	for _, slug := range m.sortedVirtualServerSlugs() {
		if v := m.VirtualServers[slug]; v.ID == idOrSlug {
			return mcpVirtualServerSettings(slug, v), true
		}
	}
	if v, ok := m.VirtualServers[idOrSlug]; ok && v != nil {
		return mcpVirtualServerSettings(idOrSlug, v), true
	}
	return MCPVirtualServerSettings{}, false
}

// MCPDefaultVirtualServer returns the virtual server /mcp serves for a key:
// the one whose access.keys lists the key, else the org's
// default_for_org one; when several match, the lowest slug wins. It returns
// true only for a configured virtual server.
//
// With none, it returns false and the compatibility view: every enabled org
// server as a member (sorted), defaults applied, no slug or id. Whether a key
// may use that view is mcp.access.default's call (MCPAccess), not this
// accessor's.
func (c *OrgConfig) MCPDefaultVirtualServer(keyID string) (MCPVirtualServerSettings, bool) {
	m := c.mcp()
	slugs := m.sortedVirtualServerSlugs()
	if keyID != "" {
		for _, slug := range slugs {
			v := m.VirtualServers[slug]
			if v.Access != nil && containsString(v.Access.Keys, keyID) {
				return mcpVirtualServerSettings(slug, v), true
			}
		}
	}
	for _, slug := range slugs {
		v := m.VirtualServers[slug]
		if v.Access != nil && boolOr(v.Access.DefaultForOrg, false) {
			return mcpVirtualServerSettings(slug, v), true
		}
	}
	compat := mcpVirtualServerSettings("", &MCPVirtualServerConfig{})
	for id, s := range m.Servers {
		if s != nil && boolOr(s.Enabled, true) {
			compat.Members = append(compat.Members, id)
		}
	}
	sort.Strings(compat.Members)
	return compat, false
}

func containsString(list []string, s string) bool {
	for _, item := range list {
		if item == s {
			return true
		}
	}
	return false
}

// MCPManagedToolsSettings is mcp.managed_tools with its defaults applied.
type MCPManagedToolsSettings struct {
	Enabled           bool
	Allow             []string
	Deny              []string
	MaxCallCostUSD    *float64 // nil: no per-call cap
	FallbackToManaged bool
	AllowUserTokens   bool
	Credentials       map[string]*MCPManagedToolCredential
}

// MCPManagedTools returns the org's managed paid-tool opt-in.
func (c *OrgConfig) MCPManagedTools() MCPManagedToolsSettings {
	var t MCPManagedToolsConfig
	if m := c.mcp(); m.ManagedTools != nil {
		t = *m.ManagedTools
	}
	return MCPManagedToolsSettings{
		Enabled:           boolOr(t.Enabled, false),
		Allow:             t.Allow,
		Deny:              t.Deny,
		MaxCallCostUSD:    t.MaxCallCostUSD,
		FallbackToManaged: boolOr(t.FallbackToManaged, false),
		AllowUserTokens:   boolOr(t.AllowUserTokens, false),
		Credentials:       t.Credentials,
	}
}

// MCPPinningSettings is mcp.pinning with its defaults applied.
type MCPPinningSettings struct {
	Mode         string // "quarantine", "warn" or "off"
	NewTools     string // "quarantine" or "allow"
	AutoBaseline bool
	Pins         map[string]string
}

// MCPPinning returns the org's tool pinning policy.
func (c *OrgConfig) MCPPinning() MCPPinningSettings {
	var p MCPPinningConfig
	if m := c.mcp(); m.Pinning != nil {
		p = *m.Pinning
	}
	return MCPPinningSettings{
		Mode:         stringOr(p.Mode, "quarantine"),
		NewTools:     stringOr(p.NewTools, "quarantine"),
		AutoBaseline: boolOr(p.AutoBaseline, true),
		Pins:         p.Pins,
	}
}

// MCPScanningSettings is mcp.scanning with its defaults applied.
type MCPScanningSettings struct {
	Descriptions string // "warn", "off" or "block"
	Arguments    []string
	Results      []string
	Shadow       bool
}

// MCPScanning returns the org's description, argument and result scanning.
func (c *OrgConfig) MCPScanning() MCPScanningSettings {
	var s MCPScanningConfig
	if m := c.mcp(); m.Scanning != nil {
		s = *m.Scanning
	}
	return MCPScanningSettings{
		Descriptions: stringOr(s.Descriptions, "warn"),
		Arguments:    s.Arguments,
		Results:      s.Results,
		Shadow:       boolOr(s.Shadow, true),
	}
}

// MCPFlowControl returns mcp.flow_control with its defaults applied.
func (c *OrgConfig) MCPFlowControl() MCPFlowControlConfig {
	var f MCPFlowControlConfig
	if m := c.mcp(); m.FlowControl != nil {
		f = *m.FlowControl
	}
	f.Mode = stringOr(f.Mode, "shadow")
	f.OnEgressAfterTaint = stringOr(f.OnEgressAfterTaint, "approve")
	return f
}

// MCPPolicyDefaultTier returns the action per risk tier when no policy rule
// matches, with its defaults applied.
func (c *OrgConfig) MCPPolicyDefaultTier() MCPPolicyDefaultTier {
	var d MCPPolicyDefaultTier
	if m := c.mcp(); m.Policy != nil && m.Policy.DefaultTier != nil {
		d = *m.Policy.DefaultTier
	}
	d.ReadOnly = stringOr(d.ReadOnly, "allow")
	d.Undeclared = stringOr(d.Undeclared, "approve")
	d.Destructive = stringOr(d.Destructive, "approve")
	d.DestructiveProd = stringOr(d.DestructiveProd, "deny")
	return d
}
