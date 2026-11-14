package guardrails

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// An enforce key policy that only logs adds to the org pass's metadata; it
// does not replace an org check's "warned" with "logged", which the audit
// record copies.
func TestKeyPolicyMetadataKeepsOrgPass(t *testing.T) {
	orgWarn := &keyTestCheck{name: "org-warn"}
	keyLog := &keyTestCheck{name: "secret"}
	p := keyTestPlugin(&tenant.GuardrailConfig{
		Checks:      map[string]*tenant.GuardrailCheck{"org-warn": {Enabled: true, Action: "warn"}},
		KeyPolicies: []*tenant.KeyGuardrailPolicy{keyTestPolicy("one", "enforce", "log", false)},
	}, map[string]Guardrail{"org-warn": orgWarn, "secret": keyLog})
	rc := keyTestContext("key-a")
	defer rc.Release()
	if result := p.ProcessRequest(context.Background(), rc); result.Action != pipeline.Continue {
		t.Fatalf("result %+v; want continue", result)
	}
	if got := rc.Metadata["guardrail_action"]; got != "warned" {
		t.Errorf("guardrail_action = %q; want warned (the org check warned)", got)
	}
	if got := rc.Metadata["guardrail_name"]; got != "org-warn,secret" {
		t.Errorf("guardrail_name = %q; want org-warn,secret", got)
	}
	if got := rc.Metadata["guardrail_warnings"]; !strings.Contains(got, "org-warn") {
		t.Errorf("guardrail_warnings = %q; want the org warning kept", got)
	}
}

type wordCheck struct{ word string }

func (g wordCheck) Name() string { return "words" }
func (g wordCheck) Stage() Stage { return StagePre }
func (g wordCheck) Check(_ context.Context, in *CheckInput) *CheckResult {
	if strings.Contains(string(in.Request.Messages[0].Content), g.word) {
		return &CheckResult{Score: 1, Message: "matched " + g.word}
	}
	return &CheckResult{Pass: true}
}

// A key-policy check's instance is cached per config: an instance a request
// built from the config it read before a change (and stored after the change
// cleared the cache) is not used for the new config.
func TestKeyPolicyInstanceFollowsCheckConfig(t *testing.T) {
	factory := func(_ string, cfg map[string]interface{}) Guardrail {
		word, _ := cfg["word"].(string)
		return wordCheck{word: word}
	}
	policy := func(word string) *tenant.KeyGuardrailPolicy {
		return &tenant.KeyGuardrailPolicy{ID: "p", Name: "p", Mode: "enforce", KeyIDs: []string{"key-a"},
			Checks: map[string]*tenant.GuardrailCheck{"words": {Enabled: true, Action: "block", Config: map[string]interface{}{"word": word}}}}
	}
	store := tenant.NewStore()
	store.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{policy("alpha")}}})
	p := NewPlugin(nil, nil, factory, nil, store)
	send := func(text string) pipeline.PluginResult {
		rc := keyTestContext("key-a")
		defer rc.Release()
		content, _ := json.Marshal(text)
		rc.Request.Messages[0].Content = content
		return p.ProcessRequest(context.Background(), rc)
	}
	if r := send("say alpha"); r.Action != pipeline.ShortCircuit {
		t.Fatalf("v1: %+v; want blocked", r)
	}
	// The config changes; the instance built for v1 is still cached (the
	// request that built it finished after the change cleared the cache).
	store.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{policy("beta")}}})
	if r := send("say beta"); r.Action != pipeline.ShortCircuit {
		t.Fatalf("v2: %+v; want the new word blocked", r)
	}
	if r := send("say alpha"); r.Action != pipeline.Continue {
		t.Fatalf("v2: %+v; want the old word let through", r)
	}
}

var _ = models.AcquireRequestContext
