package guardrails

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestOrgReflectionAdmissionUsesFreshRuntimeResolution(t *testing.T) {
	cfg := func(provider string) *tenant.OrgConfig {
		checkConfig := map[string]interface{}{"stage": "post", "on_block": "reflect"}
		if provider != "" {
			checkConfig["provider"] = provider
		}
		return &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{
			"external": {Enabled: true, Action: "block", Config: checkConfig},
		}}}
	}
	factory := func(name string, cfg map[string]interface{}) Guardrail {
		if cfg["provider"] == "external" {
			return &responseWordCheck{name: name, stage: StagePost}
		}
		return nil
	}
	plugin := NewPlugin(nil, nil, factory, nil, nil)
	previous := cfg("external")
	if err := plugin.validateOrgReflectionConfig("org-a", previous); err != nil {
		t.Fatal(err)
	}
	cached := plugin.getCachedDynamicGuardrail("org-a", "external")
	if cached == nil {
		t.Fatal("request-time resolution did not populate the fixture cache")
	}
	if err := plugin.ValidateOrgReflectionConfig(cfg("")); err == nil {
		t.Fatal("old cached implementation allowed an unresolvable replacement")
	}
	if err := plugin.ValidateOrgReflectionConfig(cfg("external")); err != nil {
		t.Fatalf("resolvable replacement rejected: %v", err)
	}
	if plugin.getCachedDynamicGuardrail("org-a", "external") != cached || len(plugin.dynamicCache) != 1 {
		t.Fatal("admission modified the live dynamic cache")
	}
}

func TestOrgReflectionAdmissionAcceptsStaticImplementation(t *testing.T) {
	registry := map[string]Guardrail{"operator-check": &responseWordCheck{name: "operator-check", stage: StagePost}}
	engine := NewEngine(config.GuardrailsConfig{Rules: []config.GuardrailRuleConfig{
		{Name: "operator-check", Stage: "post", Action: "block", Config: map[string]interface{}{"on_block": "reflect"}},
	}}, registry)
	// The static engine owns this implementation; tenant resolution has no factory
	// or registry entry of its own and must not reject the already-handled check.
	plugin := NewPlugin(engine, nil, nil, nil, nil)
	cfg := &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{
		"operator-check": {Enabled: true, Action: "block", Config: map[string]interface{}{"stage": "post", "on_block": "reflect"}},
	}}}
	if err := plugin.ValidateOrgReflectionConfig(cfg); err != nil {
		t.Fatalf("static implementation rejected: %v", err)
	}
}
