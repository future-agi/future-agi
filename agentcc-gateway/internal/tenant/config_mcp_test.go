package tenant

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
)

// mcpDefaults are the org config mcp v2 defaults.
// The admin schema states them in descriptions, not `default` keywords
// (TestAccessorDefaults requires an accessor row for each of those), so this
// table is their single pin on the gateway side.
var mcpDefaults = map[string]any{
	"access.default":                         "deny",
	"servers.era":                            "auto",
	"servers.credential.mode":                "none",
	"servers.credential.state":               "active",
	"servers.trusted_annotations":            false,
	"servers.timeout_ms":                     30000,
	"servers.tools_cache_ttl_seconds":        300,
	"servers.propagate_depth":                false,
	"servers.enabled":                        true,
	"servers.oauth.adapter":                  "auto",
	"virtual_servers.naming":                 "prefixed",
	"virtual_servers.mode":                   "direct",
	"virtual_servers.cache_scope":            "private",
	"virtual_servers.access.default_for_org": false,
	"virtual_servers.approval.require":       "none",
	"virtual_servers.approval.validity":      "once",
	"virtual_servers.approval.ttl_seconds":   900,
	"managed_tools.enabled":                  false,
	"managed_tools.max_call_cost_usd":        (*float64)(nil),
	"managed_tools.fallback_to_managed":      false,
	"managed_tools.allow_user_tokens":        false,
	"pinning.mode":                           "quarantine",
	"pinning.new_tools":                      "quarantine",
	"pinning.auto_baseline":                  true,
	"scanning.descriptions":                  "warn",
	"scanning.shadow":                        true,
	"flow_control.mode":                      "shadow",
	"flow_control.on_egress_after_taint":     "approve",
	"policy.default_tier.read_only":          "allow",
	"policy.default_tier.undeclared":         "approve",
	"policy.default_tier.destructive":        "approve",
	"policy.default_tier.destructive_prod":   "deny",
}

// mcpAccessorValues reads every defaulted MCP setting of cfg through the
// accessors, for the server "s" and the virtual server "vs" (both may be
// absent, which must still give the defaults).
func mcpAccessorValues(cfg *OrgConfig) map[string]any {
	srv, _ := cfg.MCPServer("s")
	vs, ok := cfg.MCPVirtualServer("vs")
	if !ok {
		vs = mcpVirtualServerSettings("vs", &MCPVirtualServerConfig{})
	}
	mt := cfg.MCPManagedTools()
	pin := cfg.MCPPinning()
	scan := cfg.MCPScanning()
	flow := cfg.MCPFlowControl()
	tier := cfg.MCPPolicyDefaultTier()
	return map[string]any{
		"access.default":                         cfg.MCPAccess().Default,
		"servers.era":                            srv.Era,
		"servers.credential.mode":                srv.CredentialMode,
		"servers.credential.state":               srv.CredentialState,
		"servers.trusted_annotations":            srv.TrustedAnnotations,
		"servers.timeout_ms":                     srv.TimeoutMs,
		"servers.tools_cache_ttl_seconds":        srv.ToolsCacheTTLSeconds,
		"servers.propagate_depth":                srv.PropagateDepth,
		"servers.enabled":                        srv.Enabled,
		"servers.oauth.adapter":                  srv.OAuthAdapter,
		"virtual_servers.naming":                 vs.Naming,
		"virtual_servers.mode":                   vs.Mode,
		"virtual_servers.cache_scope":            vs.CacheScope,
		"virtual_servers.access.default_for_org": vs.DefaultForOrg(),
		"virtual_servers.approval.require":       vs.Approval.Require,
		"virtual_servers.approval.validity":      vs.Approval.Validity,
		"virtual_servers.approval.ttl_seconds":   vs.Approval.TTLSeconds,
		"managed_tools.enabled":                  mt.Enabled,
		"managed_tools.max_call_cost_usd":        mt.MaxCallCostUSD,
		"managed_tools.fallback_to_managed":      mt.FallbackToManaged,
		"managed_tools.allow_user_tokens":        mt.AllowUserTokens,
		"pinning.mode":                           pin.Mode,
		"pinning.new_tools":                      pin.NewTools,
		"pinning.auto_baseline":                  pin.AutoBaseline,
		"scanning.descriptions":                  scan.Descriptions,
		"scanning.shadow":                        scan.Shadow,
		"flow_control.mode":                      flow.Mode,
		"flow_control.on_egress_after_taint":     flow.OnEgressAfterTaint,
		"policy.default_tier.read_only":          tier.ReadOnly,
		"policy.default_tier.undeclared":         tier.Undeclared,
		"policy.default_tier.destructive":        tier.Destructive,
		"policy.default_tier.destructive_prod":   tier.DestructiveProd,
	}
}

func checkDefaults(t *testing.T, want, got map[string]any) {
	t.Helper()
	keys := make([]string, 0, len(want))
	for k := range want {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		g, ok := got[k]
		if !ok {
			t.Errorf("default %s has no accessor", k)
			continue
		}
		if !reflect.DeepEqual(g, want[k]) {
			t.Errorf("%s = %#v, want default %#v", k, g, want[k])
		}
	}
	for k := range got {
		if _, ok := want[k]; !ok {
			t.Errorf("accessor %s has no pinned default", k)
		}
	}
}

func TestMCPAccessorDefaults(t *testing.T) {
	// Every pointer and enum left unset, at every level the accessors read.
	emptyEverywhere := mustParse(t, `{"mcp": {
		"access": {},
		"servers": {"s": {"url": "https://s.example.com/mcp", "credential": {}, "oauth": {}}},
		"virtual_servers": {"vs": {"access": {}, "approval": {}}},
		"managed_tools": {}, "pinning": {}, "scanning": {}, "flow_control": {},
		"policy": {"default_tier": {}}
	}}`)
	for name, cfg := range map[string]*OrgConfig{
		"nil config":       nil,
		"empty config":     {},
		"empty mcp":        {MCP: &MCPOrgConfig{}},
		"empty everywhere": emptyEverywhere,
	} {
		t.Run(name, func(t *testing.T) {
			checkDefaults(t, mcpDefaults, mcpAccessorValues(cfg))
		})
	}

	t.Run("unknown ids", func(t *testing.T) {
		if _, ok := emptyEverywhere.MCPServer("missing"); ok {
			t.Error("MCPServer reported an unconfigured server")
		}
		if _, ok := emptyEverywhere.MCPVirtualServer("missing"); ok {
			t.Error("MCPVirtualServer reported an unconfigured virtual server")
		}
		if _, ok := emptyEverywhere.MCPVirtualServer(""); ok {
			t.Error("MCPVirtualServer matched an empty id")
		}
	})
}

func TestParseMCPv2(t *testing.T) {
	cfg := mustParse(t, `{"mcp": {
		"enabled": true,
		"access": {"default": "allow"},
		"servers": {
			"github": {
				"url": "https://mcp.github.example.com/mcp",
				"era": "2025-06-18",
				"credential": {"mode": "per_user", "state": "expired"},
				"timeout_ms": 5000,
				"tools_cache_ttl_seconds": 0,
				"forward_headers": ["x-request-source"],
				"propagate_depth": true,
				"enabled": false,
				"oauth": {"adapter": "github"},
				"labels": ["private_data"]
			}
		},
		"virtual_servers": {
			"eng": {
				"id": "vs_01M4ZEB9E0J3AADR85Z4T4428X",
				"members": ["github@work", "fagi.web"],
				"naming": "flat",
				"mode": "search",
				"approval": {"require": "listed", "tools": ["github/merge_*"], "validity": "step"},
				"budgets": {"max_tool_calls_per_run": 0, "per_tool": {"fagi.web/*": {"rpm": 10}}}
			}
		},
		"managed_tools": {"enabled": true, "allow": ["search/exa", "fagi.web/*"], "max_call_cost_usd": 0.1},
		"pinning": {"mode": "off", "new_tools": "allow", "auto_baseline": false},
		"scanning": {"descriptions": "block", "shadow": false},
		"flow_control": {"mode": "enforce", "on_egress_after_taint": "deny"},
		"policy": {"default_tier": {"destructive_prod": "approve"}},
		"shaping": {"per_tool": {"*": {"budget_bytes": 4096, "project": ["/items"]}}}
	}}`)

	srv, ok := cfg.MCPServer("github")
	if !ok {
		t.Fatal("github server not found")
	}
	if srv.Era != "2025-06-18" || srv.CredentialMode != "per_user" || srv.CredentialState != "expired" ||
		srv.TimeoutMs != 5000 || srv.ToolsCacheTTLSeconds != 0 || !srv.PropagateDepth || srv.Enabled ||
		srv.OAuthAdapter != "github" || !reflect.DeepEqual(srv.Labels, []string{"private_data"}) ||
		!reflect.DeepEqual(srv.ForwardHeaders, []string{"x-request-source"}) {
		t.Errorf("server settings = %+v", srv)
	}
	if got := cfg.MCPAccess().Default; got != "allow" {
		t.Errorf("access.default = %q", got)
	}

	byID, ok := cfg.MCPVirtualServer("vs_01M4ZEB9E0J3AADR85Z4T4428X")
	if !ok || byID.Slug != "eng" {
		t.Fatalf("lookup by id = %+v, %v", byID, ok)
	}
	bySlug, ok := cfg.MCPVirtualServer("eng")
	if !ok || bySlug.ID != "vs_01M4ZEB9E0J3AADR85Z4T4428X" {
		t.Fatalf("lookup by slug = %+v, %v", bySlug, ok)
	}
	if bySlug.Naming != "flat" || bySlug.Mode != "search" || bySlug.Approval.Require != "listed" ||
		bySlug.Approval.Validity != "step" || bySlug.Approval.TTLSeconds != 900 ||
		*bySlug.Budgets.MaxToolCallsPerRun != 0 || *bySlug.Budgets.PerTool["fagi.web/*"].RPM != 10 {
		t.Errorf("virtual server settings = %+v", bySlug)
	}

	mt := cfg.MCPManagedTools()
	if !mt.Enabled || *mt.MaxCallCostUSD != 0.1 || !reflect.DeepEqual(mt.Allow, []string{"search/exa", "fagi.web/*"}) {
		t.Errorf("managed tools = %+v", mt)
	}
	if p := cfg.MCPPinning(); p.Mode != "off" || p.NewTools != "allow" || p.AutoBaseline {
		t.Errorf("pinning = %+v", p)
	}
	if s := cfg.MCPScanning(); s.Descriptions != "block" || s.Shadow {
		t.Errorf("scanning = %+v", s)
	}
	if f := cfg.MCPFlowControl(); f.Mode != "enforce" || f.OnEgressAfterTaint != "deny" {
		t.Errorf("flow control = %+v", f)
	}
	if d := cfg.MCPPolicyDefaultTier(); d.DestructiveProd != "approve" || d.ReadOnly != "allow" {
		t.Errorf("default tier = %+v", d)
	}
	if s := cfg.MCP.Shaping.PerTool["*"]; *s.BudgetBytes != 4096 || s.Project[0] != "/items" {
		t.Errorf("shaping = %+v", s)
	}
}

func TestMCPDefaultVirtualServer(t *testing.T) {
	cfg := mustParse(t, `{"mcp": {
		"servers": {"b": {"url": "https://b"}, "a": {"url": "https://a"}, "off": {"url": "https://o", "enabled": false}},
		"virtual_servers": {
			"zeta": {"id": "vs_01M4ZEB9E1A6Y8ATM7S5TJZ9YY", "access": {"keys": ["key_1"], "default_for_org": true}},
			"alpha": {"access": {"keys": ["key_1"]}},
			"org": {"access": {"default_for_org": true}}
		}
	}}`)

	bound, ok := cfg.MCPDefaultVirtualServer("key_1")
	if !ok || bound.Slug != "alpha" {
		t.Errorf("key-bound default = %q, %v; want alpha (the lowest bound slug)", bound.Slug, ok)
	}
	org, ok := cfg.MCPDefaultVirtualServer("key_other")
	if !ok || org.Slug != "org" {
		t.Errorf("org default = %q, %v; want org (lowest default_for_org slug)", org.Slug, ok)
	}

	bare := mustParse(t, `{"mcp": {"servers": {"b": {"url": "https://b"}, "a": {"url": "https://a"}, "off": {"url": "https://o", "enabled": false}}}}`)
	compat, ok := bare.MCPDefaultVirtualServer("key_1")
	if ok {
		t.Error("a config with no virtual servers reported a configured default")
	}
	if compat.Slug != "" || compat.ID != "" || !reflect.DeepEqual(compat.Members, []string{"a", "b"}) || compat.Naming != "prefixed" {
		t.Errorf("compatibility view = %+v; want every enabled server, sorted", compat)
	}
}

func TestMCPUnknownKeyRejected(t *testing.T) {
	for name, body := range map[string]string{
		"top level":           `{"mcp": {"enabled": true, "egress": {"private_allow": ["10.0.0.0/8"]}}}`,
		"server":              `{"mcp": {"servers": {"s": {"url": "https://s", "private_network": true}}}}`,
		"server credential":   `{"mcp": {"servers": {"s": {"credential": {"mode": "shared", "token": "x"}}}}}`,
		"credential secret":   `{"mcp": {"servers": {"s": {"credential": {"secret": {"type": "bearer", "value": "v", "extra": 1}}}}}}`,
		"virtual server":      `{"mcp": {"virtual_servers": {"v": {"members": ["s"], "public": true}}}}`,
		"access rule":         `{"mcp": {"virtual_servers": {"v": {"access": {"rules": [{"match": {"keys": ["k"]}}]}}}}}`,
		"managed tools":       `{"mcp": {"managed_tools": {"enabled": true, "markup_percent": 0}}}`,
		"managed credential":  `{"mcp": {"managed_tools": {"credentials": {"fagi.web": {"mode": "shared", "api_key": "x"}}}}}`,
		"pinning":             `{"mcp": {"pinning": {"mode": "quarantine", "strict": true}}}`,
		"policy rule":         `{"mcp": {"policy": {"rules": [{"id": "r", "match": {"tools": ["*"]}, "action": "deny", "reason": "x", "priority": 1}]}}}`,
		"shaping":             `{"mcp": {"shaping": {"per_tool": {"*": {"max_result_bytes": 10}}}}}`,
		"flow control":        `{"mcp": {"flow_control": {"mode": "shadow", "labels": []}}}`,
		"tools capability":    `{"tools": {"capabilities": {"search": {"enabled": true, "managed": true}}}}`,
		"identity idp":        `{"identity": {"idp": {"issuer": "https://i", "audience": "x"}}}`,
		"budgets":             `{"budgets": {"enabled": true, "fail_closed": true}}`,
		"envelope defaults":   `{"routing": {"agents": {"envelopes": {"defaults": {"max_tokens": 1}}}}}`,
		"legacy auth":         `{"mcp": {"servers": {"s": {"auth": {"type": "bearer", "password": "x"}}}}}`,
		"guardrails (legacy)": `{"mcp": {"guardrails": {"enabled": true, "blocked": ["x"]}}}`,
	} {
		t.Run(name, func(t *testing.T) {
			_, err := parseViaContract([]byte(body))
			if err == nil || !strings.Contains(err.Error(), "unknown field") {
				t.Fatalf("parse error = %v, want an unknown-field rejection", err)
			}
		})
	}
}

func TestLegacyMCPAuthParses(t *testing.T) {
	cfg := mustParse(t, `{"mcp": {
		"enabled": true,
		"servers": {"github": {
			"url": "https://mcp.example.com",
			"transport": "http",
			"auth": {"type": "bearer", "token": "mcp-token"},
			"tools_cache_ttl": "5m"
		}},
		"guardrails": {"enabled": true, "blocked_tools": ["shell"], "validate_inputs": true, "validate_outputs": false}
	}}`)

	srv, ok := cfg.MCPServer("github")
	if !ok || srv.Auth == nil || srv.Auth.Type != "bearer" || srv.Auth.Token != "mcp-token" {
		t.Fatalf("legacy auth not kept: %+v", srv)
	}
	if cfg.MCP.Servers["github"].ToolsCacheTTL != "5m" || srv.ToolsCacheTTLSeconds != 300 {
		t.Errorf("legacy tools_cache_ttl = %q, read as %ds", cfg.MCP.Servers["github"].ToolsCacheTTL, srv.ToolsCacheTTLSeconds)
	}
	// The legacy duration string keeps its value through the accessor;
	// tools_cache_ttl_seconds wins when both are set, and an unparsable
	// string falls back to the default.
	ttl := mustParse(t, `{"mcp": {"servers": {
		"legacy": {"tools_cache_ttl": "10m"},
		"both": {"tools_cache_ttl": "10m", "tools_cache_ttl_seconds": 30},
		"bad": {"tools_cache_ttl": "soon"}
	}}}`)
	for id, want := range map[string]int{"legacy": 600, "both": 30, "bad": 300} {
		if s, _ := ttl.MCPServer(id); s.ToolsCacheTTLSeconds != want {
			t.Errorf("%s: ToolsCacheTTLSeconds = %d, want %d", id, s.ToolsCacheTTLSeconds, want)
		}
	}
	// A legacy server takes every v2 default.
	if srv.Era != "auto" || srv.CredentialMode != "none" || !srv.Enabled || srv.TimeoutMs != 30000 {
		t.Errorf("legacy server defaults = %+v", srv)
	}
	if !cfg.MCP.Guardrails.Enabled || cfg.MCP.Guardrails.BlockedTools[0] != "shell" {
		t.Errorf("legacy guardrails = %+v", cfg.MCP.Guardrails)
	}

	// The v1 fixture's mcp section (legacy auth, tools_cache_ttl and
	// guardrails) decodes through the strict contract.
	body, err := os.ReadFile(v1FixturePath)
	if err != nil {
		t.Fatalf("read v1 fixture: %v", err)
	}
	var fixture map[string]json.RawMessage
	if err := json.Unmarshal(body, &fixture); err != nil {
		t.Fatal(err)
	}
	legacy := mustParse(t, `{"mcp": `+string(fixture["mcp"])+`}`)
	if s, ok := legacy.MCPServer("github"); !ok || s.Auth == nil || s.Auth.Token == "" {
		t.Errorf("v1 fixture server = %+v", s)
	}
}

func TestGoldenOrgConfigMCPv2(t *testing.T) {
	body, err := os.ReadFile(filepath.Join(goldenDir, "org-config-mcp-v2.json"))
	if err != nil {
		t.Fatalf("read golden: %v", err)
	}
	cfg, err := parseViaContract(body)
	if err != nil {
		t.Fatalf("golden rejected by the contract: %v", err)
	}

	t.Run("round trip keeps every field", func(t *testing.T) {
		var want any
		if err := json.Unmarshal(body, &want); err != nil {
			t.Fatalf("parse golden: %v", err)
		}
		if diffs := jsonDiff("", want, asJSONValue(t, cfg)); len(diffs) > 0 {
			sort.Strings(diffs)
			t.Fatalf("tenant.OrgConfig does not mirror the golden:\n%v", diffs)
		}
	})

	t.Run("accessors read the golden values", func(t *testing.T) {
		github, _ := cfg.MCPServer("github")
		issues, _ := cfg.MCPServer("issues")
		crm, _ := cfg.MCPServer("crm")
		warehouse, _ := cfg.MCPServer("warehouse")
		legacy, _ := cfg.MCPServer("legacy_docs")
		eng, engOK := cfg.MCPVirtualServer("vs_01M4ZEB9E0J3AADR85Z4T4428X")
		research, _ := cfg.MCPVirtualServer("research")
		def, defOK := cfg.MCPDefaultVirtualServer("key_9c2d")
		orgDef, _ := cfg.MCPDefaultVirtualServer("key_unbound")
		mt := cfg.MCPManagedTools()
		pin := cfg.MCPPinning()
		scan := cfg.MCPScanning()
		checks := map[string][2]any{
			"Access.Default":          {cfg.MCPAccess().Default, "deny"},
			"GitHub.CredentialMode":   {github.CredentialMode, "shared"},
			"GitHub.SecretValue":      {github.Credential.Secret.Value, "test-mcp-secret-not-a-real-token"},
			"GitHub.Timeout":          {github.TimeoutMs, 20000},
			"GitHub.TTL":              {github.ToolsCacheTTLSeconds, 120},
			"GitHub.PropagateDepth":   {github.PropagateDepth, true},
			"GitHub.OAuth":            {github.OAuthAdapter, "github"},
			"Issues.State":            {issues.CredentialState, "needs_reauth"},
			"Issues.Era":              {issues.Era, "2026-07-28"},
			"CRM.Vendor":              {crm.Credential.Vendor, "pipedream"},
			"CRM.Binding":             {crm.Credential.IdentityBinding.Name, "x-end-user-id"},
			"Warehouse.Mode":          {warehouse.CredentialMode, "id_jag"},
			"Warehouse.Enabled":       {warehouse.Enabled, false},
			"Legacy.Token":            {legacy.Auth.Token, "test-legacy-token-not-real"},
			"Legacy.Transport":        {legacy.Transport, "sse"},
			"Eng.Found":               {engOK, true},
			"Eng.Slug":                {eng.Slug, "engineering"},
			"Eng.Members":             {eng.Members, []string{"github", "issues@work", "fagi.web"}},
			"Eng.ApprovalTTL":         {eng.Approval.TTLSeconds, 600},
			"Eng.DefaultForOrg":       {eng.DefaultForOrg(), true},
			"Eng.Override":            {eng.Overrides["github/create_issue"].Name, "open_issue"},
			"Eng.StepProfile":         {eng.StepProfiles["plan"].Include, []string{"issues/*"}},
			"Research.Mode":           {research.Mode, "search"},
			"Research.Validity":       {research.Approval.Validity, "step"},
			"Research.ApprovalTTL":    {research.Approval.TTLSeconds, 900},
			"Default.BoundKey":        {def.Slug, "research"},
			"Default.Configured":      {defOK, true},
			"Default.OrgDefault":      {orgDef.Slug, "engineering"},
			"Managed.Enabled":         {mt.Enabled, true},
			"Managed.Allow":           {mt.Allow, []string{"fagi.web/*", "search/exa"}},
			"Managed.Cap":             {*mt.MaxCallCostUSD, 0.25},
			"Managed.CredentialState": {mt.Credentials["fagi.web"].State, "active"},
			"Pinning.Pin":             {pin.Pins["github/create_issue"], "cc1441cea3b1f974d2f8bb95234d3c36b99536a4b6dbc21dc7dee08b463908d0"},
			"Scanning.Descriptions":   {scan.Descriptions, "block"},
			"Scanning.Shadow":         {scan.Shadow, false},
			"Flow.Mode":               {cfg.MCPFlowControl().Mode, "enforce"},
			"Policy.DestructiveProd":  {cfg.MCPPolicyDefaultTier().DestructiveProd, "deny"},
			"Policy.RuleAction":       {cfg.MCP.Policy.Rules[0].Action, "deny"},
			"Policy.ComboSequence":    {cfg.MCP.Policy.Combos[0].Sequence, []string{"issues/read_*", "github/create_*"}},
			"Shaping.Budget":          {*cfg.MCP.Shaping.PerTool["github/search_code"].BudgetBytes, 32768},
		}
		for name, c := range checks {
			if !reflect.DeepEqual(c[0], c[1]) {
				t.Errorf("%s = %#v, want %#v", name, c[0], c[1])
			}
		}
	})
}
