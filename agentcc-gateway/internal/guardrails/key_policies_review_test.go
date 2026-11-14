package guardrails

import (
	"context"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A key-policy check with no config must not run the gateway's static
// instance, which config.yaml may have configured (e.g. PII redaction for
// every request). It gets its own instance from the key policy's config.
func TestKeyPolicyCheckWithoutConfigDoesNotUseStaticInstance(t *testing.T) {
	static := &keyTestCheck{name: "pii", mutate: true}
	own := &keyTestCheck{name: "pii"}
	factoryCalls := 0
	store := tenant.NewStore()
	store.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{
		{ID: "p1", Name: "p1", Mode: "enforce", KeyIDs: []string{"key-a"}, Checks: map[string]*tenant.GuardrailCheck{"pii": {Enabled: true, Action: "log"}}},
	}}})
	p := NewPlugin(nil, map[string]Guardrail{"pii": static}, func(name string, cfg map[string]interface{}) Guardrail {
		factoryCalls++
		if name == "pii" {
			return own
		}
		return nil
	}, nil, store)

	rc := keyTestContext("key-a")
	defer rc.Release()
	p.ProcessRequest(context.Background(), rc)
	if static.calls != 0 || own.calls != 1 || factoryCalls != 1 {
		t.Fatalf("static calls %d, own calls %d, factory calls %d; want 0, 1, 1", static.calls, own.calls, factoryCalls)
	}
	if got := string(rc.Request.Messages[0].Content); got != `"secret"` {
		t.Fatalf("request rewritten to %s by the static instance", got)
	}
}

// On a pass-through route the client's body is forwarded as sent, so a
// monitor-mode remediator can't claim it would have redacted it.
func TestKeyPolicyMonitorRemediatorOnPassThroughIsLogged(t *testing.T) {
	g := &keyTestCheck{name: "pii", mutate: true}
	p := keyTestPlugin(&tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{keyTestPolicy("one", "monitor", "log", true)}}, map[string]Guardrail{"pii": g})
	rc := keyTestContext("key-a")
	defer rc.Release()
	rc.GuardrailRequest, rc.Request = rc.Request, nil

	p.ProcessRequest(context.Background(), rc)
	if len(rc.GuardrailResults) != 1 {
		t.Fatalf("results %+v", rc.GuardrailResults)
	}
	got := rc.GuardrailResults[0]
	if got.Outcome != "logged" || got.Mode != "monitor" || !strings.Contains(got.Message, "forwarded unchanged") {
		t.Fatalf("attribution %+v; want outcome logged with the forwarded-unchanged note", got)
	}
	if string(rc.GuardrailRequest.Messages[0].Content) != `"secret"` {
		t.Fatalf("pass-through projection changed: %s", rc.GuardrailRequest.Messages[0].Content)
	}
	if rc.Flags.GuardrailTriggered {
		t.Fatal("monitor match set GuardrailTriggered")
	}
}

// Monitor records what enforce would have done. When a remediator in the
// policy removes the content a blocking check looks for, enforce redacts and
// does not block, so monitor must not report would_block either.
func TestKeyPolicyMonitorMirrorsEnforceOrder(t *testing.T) {
	for _, mode := range []string{"enforce", "monitor"} {
		t.Run(mode, func(t *testing.T) {
			redactor := &keyTestCheck{name: "pii", mutate: true}
			blocker := &keyTestCheck{name: "secret"}
			pol := &tenant.KeyGuardrailPolicy{ID: "p", Name: "p", Mode: mode, KeyIDs: []string{"key-a"}, Checks: map[string]*tenant.GuardrailCheck{
				"pii":    {Enabled: true, Action: "log"},
				"secret": {Enabled: true, Action: "block"},
			}}
			p := keyTestPlugin(&tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{pol}}, map[string]Guardrail{"pii": redactor, "secret": blocker})
			rc := keyTestContext("key-a")
			defer rc.Release()

			if res := p.ProcessRequest(context.Background(), rc); res.Action == pipeline.ShortCircuit {
				t.Fatalf("blocked: %+v", res)
			}
			outcomes := map[string]string{}
			for _, r := range rc.GuardrailResults {
				outcomes[r.Name] = r.Outcome
			}
			want := map[string]string{"pii": "redacted"}
			wantContent := `"rewritten"`
			if mode == "monitor" {
				want = map[string]string{"pii": "would_redact"}
				wantContent = `"secret"`
			}
			if len(outcomes) != len(want) || outcomes["pii"] != want["pii"] {
				t.Fatalf("outcomes %v; want %v", outcomes, want)
			}
			if got := string(rc.Request.Messages[0].Content); got != wantContent {
				t.Fatalf("request content %s; want %s", got, wantContent)
			}
		})
	}
}
