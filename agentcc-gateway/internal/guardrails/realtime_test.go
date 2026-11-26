package guardrails

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type realtimeTestCheck struct {
	name  string
	check func(context.Context, *CheckInput) *CheckResult
}

func (g realtimeTestCheck) Name() string { return g.name }
func (g realtimeTestCheck) Stage() Stage { return StagePre }
func (g realtimeTestCheck) Check(ctx context.Context, input *CheckInput) *CheckResult {
	return g.check(ctx, input)
}

func TestRealtimeFailureIsNotAThresholdPass(t *testing.T) {
	for _, mode := range []string{"timeout", "panic", "nil", "rewrite"} {
		t.Run(mode, func(t *testing.T) {
			g := realtimeTestCheck{"test", func(ctx context.Context, in *CheckInput) *CheckResult {
				switch mode {
				case "timeout":
					<-ctx.Done()
					return &CheckResult{Pass: true}
				case "panic":
					panic("synthetic failure")
				case "nil":
					return nil
				case "rewrite":
					in.Request.Messages[0].Content = json.RawMessage(`"redacted"`)
				}
				return &CheckResult{Pass: true}
			}}
			p := &RealtimePolicy{}
			p.rules[StagePre] = []resolvedRule{{guardrail: g, action: ActionBlock, threshold: 1, timeout: time.Millisecond}}
			if err := p.Check(context.Background(), StagePre, "original"); err == nil {
				t.Fatal("unapproved original content must fail closed even at threshold 1")
			}
		})
	}
}

func TestRealtimeChecksOwnTheirInputs(t *testing.T) {
	p := &RealtimePolicy{metadata: map[string]string{"org_id": "original"}}
	mutate := realtimeTestCheck{"mutate", func(_ context.Context, in *CheckInput) *CheckResult {
		in.Request.Messages[0].Content = json.RawMessage(`"changed"`)
		in.Metadata["org_id"] = "changed"
		return &CheckResult{Pass: true}
	}}
	observe := realtimeTestCheck{"observe", func(_ context.Context, in *CheckInput) *CheckResult {
		if string(in.Request.Messages[0].Content) != `"original"` || in.Metadata["org_id"] != "original" {
			return &CheckResult{Pass: false}
		}
		return &CheckResult{Pass: true}
	}}
	p.rules[StagePre] = []resolvedRule{{guardrail: mutate, action: ActionLog, timeout: time.Second}, {guardrail: observe, action: ActionBlock, timeout: time.Second}}
	if err := p.Check(context.Background(), StagePre, "original"); err != nil {
		t.Fatal(err)
	}
}

func TestRealtimeMissingBlockingImplementation(t *testing.T) {
	for _, source := range []string{"static", "managed"} {
		t.Run(source, func(t *testing.T) {
			cfg := config.GuardrailsConfig{Rules: []config.GuardrailRuleConfig{{Name: "missing", Stage: "post", Action: "block"}}}
			store := tenant.NewStore()
			if source == "managed" {
				cfg.Rules = nil
				store.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{"missing": {Enabled: true, Action: "block"}}}})
			}
			plugin := NewPlugin(NewEngine(cfg, nil), nil, nil, nil, store)
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Metadata["org_id"] = "org"
			if _, err := plugin.BindRealtime(rc, RealtimeTextBoth); err == nil || err.Code != "realtime_guardrail_unsupported" {
				t.Fatalf("missing implementation must refuse, got %v", err)
			}
		})
	}
}

func TestRealtimePostStageTextIsOnlyTheResponse(t *testing.T) {
	var seen *CheckInput
	g := realtimeTestCheck{"observe", func(_ context.Context, in *CheckInput) *CheckResult {
		seen = in
		return &CheckResult{Pass: true}
	}}
	p := &RealtimePolicy{model: "session-model", metadata: map[string]string{"org_id": "org"}}
	p.rules[StagePost] = []resolvedRule{{guardrail: g, action: ActionBlock, timeout: time.Second}}
	if err := p.CheckModel(context.Background(), StagePost, "unit-model", "transcript"); err != nil {
		t.Fatal(err)
	}
	if seen.Request == nil || seen.Request.Model != "unit-model" || len(seen.Request.Messages) != 0 {
		t.Fatalf("post-stage request must keep the model without the text: %+v", seen.Request)
	}
	if seen.Response == nil || len(seen.Response.Choices) != 1 || string(seen.Response.Choices[0].Message.Content) != `"transcript"` {
		t.Fatalf("post-stage response must carry the text once: %+v", seen.Response)
	}
	if seen.Metadata["org_id"] != "org" {
		t.Fatalf("metadata=%v", seen.Metadata)
	}
}

// Speech and transcription relays bind the key's Enforce policies too: a
// blocking check blocks the matching text. A Monitor policy binds nothing, so
// it cannot change speech traffic; another key of the org gets neither.
func TestRealtimeBindsKeyPolicies(t *testing.T) {
	blocked := realtimeTestCheck{"blocklist", func(_ context.Context, in *CheckInput) *CheckResult {
		if string(in.Request.Messages[0].Content) == `"forbidden words"` {
			return &CheckResult{Pass: false, Score: 1}
		}
		return &CheckResult{Pass: true}
	}}
	registry := map[string]Guardrail{"blocklist": blocked}
	bind := func(t *testing.T, mode, keyID string, extra map[string]string) *RealtimePolicy {
		t.Helper()
		store := tenant.NewStore()
		store.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{{
			ID: "pol", Name: "pol", Mode: mode, KeyIDs: []string{"key_a"},
			Checks: map[string]*tenant.GuardrailCheck{"blocklist": {Enabled: true, Action: "block", ConfidenceThreshold: 0.5}},
		}}}})
		plugin := NewPlugin(nil, registry, nil, nil, store)
		rc := models.AcquireRequestContext()
		t.Cleanup(rc.Release)
		rc.Metadata["org_id"] = "org"
		rc.Metadata["auth_key_id"] = keyID
		for k, v := range extra {
			rc.Metadata[k] = v
		}
		bound, err := plugin.BindRealtime(rc, RealtimeTextBoth)
		if err != nil {
			t.Fatalf("bind: %v", err)
		}
		return bound
	}

	t.Run("enforce blocks for the policy's key", func(t *testing.T) {
		bound := bind(t, "enforce", "key_a", nil)
		if !bound.Blocking(StagePre) {
			t.Fatal("enforce key policy bound no blocking pre-stage rule")
		}
		if err := bound.Check(context.Background(), StagePre, "forbidden words"); err == nil {
			t.Fatal("enforce key policy let blocked text through")
		}
		if err := bound.Check(context.Background(), StagePre, "fine words"); err != nil {
			t.Fatalf("clean text: %v", err)
		}
	})
	t.Run("monitor binds nothing, so the relay installs no filter", func(t *testing.T) {
		bound := bind(t, "monitor", "key_a", nil)
		if bound.Has(StagePre) || bound.Has(StagePost) {
			t.Fatalf("monitor: has pre=%v post=%v; a Monitor policy must not change speech traffic", bound.Has(StagePre), bound.Has(StagePost))
		}
		if err := bound.Check(context.Background(), StagePre, "forbidden words"); err != nil {
			t.Fatalf("monitor key policy blocked: %v", err)
		}
	})
	t.Run("other key gets nothing", func(t *testing.T) {
		if bound := bind(t, "enforce", "key_b", nil); bound.Has(StagePre) || bound.Has(StagePost) {
			t.Fatal("a key outside the policy got its checks")
		}
	})
	t.Run("a disabled-policy header does not drop them", func(t *testing.T) {
		bound := bind(t, "enforce", "key_a", map[string]string{"x-guardrail-policy": "disabled", "key_allow_policy_override": "true"})
		if err := bound.Check(context.Background(), StagePre, "forbidden words"); err == nil {
			t.Fatal("X-Guardrail-Policy: disabled turned the key policy off")
		}
	})
}

// A key policy's check binds only in a direction the session reads as text, so
// enforcing a pre-stage check on a key skips transcription sessions instead of
// making the speech handler refuse them. Org checks still bind in both.
func TestRealtimeKeyPolicyReadableDirections(t *testing.T) {
	pass := func(_ context.Context, _ *CheckInput) *CheckResult { return &CheckResult{Pass: true} }
	registry := map[string]Guardrail{
		"pre-check":  realtimeTestCheck{"pre-check", pass},
		"post-check": realtimeStageCheck{realtimeTestCheck{"post-check", pass}, StagePost},
	}
	block := func() map[string]*tenant.GuardrailCheck {
		return map[string]*tenant.GuardrailCheck{
			"pre-check":  {Enabled: true, Action: "block", ConfidenceThreshold: 0.5},
			"post-check": {Enabled: true, Action: "block", ConfidenceThreshold: 0.5},
		}
	}
	bind := func(t *testing.T, readable RealtimeText, orgChecks map[string]*tenant.GuardrailCheck) *RealtimePolicy {
		t.Helper()
		store := tenant.NewStore()
		store.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{
			Checks: orgChecks,
			KeyPolicies: []*tenant.KeyGuardrailPolicy{{
				ID: "pol", Name: "pol", Mode: "enforce", KeyIDs: []string{"key_a"}, Checks: block(),
			}},
		}})
		rc := models.AcquireRequestContext()
		t.Cleanup(rc.Release)
		rc.Metadata["org_id"] = "org"
		rc.Metadata["auth_key_id"] = "key_a"
		bound, err := NewPlugin(nil, registry, nil, nil, store).BindRealtime(rc, readable)
		if err != nil {
			t.Fatalf("bind: %v", err)
		}
		return bound
	}
	for _, tc := range []struct {
		name              string
		readable          RealtimeText
		wantPre, wantPost bool
	}{
		{"transcription reads only post", RealtimeText{StagePost: true}, false, true},
		{"synthesis reads only pre", RealtimeText{StagePre: true}, true, false},
		{"turns reads neither", RealtimeText{}, false, false},
		{"both", RealtimeTextBoth, true, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			bound := bind(t, tc.readable, nil)
			if bound.Blocking(StagePre) != tc.wantPre || bound.Blocking(StagePost) != tc.wantPost {
				t.Fatalf("blocking pre=%v post=%v, want pre=%v post=%v",
					bound.Blocking(StagePre), bound.Blocking(StagePost), tc.wantPre, tc.wantPost)
			}
		})
	}
	t.Run("org checks still bind in an unreadable direction", func(t *testing.T) {
		bound := bind(t, RealtimeText{StagePost: true}, block())
		if !bound.Blocking(StagePre) {
			t.Fatal("an org pre-stage block check must still bind, so the handler refuses the session")
		}
	})
}

type realtimeStageCheck struct {
	realtimeTestCheck
	stage Stage
}

func (c realtimeStageCheck) Stage() Stage { return c.stage }
