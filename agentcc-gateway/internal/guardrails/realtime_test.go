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
			if _, err := plugin.BindRealtime(rc); err == nil || err.Code != "realtime_guardrail_unsupported" {
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
