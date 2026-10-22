package tenant

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
)

// toolsAndPolicyDefaults are the tools defaults and the non-MCP policy
// defaults (budgets, envelopes, identity). Like mcpDefaults, they are stated in the admin
// schema's descriptions and pinned here.
var toolsAndPolicyDefaults = map[string]any{
	"tools.capabilities.enabled":               false,
	"tools.capabilities.max_cost_usd_per_call": (*float64)(nil),
	"tools.policy.no_training_vendors_only":    false,
	"tools.policy.zdr_required":                false,
	"tools.policy.retention_override":          "",
	"budgets.fail_mode":                        "closed",
	"budgets.no_max_tokens":                    "reserve_default",
	"routing.agents.envelopes.on_breach":       "stop",
	"routing.agents.envelopes.soft_threshold":  0.8,
	"routing.agents.envelopes.allow_downgrade": false,
	"routing.agents.envelopes.loop.action":     "stop",
	"identity.require_human_sponsor":           false,
	"identity.grants.max_ttl_days":             30,
}

func toolsAndPolicyAccessorValues(cfg *OrgConfig) map[string]any {
	capability := cfg.ToolsCapability("search")
	policy := cfg.ToolsPolicy()
	env := cfg.AgentEnvelopes()
	id := cfg.IdentityPolicy()
	return map[string]any{
		"tools.capabilities.enabled":               capability.Enabled,
		"tools.capabilities.max_cost_usd_per_call": capability.MaxCostUSDPerCall,
		"tools.policy.no_training_vendors_only":    policy.NoTrainingVendorsOnly,
		"tools.policy.zdr_required":                policy.ZDRRequired,
		"tools.policy.retention_override":          policy.RetentionOverride,
		"budgets.fail_mode":                        cfg.BudgetsFailMode(),
		"budgets.no_max_tokens":                    cfg.BudgetsNoMaxTokens(),
		"routing.agents.envelopes.on_breach":       env.OnBreach,
		"routing.agents.envelopes.soft_threshold":  env.SoftThreshold,
		"routing.agents.envelopes.allow_downgrade": env.AllowDowngrade,
		"routing.agents.envelopes.loop.action":     env.LoopAction,
		"identity.require_human_sponsor":           id.RequireHumanSponsor,
		"identity.grants.max_ttl_days":             id.GrantsMaxTTLDays,
	}
}

func TestToolsAccessorDefaults(t *testing.T) {
	emptyEverywhere := mustParse(t, `{
		"tools": {"capabilities": {"search": {}}, "policy": {"domains": {}}},
		"budgets": {"enabled": true},
		"routing": {"agents": {"envelopes": {"defaults": {}, "loop": {}}}},
		"identity": {"grants": {}, "idp": {}}
	}`)
	for name, cfg := range map[string]*OrgConfig{
		"nil config":       nil,
		"empty config":     {},
		"empty sections":   {Tools: &ToolsConfig{}, Identity: &IdentityConfig{}, Budgets: &BudgetsConfig{}, Routing: &RoutingConfig{Agents: &AgentsConfig{}}},
		"empty everywhere": emptyEverywhere,
	} {
		t.Run(name, func(t *testing.T) {
			checkDefaults(t, toolsAndPolicyDefaults, toolsAndPolicyAccessorValues(cfg))
		})
	}

	t.Run("an unconfigured capability is disabled", func(t *testing.T) {
		cfg := mustParse(t, `{"tools": {"capabilities": {"search": {"enabled": true}}}}`)
		if !cfg.ToolsCapability("search").Enabled {
			t.Error("configured capability reads disabled")
		}
		if cfg.ToolsCapability("fetch").Enabled {
			t.Error("unconfigured capability reads enabled")
		}
	})
}

func TestGoldenOrgConfigToolsV1(t *testing.T) {
	body, err := os.ReadFile(filepath.Join(goldenDir, "org-config-tools-v1.json"))
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
		search := cfg.ToolsCapability("search")
		fetch := cfg.ToolsCapability("fetch")
		docs := cfg.ToolsCapability("documents.parse")
		policy := cfg.ToolsPolicy()
		checks := map[string][2]any{
			"Search.Enabled":        {search.Enabled, true},
			"Search.Providers":      {search.Providers, []string{"exa", "brave", "tavily"}},
			"Search.Cap":            {*search.MaxCostUSDPerCall, 0.02},
			"Search.Default":        {search.Defaults["num_results"], 10.0},
			"Search.Pinned":         {search.Pinned["safe_search"], true},
			"Fetch.Enabled":         {fetch.Enabled, true},
			"Fetch.Cap":             {fetch.MaxCostUSDPerCall, (*float64)(nil)},
			"Docs.Enabled":          {docs.Enabled, false},
			"Policy.Allow":          {policy.DomainsAllow, []string{"*.example.com", "docs.python.org"}},
			"Policy.Deny":           {policy.DomainsDeny, []string{"*.internal.example.com"}},
			"Policy.NoTraining":     {policy.NoTrainingVendorsOnly, true},
			"Policy.ZDR":            {policy.ZDRRequired, false},
			"Policy.Regions":        {policy.Regions, []string{"us", "eu"}},
			"Policy.Retention":      {policy.RetentionOverride, "metadata"},
			"ManagedTools.Enabled":  {cfg.MCPManagedTools().Enabled, true},
			"ManagedTools.CapGlobs": {cfg.MCPManagedTools().Allow, []string{"search/exa", "search/brave", "fagi.web/*"}},
		}
		for name, c := range checks {
			if !reflect.DeepEqual(c[0], c[1]) {
				t.Errorf("%s = %#v, want %#v", name, c[0], c[1])
			}
		}
	})
}

// The non-MCP policy keys of the org-config-v2 golden reach their
// accessors (TestGoldenOrgConfigV2 checks the round trip).
func TestGoldenOrgConfigV2PolicyKeys(t *testing.T) {
	body, err := os.ReadFile(filepath.Join(goldenDir, "org-config-v2.json"))
	if err != nil {
		t.Fatalf("read golden: %v", err)
	}
	cfg, err := parseViaContract(body)
	if err != nil {
		t.Fatalf("golden rejected by the contract: %v", err)
	}
	env := cfg.AgentEnvelopes()
	id := cfg.IdentityPolicy()
	checks := map[string][2]any{
		"Budgets.FailMode":         {cfg.BudgetsFailMode(), "open"},
		"Budgets.NoMaxTokens":      {cfg.BudgetsNoMaxTokens(), "clamp"},
		"Envelopes.OnBreach":       {env.OnBreach, "downgrade"},
		"Envelopes.SoftThreshold":  {env.SoftThreshold, 0.75},
		"Envelopes.AllowDowngrade": {env.AllowDowngrade, true},
		"Envelopes.DowngradeModel": {env.DowngradeModel, "openai/gpt-6-mini"},
		"Envelopes.Loop":           {env.LoopAction, "warn"},
		"Envelopes.MaxUSD":         {*env.Defaults.MaxUSD, 5.0},
		"Envelopes.MaxChildren":    {*env.Defaults.MaxConcurrentChildren, 10},
		"Identity.RequireSponsor":  {id.RequireHumanSponsor, true},
		"Identity.MaxTTLDays":      {id.GrantsMaxTTLDays, 14},
		"Identity.IdPClientID":     {id.IdP.ClientID, "agentcc-gateway"},
		"Identity.NoClientSecret":  {id.IdP.ClientSecret, ""},
	}
	for name, c := range checks {
		if !reflect.DeepEqual(c[0], c[1]) {
			t.Errorf("%s = %#v, want %#v", name, c[0], c[1])
		}
	}
}

// Push-only identity parts (the IdP client secret and the standing grants)
// decode through the strict contract when Django pushes them.
func TestIdentityPushOnlyPartsParse(t *testing.T) {
	cfg := mustParse(t, `{"identity": {
		"idp": {"issuer": "https://i", "token_endpoint": "https://i/token", "client_id": "c", "client_secret": "test-idp-secret-not-real"},
		"delegated_grants": {"grant_1": {
			"grant_id": "grant_1", "agent_id": "researcher", "grantor_user_id": "user_42",
			"scopes": {"servers": ["github"], "tools": ["github/get_*"], "models": ["anthropic/*"]},
			"max_usd": 2.5, "preapproved_rules": ["no-prod-deletes"], "expires_at": "2026-11-01T00:00:00Z"
		}}
	}}`)
	id := cfg.IdentityPolicy()
	if id.IdP.ClientSecret != "test-idp-secret-not-real" {
		t.Errorf("client secret = %q", id.IdP.ClientSecret)
	}
	g := id.DelegatedGrants["grant_1"]
	if g == nil || g.GrantorUserID != "user_42" || *g.MaxUSD != 2.5 || g.Scopes.Tools[0] != "github/get_*" {
		t.Errorf("grant = %+v", g)
	}
}
