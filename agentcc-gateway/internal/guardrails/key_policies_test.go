package guardrails

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type keyTestCheck struct {
	name   string
	mutate bool
	delay  bool
	calls  int
}

func (g *keyTestCheck) Name() string       { return g.name }
func (g *keyTestCheck) Stage() Stage       { return StagePre }
func (g *keyTestCheck) MutatesInput() bool { return g.mutate }
func (g *keyTestCheck) Check(ctx context.Context, input *CheckInput) *CheckResult {
	g.calls++
	if g.delay {
		<-ctx.Done()
		return nil
	}
	text := string(input.Request.Messages[0].Content)
	if !strings.Contains(text, "secret") {
		return &CheckResult{Pass: true}
	}
	if g.mutate {
		input.Request.Messages[0].Content = json.RawMessage(`"rewritten"`)
	}
	return &CheckResult{Score: 1, Message: "matched"}
}
func keyTestContext(key string) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.Metadata["org_id"] = "org"
	rc.Metadata["auth_key_id"] = key
	rc.Request = &models.ChatCompletionRequest{Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"secret"`)}}}
	return rc
}
func keyTestPolicy(id, mode, action string, mutate bool) *tenant.KeyGuardrailPolicy {
	name := "secret"
	if mutate {
		name = "pii"
	}
	return &tenant.KeyGuardrailPolicy{ID: id, Name: id, Mode: mode, KeyIDs: []string{"key-a"}, Checks: map[string]*tenant.GuardrailCheck{name: {Enabled: true, Action: action}}}
}
func keyTestPlugin(cfg *tenant.GuardrailConfig, registry map[string]Guardrail) *GuardrailPlugin {
	store := tenant.NewStore()
	store.Set("org", &tenant.OrgConfig{Guardrails: cfg})
	return NewPlugin(nil, registry, nil, nil, store)
}
func TestKeyPolicyEnforceOnlySelectedKeyAndEmptyOrg(t *testing.T) {
	g := &keyTestCheck{name: "secret"}
	p := keyTestPlugin(&tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{keyTestPolicy("one", "enforce", "block", false)}}, map[string]Guardrail{"secret": g})
	for _, tc := range []struct {
		key     string
		blocked bool
	}{{"key-a", true}, {"key-b", false}, {"", false}} {
		rc := keyTestContext(tc.key)
		result := p.ProcessRequest(context.Background(), rc)
		if (result.Action == pipeline.ShortCircuit) != tc.blocked {
			t.Errorf("key %q result %+v", tc.key, result)
		}
		if tc.blocked && (len(rc.GuardrailResults) != 1 || rc.GuardrailResults[0].PolicyID != "one" || rc.GuardrailResults[0].PolicyScope != "key" || rc.GuardrailResults[0].Outcome != "blocked") {
			t.Errorf("key attribution: %+v", rc.GuardrailResults)
		}
		rc.Release()
	}
	if g.calls != 1 {
		t.Errorf("calls=%d want 1", g.calls)
	}
}
func TestKeyPolicyMonitorDoesNotChangeRequestOrResponseMetadata(t *testing.T) {
	for _, action := range []string{"block", "warn", "log"} {
		t.Run(action, func(t *testing.T) {
			g := &keyTestCheck{name: "pii", mutate: true}
			p := keyTestPlugin(&tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{keyTestPolicy("monitor", "monitor", action, true)}}, map[string]Guardrail{"pii": g})
			rc := keyTestContext("key-a")
			defer rc.Release()
			rc.Metadata["x-guardrail-policy"] = "disabled"
			res := p.ProcessRequest(context.Background(), rc)
			if res.Action != pipeline.Continue || string(rc.Request.Messages[0].Content) != `"secret"` || rc.Flags.GuardrailTriggered || !rc.Flags.GuardrailMonitored || rc.Metadata["guardrail_name"] != "" || rc.Metadata["guardrail_action"] != "" {
				t.Fatalf("monitor mutated response/request: %+v %+v", res, rc)
			}
			if len(rc.GuardrailResults) != 1 || rc.GuardrailResults[0].Action != "log" || rc.GuardrailResults[0].WouldAction != action || rc.GuardrailResults[0].Outcome != map[string]string{"block": "would_block", "warn": "would_redact", "log": "would_redact"}[action] || rc.GuardrailResults[0].Mode != "monitor" {
				t.Fatalf("monitor attribution %+v", rc.GuardrailResults)
			}
		})
	}
}
func TestKeyPolicyMonitorTimeoutFailClosed(t *testing.T) {
	g := &keyTestCheck{name: "secret", delay: true}
	p := keyTestPlugin(&tenant.GuardrailConfig{TimeoutMs: 10, FailOpen: false, KeyPolicies: []*tenant.KeyGuardrailPolicy{keyTestPolicy("monitor", "monitor", "block", false)}}, map[string]Guardrail{"secret": g})
	rc := keyTestContext("key-a")
	defer rc.Release()
	if res := p.ProcessRequest(context.Background(), rc); res.Action != pipeline.Continue {
		t.Fatalf("timeout blocked: %+v", res)
	}
	if rc.Flags.GuardrailTriggered {
		t.Fatal("monitor timeout set response flag")
	}
}
func TestOrgBlockPrecedesKeyMonitorAndKeyRemediation(t *testing.T) {
	for _, mode := range []string{"monitor", "enforce"} {
		t.Run(mode, func(t *testing.T) {
			g := &keyTestCheck{name: "pii", mutate: true}
			cfg := &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{"pii": {Enabled: true, Action: "block"}}, KeyPolicies: []*tenant.KeyGuardrailPolicy{keyTestPolicy("key", mode, "log", true)}}
			p := keyTestPlugin(cfg, map[string]Guardrail{"pii": g})
			rc := keyTestContext("key-a")
			defer rc.Release()
			if result := p.ProcessRequest(context.Background(), rc); result.Action != pipeline.ShortCircuit {
				t.Fatalf("key policy weakened the org block: %+v", result)
			}
			if g.calls != 1 || len(rc.GuardrailResults) != 1 || rc.GuardrailResults[0].PolicyScope != "org" || rc.GuardrailResults[0].PolicyName != "Organization guardrails" || rc.GuardrailResults[0].Mode != "enforce" {
				t.Fatalf("org attribution %+v calls=%d", rc.GuardrailResults, g.calls)
			}
		})
	}
}
func TestKeyPolicyDynamicCacheIsolationAndStaticEngine(t *testing.T) {
	created := 0
	factory := func(name string, cfg map[string]interface{}) Guardrail { created++; return &keyTestCheck{name: name} }
	cfg := &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{"secret": {Enabled: true, Action: "log", Config: map[string]interface{}{"id": "org"}}}, KeyPolicies: []*tenant.KeyGuardrailPolicy{keyTestPolicy("a", "enforce", "log", false), keyTestPolicy("b", "enforce", "log", false)}}
	for _, pol := range cfg.KeyPolicies {
		pol.Checks["secret"].Config = map[string]interface{}{"id": pol.ID}
	}
	store := tenant.NewStore()
	store.Set("org", &tenant.OrgConfig{Guardrails: cfg})
	p := NewPlugin(nil, nil, factory, nil, store)
	rc := keyTestContext("key-a")
	defer rc.Release()
	if result := p.ProcessRequest(context.Background(), rc); result.Action != pipeline.Continue {
		t.Fatal(result)
	}
	if created != 3 || len(rc.GuardrailResults) != 3 {
		t.Fatalf("created=%d results=%+v", created, rc.GuardrailResults)
	}
	keyA := keyPolicyCacheKey("secret", "a", cfg.KeyPolicies[0].Checks["secret"])
	keyB := keyPolicyCacheKey("secret", "b", cfg.KeyPolicies[1].Checks["secret"])
	if p.dynamicCache["org"][keyA] == nil || p.dynamicCache["org"]["secret"] == p.dynamicCache["org"][keyA] || p.dynamicCache["org"][keyA] == p.dynamicCache["org"][keyB] {
		t.Fatal("shared cache instances")
	}
	p.InvalidateDynamicCache("org")
	if len(p.dynamicCache["org"]) != 0 {
		t.Fatal("cache not invalidated")
	}
}

func TestKeyPolicyRunsDespiteStaticRuleSameName(t *testing.T) {
	g := &keyTestCheck{name: "secret"}
	cfg := &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{"secret": {Enabled: true, Action: "block"}}, KeyPolicies: []*tenant.KeyGuardrailPolicy{keyTestPolicy("key", "enforce", "log", false)}}
	store := tenant.NewStore()
	store.Set("org", &tenant.OrgConfig{Guardrails: cfg})
	engine := NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{{Name: "secret", Stage: "post", Mode: "sync", Action: "log"}}}, map[string]Guardrail{"secret": g})
	p := NewPlugin(engine, map[string]Guardrail{"secret": g}, nil, nil, store)
	rc := keyTestContext("key-a")
	defer rc.Release()
	if result := p.ProcessRequest(context.Background(), rc); result.Action != pipeline.Continue {
		t.Fatalf("static overlap: %+v", result)
	}
	if g.calls != 1 || len(rc.GuardrailResults) != 1 || rc.GuardrailResults[0].PolicyScope != "key" {
		t.Fatalf("static overlap skipped key: calls=%d results=%+v", g.calls, rc.GuardrailResults)
	}
}

func TestKeyPolicyEnforceRedactionAndPassThroughAttribution(t *testing.T) {
	for _, route := range []string{"rewritten", "pass-through"} {
		t.Run(route, func(t *testing.T) {
			g := &keyTestCheck{name: "pii", mutate: true}
			p := keyTestPlugin(&tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{keyTestPolicy("pii-policy", "enforce", "log", true)}}, map[string]Guardrail{"pii": g})
			rc := keyTestContext("key-a")
			defer rc.Release()
			if route == "pass-through" {
				rc.GuardrailRequest = rc.Request
				rc.Request = nil
			}
			if res := p.ProcessRequest(context.Background(), rc); res.Action != pipeline.Continue {
				t.Fatalf("redaction blocked: %+v", res)
			}
			result := rc.GuardrailResults[0]
			if route == "pass-through" {
				if result.Outcome != "logged" || !strings.Contains(result.Message, "forwarded unchanged") {
					t.Fatalf("pass-through reported rewrite: %+v", result)
				}
			} else if result.Outcome != "redacted" || string(rc.Request.Messages[0].Content) != `"rewritten"` {
				t.Fatalf("missing rewrite: %+v %+v", result, rc.Request)
			}
		})
	}
}

func TestOrgRemediationFeedsKeyPass(t *testing.T) {
	org := &keyTestCheck{name: "pii", mutate: true}
	key := &keyTestCheck{name: "secret"}
	policy := keyTestPolicy("key", "enforce", "block", false)
	cfg := &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{"pii": {Enabled: true, Action: "log"}}, KeyPolicies: []*tenant.KeyGuardrailPolicy{policy}}
	p := keyTestPlugin(cfg, map[string]Guardrail{"pii": org, "secret": key})
	rc := keyTestContext("key-a")
	defer rc.Release()
	if res := p.ProcessRequest(context.Background(), rc); res.Action != pipeline.Continue || key.calls != 1 || len(rc.GuardrailResults) != 1 || rc.GuardrailResults[0].Outcome != "redacted" {
		t.Fatalf("key did not see org rewrite: %+v %+v", res, rc.GuardrailResults)
	}
}

func TestKeyPolicyPriorityNameAndIDOrder(t *testing.T) {
	g := &keyTestCheck{name: "secret"}
	makePolicy := func(id, name string, priority int) *tenant.KeyGuardrailPolicy {
		pol := keyTestPolicy(id, "enforce", "log", false)
		pol.Name = name
		pol.Priority = priority
		return pol
	}
	cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{
		makePolicy("last", "z", 2), makePolicy("tie-b", "a", 2), makePolicy("first", "z", 1), makePolicy("tie-a", "a", 2),
	}}
	p := keyTestPlugin(cfg, map[string]Guardrail{"secret": g})
	rc := keyTestContext("key-a")
	defer rc.Release()
	if res := p.ProcessRequest(context.Background(), rc); res.Action != pipeline.Continue {
		t.Fatalf("ordered checks: %+v", res)
	}
	want := []string{"first", "tie-a", "tie-b", "last"}
	if len(rc.GuardrailResults) != len(want) {
		t.Fatalf("results: %+v", rc.GuardrailResults)
	}
	for i, id := range want {
		if rc.GuardrailResults[i].PolicyID != id {
			t.Fatalf("result %d policy=%s want %s", i, rc.GuardrailResults[i].PolicyID, id)
		}
	}
}
