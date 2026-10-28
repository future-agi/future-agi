package guardrails

import (
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestReflectionDebugUsesBufferingApplicability(t *testing.T) {
	for _, dynamic := range []bool{false, true} {
		for _, tc := range []struct {
			name, stage, mode, onBlock, requestPolicy string
			debug, disabled                           bool
			override                                  config.KeyGuardrailOverride
			wantBuffer, wantDebug                     bool
		}{
			{name: "debug", stage: "post", onBlock: "reflect", debug: true, wantBuffer: true, wantDebug: true},
			{name: "debug off", stage: "post", onBlock: "reflect", wantBuffer: true},
			{name: "reject", stage: "post", onBlock: "reject", debug: true},
			{name: "pre", stage: "pre", onBlock: "reflect", debug: true},
			{name: "key disabled", stage: "post", onBlock: "reflect", debug: true, override: config.KeyGuardrailOverride{Disabled: true}},
			{name: "key warn", stage: "post", onBlock: "reflect", debug: true, override: config.KeyGuardrailOverride{Action: "warn"}},
			{name: "request disabled", stage: "post", onBlock: "reflect", debug: true, requestPolicy: "disabled"},
			{name: "request log only", stage: "post", onBlock: "reflect", debug: true, requestPolicy: "log-only"},
			{name: "request strict", stage: "post", onBlock: "reflect", debug: true, requestPolicy: "strict", override: config.KeyGuardrailOverride{Action: "warn"}, wantBuffer: true, wantDebug: true},
			{name: "org disabled", stage: "post", onBlock: "reflect", debug: true, disabled: true},
			{name: "async", stage: "post", mode: "async", onBlock: "reflect", debug: true},
		} {
			if (dynamic && tc.mode == "async") || (!dynamic && tc.disabled) {
				continue // Tenant checks are synchronous; static enablement comes from policy.
			}
			t.Run(fmt.Sprintf("tenant=%t/%s", dynamic, tc.name), func(t *testing.T) {
				cfg := map[string]interface{}{"stage": tc.stage, "on_block": tc.onBlock, "reflect_debug": tc.debug}
				registry := map[string]Guardrail{"output": &responseWordCheck{name: "output", stage: StagePre}}
				policies := policy.NewStore()
				tc.override.Name = "output"
				policies.Register("key", &config.KeyGuardrailPolicyConfig{Overrides: []config.KeyGuardrailOverride{tc.override}})
				var engine *Engine
				var store *tenant.Store
				if dynamic {
					store = tenant.NewStore()
					store.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{"output": {Enabled: !tc.disabled, Action: "block", Config: cfg}}}})
				} else {
					engine = NewEngine(config.GuardrailsConfig{Rules: []config.GuardrailRuleConfig{{Name: "output", Stage: tc.stage, Mode: tc.mode, Action: "block", Config: cfg}}}, registry)
				}
				plugin := NewPlugin(engine, registry, nil, policies, store)
				rc := reflectionRC()
				defer rc.Release()
				rc.Metadata["org_id"], rc.Metadata["auth_key_id"] = "org", "key"
				rc.Metadata["x-guardrail-policy"], rc.Metadata["key_allow_policy_override"] = tc.requestPolicy, "true"
				if buffered, debug := plugin.BufferResponse(rc), plugin.ReflectionDebug(rc); buffered != tc.wantBuffer || debug != tc.wantDebug {
					t.Fatalf("buffer=%t debug=%t; want buffer=%t debug=%t", buffered, debug, tc.wantBuffer, tc.wantDebug)
				}
			})
		}
	}
}
