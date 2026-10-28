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

type responseWordCheck struct {
	name, word string
	stage      Stage
}

func (g *responseWordCheck) Name() string { return g.name }
func (g *responseWordCheck) Stage() Stage { return g.stage }
func (g *responseWordCheck) Check(_ context.Context, in *CheckInput) *CheckResult {
	if in.Response == nil {
		return &CheckResult{Pass: true}
	}
	for _, c := range in.Response.Choices {
		if strings.Contains(string(c.Message.Content), g.word) {
			return &CheckResult{Pass: false, Score: 1, Message: "remove " + g.word}
		}
	}
	return &CheckResult{Pass: true}
}
func reflectionRC() *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.Model = "model"
	rc.Request = &models.ChatCompletionRequest{Model: "model", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"original"`)}}}
	return rc
}
func reflectionResponse(text string) *models.ChatCompletionResponse {
	content, _ := json.Marshal(text)
	return &models.ChatCompletionResponse{Model: "model", Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: content}, FinishReason: "stop"}}, Usage: &models.Usage{PromptTokens: 1, CompletionTokens: 1, TotalTokens: 2}}
}

func TestDynamicReflectionStageAndTenantIsolation(t *testing.T) {
	store := tenant.NewStore()
	store.Set("reflect-org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{"output": {Enabled: true, Action: "block", Config: map[string]interface{}{"stage": "post", "on_block": "reflect"}}}}})
	store.Set("reject-org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{"output": {Enabled: true, Action: "block", Config: map[string]interface{}{"stage": "post"}}}}})
	store.Set("legacy-org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{"output": {Enabled: true, Action: "block"}}}})
	plugin := NewPlugin(nil, map[string]Guardrail{"output": &responseWordCheck{name: "output", word: "bad", stage: StagePre}}, nil, nil, store)
	engine := pipeline.NewEngine(plugin)
	for _, tc := range []struct {
		org     string
		calls   int
		blocked bool
	}{{"reflect-org", 2, false}, {"reject-org", 1, true}, {"legacy-org", 1, false}, {"unknown-org", 1, false}} {
		t.Run(tc.org, func(t *testing.T) {
			rc := reflectionRC()
			defer rc.Release()
			rc.Metadata["org_id"] = tc.org
			calls := 0
			err := engine.Process(context.Background(), rc, func(_ context.Context, rc *models.RequestContext) error {
				calls++
				text := "bad"
				if calls > 1 {
					text = "good"
				}
				rc.Response = reflectionResponse(text)
				return nil
			})
			if calls != tc.calls || (err != nil) != tc.blocked {
				t.Fatalf("calls=%d error=%v", calls, err)
			}
			if tc.blocked && rc.Response != nil {
				t.Fatal("dynamic post block retained response")
			}
		})
	}
}

func TestReflectionWholeRequestCeilingDoesNotReset(t *testing.T) {
	r := map[string]Guardrail{"first": &responseWordCheck{name: "first", word: "first"}, "second": &responseWordCheck{name: "second", word: "second"}}
	cfg := config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{
		{Name: "first", Stage: "post", Action: "block", Config: map[string]interface{}{"on_block": "reflect", "reflect_max_attempts": 1}},
		{Name: "second", Stage: "post", Action: "block", Config: map[string]interface{}{"on_block": "reflect", "reflect_max_attempts": 5}},
	}}
	e := pipeline.NewEngine(NewPlugin(NewEngine(cfg, r), r, nil, nil, nil))
	rc := reflectionRC()
	defer rc.Release()
	calls := 0
	err := e.Process(context.Background(), rc, func(_ context.Context, rc *models.RequestContext) error {
		calls++
		text := "first"
		if calls > 1 {
			text = "second"
		}
		rc.Response = reflectionResponse(text)
		return nil
	})
	if err == nil || calls != 2 || rc.Response != nil {
		t.Fatalf("ceiling reset: calls=%d error=%v", calls, err)
	}
}

func TestReflectionAsyncAndOperationalFailuresNeverRetry(t *testing.T) {
	for _, tc := range []struct {
		name, mode string
		delay      time.Duration
		wantCalls  int
	}{{"async", "async", 0, 0}, {"timeout", "sync", 100 * time.Millisecond, 1}} {
		t.Run(tc.name, func(t *testing.T) {
			g := &mockGuardrail{name: "check", stage: StagePost, delay: tc.delay, result: &CheckResult{Pass: false, Score: 1, Message: "blocked"}}
			r := map[string]Guardrail{"check": g}
			cfg := config.GuardrailsConfig{DefaultTimeout: time.Millisecond, Rules: []config.GuardrailRuleConfig{{Name: "check", Stage: "post", Action: "block", Mode: tc.mode, Config: map[string]interface{}{"on_block": "reflect"}}}}
			e := pipeline.NewEngine(NewPlugin(NewEngine(cfg, r), r, nil, nil, nil))
			rc := reflectionRC()
			defer rc.Release()
			calls := 0
			err := e.Process(context.Background(), rc, func(_ context.Context, rc *models.RequestContext) error {
				calls++
				rc.Response = reflectionResponse("body")
				return nil
			})
			if calls != tc.wantCalls || err == nil {
				t.Fatalf("calls=%d error=%v", calls, err)
			}
		})
	}
}

func TestReflectionCancellationDoesNotLaunchNextAttempt(t *testing.T) {
	g := &responseWordCheck{name: "check", word: "bad"}
	r := map[string]Guardrail{"check": g}
	cfg := config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{{Name: "check", Stage: "post", Action: "block", Config: map[string]interface{}{"on_block": "reflect"}}}}
	e := pipeline.NewEngine(NewPlugin(NewEngine(cfg, r), r, nil, nil, nil))
	rc := reflectionRC()
	defer rc.Release()
	calls := 0
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	err := e.Process(ctx, rc, func(_ context.Context, rc *models.RequestContext) error {
		calls++
		rc.Response = reflectionResponse("bad")
		cancel()
		return nil
	})
	if err == nil || calls != 1 || rc.Response != nil || rc.AccountedUsage.TotalTokens != 2 {
		t.Fatalf("calls=%d error=%v usage=%+v", calls, err, rc.AccountedUsage)
	}
}

func TestStaticStageRemainsAuthoritative(t *testing.T) {
	g := &responseWordCheck{name: "check", word: "bad", stage: StagePre}
	r := map[string]Guardrail{"check": g}
	cfg := config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{{Name: "check", Stage: "post", Action: "block", Config: map[string]interface{}{"stage": "pre", "on_block": "reflect"}}}}
	e := pipeline.NewEngine(NewPlugin(NewEngine(cfg, r), r, nil, nil, nil))
	rc := reflectionRC()
	defer rc.Release()
	calls := 0
	err := e.Process(context.Background(), rc, func(_ context.Context, rc *models.RequestContext) error {
		calls++
		rc.Response = reflectionResponse("bad")
		return nil
	})
	if err == nil || calls != 2 {
		t.Fatalf("calls=%d error=%v", calls, err)
	}
}
