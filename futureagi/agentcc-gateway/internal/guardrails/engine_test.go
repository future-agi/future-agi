package guardrails

import (
	"context"
	"encoding/json"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// ---------------------------------------------------------------------------
// Mock Guardrail
// ---------------------------------------------------------------------------

type mockGuardrail struct {
	name   string
	stage  Stage
	result *CheckResult
	delay  time.Duration
}

type keywordListGuardrail struct {
	name  string
	words []string
}

type configWordGuardrail struct {
	name string
	word string
}

func (g *mockGuardrail) Name() string { return g.name }
func (g *mockGuardrail) Stage() Stage { return g.stage }
func (g *mockGuardrail) Check(ctx context.Context, input *CheckInput) *CheckResult {
	if g.delay > 0 {
		select {
		case <-time.After(g.delay):
		case <-ctx.Done():
			return nil
		}
	}
	return g.result
}

func (g *keywordListGuardrail) Name() string { return g.name }
func (g *keywordListGuardrail) Stage() Stage { return StagePre }
func (g *keywordListGuardrail) Check(ctx context.Context, input *CheckInput) *CheckResult {
	if input == nil || input.Request == nil {
		return &CheckResult{Pass: true}
	}

	for _, message := range input.Request.Messages {
		var content string
		if err := json.Unmarshal(message.Content, &content); err != nil {
			continue
		}

		for _, word := range g.words {
			if strings.Contains(content, word) {
				return &CheckResult{Pass: false, Score: 1, Message: "keyword matched"}
			}
		}
	}

	return &CheckResult{Pass: true}
}

func (g *configWordGuardrail) Name() string { return g.name }
func (g *configWordGuardrail) Stage() Stage { return StagePre }
func (g *configWordGuardrail) Check(ctx context.Context, input *CheckInput) *CheckResult {
	if input == nil || input.Request == nil || g.word == "" {
		return &CheckResult{Pass: true}
	}

	for _, message := range input.Request.Messages {
		var content string
		if err := json.Unmarshal(message.Content, &content); err != nil {
			continue
		}
		if strings.Contains(content, g.word) {
			return &CheckResult{Pass: false, Score: 1, Message: "keyword matched"}
		}
	}

	return &CheckResult{Pass: true}
}

func newOrgThresholdPluginTestGuardrail(name string, score float64) *GuardrailPlugin {
	registry := map[string]Guardrail{
		name: &mockGuardrail{
			name:  name,
			stage: StagePre,
			result: &CheckResult{
				Pass:    false,
				Score:   score,
				Message: "threshold regression",
			},
		},
	}

	tenantStore := tenant.NewStore()
	tenantStore.Set("org-th3967", &tenant.OrgConfig{
		Guardrails: &tenant.GuardrailConfig{
			Checks: map[string]*tenant.GuardrailCheck{
				name: {
					Enabled:             true,
					Action:              "block",
					ConfidenceThreshold: 0.8,
				},
			},
		},
	})

	return NewPlugin(nil, registry, nil, nil, tenantStore)
}

func newOrgThresholdPluginTestRequestContext() *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
	rc.Model = "gpt-4o"
	rc.Metadata["org_id"] = "org-th3967"
	return rc
}

// ---------------------------------------------------------------------------
// Engine Tests
// ---------------------------------------------------------------------------

func TestEngine_SyncBlock(t *testing.T) {
	registry := map[string]Guardrail{
		"blocker": &mockGuardrail{
			name:  "blocker",
			stage: StagePre,
			result: &CheckResult{
				Pass:    false,
				Score:   0.95,
				Action:  ActionBlock,
				Message: "PII detected",
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "blocker", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.8},
		},
	}
	engine := NewEngine(cfg, registry)

	input := &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}

	result := engine.RunPre(context.Background(), input, nil, policy.RequestPolicyNone)
	if !result.Blocked {
		t.Fatal("expected request to be blocked")
	}
	if len(result.Triggered) != 1 {
		t.Fatalf("expected 1 triggered, got %d", len(result.Triggered))
	}
	if result.Triggered[0].Name != "blocker" {
		t.Errorf("triggered name = %q, want 'blocker'", result.Triggered[0].Name)
	}
}

func TestEngine_SyncWarn(t *testing.T) {
	registry := map[string]Guardrail{
		"warner": &mockGuardrail{
			name:  "warner",
			stage: StagePre,
			result: &CheckResult{
				Pass:    false,
				Score:   0.85,
				Action:  ActionWarn,
				Message: "mild toxicity",
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "warner", Stage: "pre", Mode: "sync", Action: "warn", Threshold: 0.7},
		},
	}
	engine := NewEngine(cfg, registry)

	input := &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}

	result := engine.RunPre(context.Background(), input, nil, policy.RequestPolicyNone)
	if result.Blocked {
		t.Fatal("should not be blocked for warn action")
	}
	if len(result.Warnings) != 1 {
		t.Fatalf("expected 1 warning, got %d", len(result.Warnings))
	}
	if len(result.Triggered) != 1 {
		t.Fatalf("expected 1 triggered, got %d", len(result.Triggered))
	}
}

func TestEngine_SyncLog(t *testing.T) {
	registry := map[string]Guardrail{
		"logger": &mockGuardrail{
			name:  "logger",
			stage: StagePre,
			result: &CheckResult{
				Pass:    false,
				Score:   0.9,
				Action:  ActionLog,
				Message: "flagged for review",
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "logger", Stage: "pre", Mode: "sync", Action: "log", Threshold: 0.5},
		},
	}
	engine := NewEngine(cfg, registry)

	input := &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}

	result := engine.RunPre(context.Background(), input, nil, policy.RequestPolicyNone)
	if result.Blocked {
		t.Fatal("log action should not block")
	}
	if len(result.Warnings) != 0 {
		t.Errorf("log action should produce no warnings, got %d", len(result.Warnings))
	}
	if len(result.Triggered) != 1 {
		t.Fatalf("expected 1 triggered, got %d", len(result.Triggered))
	}
}

func TestEngine_PassBelowThreshold(t *testing.T) {
	registry := map[string]Guardrail{
		"checker": &mockGuardrail{
			name:  "checker",
			stage: StagePre,
			result: &CheckResult{
				Pass:  true,
				Score: 0.3,
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "checker", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.8},
		},
	}
	engine := NewEngine(cfg, registry)

	result := engine.RunPre(context.Background(), &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}, nil, policy.RequestPolicyNone)

	if result.Blocked {
		t.Fatal("should pass when score is below threshold")
	}
	if len(result.Triggered) != 0 {
		t.Errorf("expected 0 triggered, got %d", len(result.Triggered))
	}
}

func TestEngine_ExactThresholdPasses(t *testing.T) {
	registry := map[string]Guardrail{
		"checker": &mockGuardrail{
			name:  "checker",
			stage: StagePre,
			result: &CheckResult{
				Pass:  true,
				Score: 0.8, // exactly at threshold
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "checker", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.8},
		},
	}
	engine := NewEngine(cfg, registry)

	result := engine.RunPre(context.Background(), &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}, nil, policy.RequestPolicyNone)

	if result.Blocked {
		t.Fatal("score exactly at threshold should pass (must exceed)")
	}
}

func TestEngine_PromptInjectionBelowThresholdPasses_TH3967(t *testing.T) {
	registry := map[string]Guardrail{
		"prompt-injection": &mockGuardrail{
			name:  "prompt-injection",
			stage: StagePre,
			result: &CheckResult{
				Pass:    false,
				Score:   0.7333333333333334,
				Message: "Potential prompt injection detected",
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "prompt-injection", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.8},
		},
	}
	engine := NewEngine(cfg, registry)

	result := engine.RunPre(context.Background(), &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}, nil, policy.RequestPolicyNone)

	if result.Blocked {
		t.Fatal("prompt-injection score 0.73 below threshold 0.8 should not block")
	}
	if len(result.Triggered) != 0 {
		t.Fatalf("expected 0 triggered guardrails, got %d", len(result.Triggered))
	}
}

func TestPlugin_OrgPromptInjectionBelowThresholdPasses_TH3967(t *testing.T) {
	plugin := newOrgThresholdPluginTestGuardrail("prompt-injection", 0.7333333333333334)
	rc := newOrgThresholdPluginTestRequestContext()
	defer rc.Release()

	result := plugin.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatal("org-config prompt-injection score 0.73 below threshold 0.8 should not block")
	}
	if rc.Flags.GuardrailTriggered {
		t.Fatal("guardrail should not be marked as triggered below threshold")
	}
}

func TestPlugin_OtherScoreBasedGuardrailsBelowThresholdPass_TH3967(t *testing.T) {
	tests := []struct {
		name  string
		score float64
	}{
		{name: "pii-detection", score: 0.3},
		{name: "content-moderation", score: 0.25},
		{name: "secret-detection", score: 0.4},
		{name: "keyword-blocklist", score: 0.5},
		{name: "lakera", score: 0.67},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			plugin := newOrgThresholdPluginTestGuardrail(tt.name, tt.score)
			rc := newOrgThresholdPluginTestRequestContext()
			defer rc.Release()

			result := plugin.ProcessRequest(context.Background(), rc)
			if result.Action != pipeline.Continue {
				t.Fatalf("%s score %.2f below threshold 0.8 should not block", tt.name, tt.score)
			}
			if rc.Flags.GuardrailTriggered {
				t.Fatalf("%s should not be marked as triggered below threshold", tt.name)
			}
		})
	}
}

func TestPlugin_OrgKeywordBlocklistUsesTenantWords(t *testing.T) {
	registry := map[string]Guardrail{
		"keyword-blocklist": &keywordListGuardrail{
			name:  "keyword-blocklist",
			words: []string{"staticword"},
		},
	}

	tenantStore := tenant.NewStore()
	tenantStore.Set("org-keyword-blocklist", &tenant.OrgConfig{
		Guardrails: &tenant.GuardrailConfig{
			Checks: map[string]*tenant.GuardrailCheck{
				"keyword-blocklist": {
					Enabled:             true,
					Action:              "block",
					ConfidenceThreshold: 0,
					Config: map[string]interface{}{
						"words": []interface{}{"tenantword"},
					},
				},
			},
		},
	})

	dynamicFactory := func(name string, cfg map[string]interface{}) Guardrail {
		if name == "keyword-blocklist" {
			words, _ := cfg["words"].([]interface{})
			list := make([]string, 0, len(words))
			for _, word := range words {
				if text, ok := word.(string); ok {
					list = append(list, text)
				}
			}

			return &keywordListGuardrail{name: name, words: list}
		}
		return nil
	}

	plugin := NewPlugin(nil, registry, dynamicFactory, nil, tenantStore)

	raw, err := json.Marshal("this request contains tenantword but not the static keyword")
	if err != nil {
		t.Fatalf("marshal request content: %v", err)
	}

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = &models.ChatCompletionRequest{
		Model: "gpt-4o",
		Messages: []models.Message{
			{Role: "user", Content: raw},
		},
	}
	rc.Metadata["org_id"] = "org-keyword-blocklist"

	result := plugin.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.ShortCircuit {
		t.Fatal("tenant keyword-blocklist words should block matching content")
	}
	if rc.Metadata["guardrail_action"] != "blocked" {
		t.Errorf("guardrail_action = %q, want 'blocked'", rc.Metadata["guardrail_action"])
	}
}

func TestPlugin_OrgBuiltInGuardrailUsesTenantConfig(t *testing.T) {
	registry := map[string]Guardrail{
		"topic-restriction": &configWordGuardrail{name: "topic-restriction", word: "finance"},
	}

	tenantStore := tenant.NewStore()
	tenantStore.Set("org-topic-tenant", &tenant.OrgConfig{
		Guardrails: &tenant.GuardrailConfig{
			Checks: map[string]*tenant.GuardrailCheck{
				"topic-restriction": {
					Enabled:             true,
					Action:              "block",
					ConfidenceThreshold: 0,
					Config: map[string]interface{}{
						"word": "sports",
					},
				},
			},
		},
	})

	dynamicFactory := func(name string, cfg map[string]interface{}) Guardrail {
		if name == "topic-restriction" {
			word, _ := cfg["word"].(string)
			return &configWordGuardrail{name: name, word: word}
		}
		return nil
	}

	plugin := NewPlugin(nil, registry, dynamicFactory, nil, tenantStore)

	raw, err := json.Marshal("this request is about sports and should only be blocked by tenant config")
	if err != nil {
		t.Fatalf("marshal request content: %v", err)
	}

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = &models.ChatCompletionRequest{
		Model: "gpt-4o",
		Messages: []models.Message{
			{Role: "user", Content: raw},
		},
	}
	rc.Metadata["org_id"] = "org-topic-tenant"

	result := plugin.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.ShortCircuit {
		t.Fatal("tenant topic-restriction config should block matching tenant-only content")
	}
	if rc.Metadata["guardrail_name"] != "topic-restriction" {
		t.Errorf("guardrail_name = %q, want topic-restriction", rc.Metadata["guardrail_name"])
	}
}

func TestEngine_TimeoutFailOpen(t *testing.T) {
	registry := map[string]Guardrail{
		"slow": &mockGuardrail{
			name:  "slow",
			stage: StagePre,
			delay: 500 * time.Millisecond,
			result: &CheckResult{
				Pass:  false,
				Score: 0.9,
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 50 * time.Millisecond,
		Rules: []config.GuardrailRuleConfig{
			{Name: "slow", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.5},
		},
	}
	engine := NewEngine(cfg, registry)

	result := engine.RunPre(context.Background(), &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}, nil, policy.RequestPolicyNone)

	if result.Blocked {
		t.Fatal("fail-open should allow request when guardrail times out")
	}
}

func TestEngine_TimeoutFailClosed(t *testing.T) {
	registry := map[string]Guardrail{
		"slow": &mockGuardrail{
			name:  "slow",
			stage: StagePre,
			delay: 500 * time.Millisecond,
			result: &CheckResult{
				Pass:  false,
				Score: 0.9,
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       false, // fail-closed
		DefaultTimeout: 50 * time.Millisecond,
		Rules: []config.GuardrailRuleConfig{
			{Name: "slow", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.5},
		},
	}
	engine := NewEngine(cfg, registry)

	result := engine.RunPre(context.Background(), &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}, nil, policy.RequestPolicyNone)

	if !result.Blocked {
		t.Fatal("fail-closed should block request when guardrail times out")
	}
}

func TestEngine_AsyncDoesNotBlock(t *testing.T) {
	var called atomic.Bool
	registry := map[string]Guardrail{
		"async-checker": &mockGuardrail{
			name:  "async-checker",
			stage: StagePre,
			delay: 100 * time.Millisecond,
			result: &CheckResult{
				Pass:    false,
				Score:   0.99,
				Message: "bad content",
			},
		},
	}

	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "async-checker", Stage: "pre", Mode: "async", Action: "block", Threshold: 0.5},
		},
	}
	engine := NewEngine(cfg, registry)

	start := time.Now()
	result := engine.RunPre(context.Background(), &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}, nil, policy.RequestPolicyNone)
	elapsed := time.Since(start)

	if result.Blocked {
		t.Fatal("async guardrail should not block")
	}
	if elapsed > 50*time.Millisecond {
		t.Errorf("async should return immediately, took %v", elapsed)
	}

	// Wait for async to complete.
	time.Sleep(200 * time.Millisecond)
	_ = called // async guardrail ran in background
}

func TestEngine_MultipleGuardrails_FirstBlockWins(t *testing.T) {
	registry := map[string]Guardrail{
		"g1": &mockGuardrail{
			name:  "g1",
			stage: StagePre,
			result: &CheckResult{
				Pass:    false,
				Score:   0.95,
				Action:  ActionBlock,
				Message: "first block",
			},
		},
		"g2": &mockGuardrail{
			name:  "g2",
			stage: StagePre,
			result: &CheckResult{
				Pass:    false,
				Score:   0.99,
				Action:  ActionBlock,
				Message: "second block",
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "g1", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.8},
			{Name: "g2", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.8},
		},
	}
	engine := NewEngine(cfg, registry)

	result := engine.RunPre(context.Background(), &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}, nil, policy.RequestPolicyNone)

	if !result.Blocked {
		t.Fatal("should be blocked")
	}
	if len(result.Triggered) != 1 {
		t.Fatalf("expected 1 triggered (short-circuit), got %d", len(result.Triggered))
	}
	if result.Triggered[0].Name != "g1" {
		t.Errorf("first block should win, got %q", result.Triggered[0].Name)
	}
}

func TestEngine_PostStage(t *testing.T) {
	registry := map[string]Guardrail{
		"toxicity": &mockGuardrail{
			name:  "toxicity",
			stage: StagePost,
			result: &CheckResult{
				Pass:    false,
				Score:   0.9,
				Action:  ActionBlock,
				Message: "toxic response",
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "toxicity", Stage: "post", Mode: "sync", Action: "block", Threshold: 0.7},
		},
	}
	engine := NewEngine(cfg, registry)

	resp := &models.ChatCompletionResponse{
		ID:    "test",
		Model: "gpt-4o",
		Choices: []models.Choice{
			{
				Message: models.Message{
					Role:    "assistant",
					Content: json.RawMessage(`"toxic content"`),
				},
			},
		},
	}

	result := engine.RunPost(context.Background(), &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Response: resp,
		Metadata: map[string]string{},
	}, nil, policy.RequestPolicyNone, nil)

	if !result.Blocked {
		t.Fatal("post-stage should block toxic response")
	}
}

func TestEngine_UnknownGuardrailSkipped(t *testing.T) {
	registry := map[string]Guardrail{} // empty registry

	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "nonexistent", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.5},
		},
	}
	engine := NewEngine(cfg, registry)

	if engine.PreCount() != 0 {
		t.Errorf("unknown guardrail should be skipped, pre count = %d", engine.PreCount())
	}
}

func TestEngine_NilResult(t *testing.T) {
	registry := map[string]Guardrail{
		"nil-checker": &mockGuardrail{
			name:   "nil-checker",
			stage:  StagePre,
			result: nil, // returns nil
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "nil-checker", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.5},
		},
	}
	engine := NewEngine(cfg, registry)

	result := engine.RunPre(context.Background(), &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}, nil, policy.RequestPolicyNone)

	if result.Blocked {
		t.Fatal("nil result should be treated as pass")
	}
}

func TestEngine_PerRuleTimeout(t *testing.T) {
	registry := map[string]Guardrail{
		"slow": &mockGuardrail{
			name:  "slow",
			stage: StagePre,
			delay: 200 * time.Millisecond,
			result: &CheckResult{
				Pass:  false,
				Score: 0.9,
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second, // high default
		Rules: []config.GuardrailRuleConfig{
			{Name: "slow", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.5, Timeout: 50 * time.Millisecond}, // short per-rule timeout
		},
	}
	engine := NewEngine(cfg, registry)

	result := engine.RunPre(context.Background(), &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}, nil, policy.RequestPolicyNone)

	if result.Blocked {
		t.Fatal("per-rule timeout with fail-open should pass")
	}
}

// ---------------------------------------------------------------------------
// Plugin Tests
// ---------------------------------------------------------------------------

func TestPlugin_PreBlock(t *testing.T) {
	registry := map[string]Guardrail{
		"blocker": &mockGuardrail{
			name:  "blocker",
			stage: StagePre,
			result: &CheckResult{
				Pass:    false,
				Score:   0.95,
				Action:  ActionBlock,
				Message: "PII found",
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "blocker", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.8},
		},
	}
	engine := NewEngine(cfg, registry)
	plugin := NewPlugin(engine, nil, nil, nil, nil)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
	rc.Model = "gpt-4o"

	result := plugin.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.ShortCircuit {
		t.Fatal("expected ShortCircuit on block")
	}
	if result.Error == nil {
		t.Fatal("expected error on block")
	}
	if !rc.Flags.GuardrailTriggered {
		t.Error("GuardrailTriggered flag should be set")
	}
	if rc.Metadata["guardrail_action"] != "blocked" {
		t.Errorf("guardrail_action = %q, want 'blocked'", rc.Metadata["guardrail_action"])
	}
}

func TestPlugin_PreWarn(t *testing.T) {
	registry := map[string]Guardrail{
		"warner": &mockGuardrail{
			name:  "warner",
			stage: StagePre,
			result: &CheckResult{
				Pass:    false,
				Score:   0.85,
				Action:  ActionWarn,
				Message: "mild issue",
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "warner", Stage: "pre", Mode: "sync", Action: "warn", Threshold: 0.7},
		},
	}
	engine := NewEngine(cfg, registry)
	plugin := NewPlugin(engine, nil, nil, nil, nil)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
	rc.Model = "gpt-4o"

	result := plugin.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatal("warn should not block")
	}
	if !rc.Flags.GuardrailTriggered {
		t.Error("GuardrailTriggered should be set for warnings")
	}
	if rc.Metadata["guardrail_action"] != "warned" {
		t.Errorf("guardrail_action = %q, want 'warned'", rc.Metadata["guardrail_action"])
	}
}

func TestPlugin_PostBlock(t *testing.T) {
	registry := map[string]Guardrail{
		"toxicity": &mockGuardrail{
			name:  "toxicity",
			stage: StagePost,
			result: &CheckResult{
				Pass:    false,
				Score:   0.92,
				Action:  ActionBlock,
				Message: "toxic response",
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "toxicity", Stage: "post", Mode: "sync", Action: "block", Threshold: 0.7},
		},
	}
	engine := NewEngine(cfg, registry)
	plugin := NewPlugin(engine, nil, nil, nil, nil)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
	rc.Response = &models.ChatCompletionResponse{ID: "test"}
	rc.Model = "gpt-4o"

	result := plugin.ProcessResponse(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("post-block should return error")
	}
	if rc.Response != nil {
		t.Error("response should be cleared on post-block")
	}
	if !rc.Flags.GuardrailTriggered {
		t.Error("GuardrailTriggered should be set")
	}
}

func TestPlugin_NoGuardrails(t *testing.T) {
	engine := NewEngine(config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
	}, nil)
	plugin := NewPlugin(engine, nil, nil, nil, nil)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}

	result := plugin.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Error("no guardrails should always continue")
	}
}

// ---------------------------------------------------------------------------
// Per-Key Policy Tests
// ---------------------------------------------------------------------------

func TestEngine_PolicyDisableGuardrail(t *testing.T) {
	registry := map[string]Guardrail{
		"blocker": &mockGuardrail{
			name:  "blocker",
			stage: StagePre,
			result: &CheckResult{
				Pass:  false,
				Score: 0.95,
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "blocker", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.5},
		},
	}
	engine := NewEngine(cfg, registry)

	// Policy disables this guardrail.
	p := &policy.Policy{
		Overrides: map[string]policy.Override{
			"blocker": {Disabled: true},
		},
	}

	result := engine.RunPre(context.Background(), &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}, p, policy.RequestPolicyNone)

	if result.Blocked {
		t.Fatal("disabled guardrail should not block")
	}
	if len(result.Triggered) != 0 {
		t.Errorf("expected 0 triggered, got %d", len(result.Triggered))
	}
}

func TestEngine_PolicyOverrideAction(t *testing.T) {
	registry := map[string]Guardrail{
		"blocker": &mockGuardrail{
			name:  "blocker",
			stage: StagePre,
			result: &CheckResult{
				Pass:    false,
				Score:   0.95,
				Message: "triggered",
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "blocker", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.5},
		},
	}
	engine := NewEngine(cfg, registry)

	// Policy downgrades action from block to log.
	p := &policy.Policy{
		Overrides: map[string]policy.Override{
			"blocker": {Action: "log"},
		},
	}

	result := engine.RunPre(context.Background(), &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}, p, policy.RequestPolicyNone)

	if result.Blocked {
		t.Fatal("policy downgraded to log should not block")
	}
	if len(result.Triggered) != 1 {
		t.Fatalf("expected 1 triggered, got %d", len(result.Triggered))
	}
	if result.Triggered[0].Action != ActionLog {
		t.Errorf("expected ActionLog, got %v", result.Triggered[0].Action)
	}
}

func TestEngine_PolicyOverrideThreshold(t *testing.T) {
	registry := map[string]Guardrail{
		"checker": &mockGuardrail{
			name:  "checker",
			stage: StagePre,
			result: &CheckResult{
				Pass:  true, // pass=true, but score exceeds global threshold
				Score: 0.7,
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "checker", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.5},
		},
	}
	engine := NewEngine(cfg, registry)

	// Without policy: score 0.7 > threshold 0.5 → triggers.
	result := engine.RunPre(context.Background(), &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}, nil, policy.RequestPolicyNone)
	if !result.Blocked {
		t.Fatal("without policy, score 0.7 > threshold 0.5 should block")
	}

	// With policy: threshold raised to 0.8 → score 0.7 no longer triggers.
	newThresh := 0.8
	p := &policy.Policy{
		Overrides: map[string]policy.Override{
			"checker": {Threshold: &newThresh},
		},
	}

	result = engine.RunPre(context.Background(), &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}, p, policy.RequestPolicyNone)

	if result.Blocked {
		t.Fatal("raised threshold should prevent trigger")
	}
	if len(result.Triggered) != 0 {
		t.Errorf("expected 0 triggered, got %d", len(result.Triggered))
	}
}

func TestEngine_RequestPolicyLogOnly(t *testing.T) {
	registry := map[string]Guardrail{
		"blocker": &mockGuardrail{
			name:  "blocker",
			stage: StagePre,
			result: &CheckResult{
				Pass:    false,
				Score:   0.95,
				Message: "blocked",
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "blocker", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.5},
		},
	}
	engine := NewEngine(cfg, registry)

	result := engine.RunPre(context.Background(), &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}, nil, policy.RequestPolicyLogOnly)

	if result.Blocked {
		t.Fatal("log-only request policy should not block")
	}
	if len(result.Triggered) != 1 {
		t.Fatalf("expected 1 triggered, got %d", len(result.Triggered))
	}
}

func TestEngine_RequestPolicyDisabled(t *testing.T) {
	registry := map[string]Guardrail{
		"blocker": &mockGuardrail{
			name:  "blocker",
			stage: StagePre,
			result: &CheckResult{
				Pass:  false,
				Score: 0.95,
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "blocker", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.5},
		},
	}
	engine := NewEngine(cfg, registry)

	result := engine.RunPre(context.Background(), &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}, nil, policy.RequestPolicyDisabled)

	if result.Blocked {
		t.Fatal("disabled request policy should skip all guardrails")
	}
	if len(result.Triggered) != 0 {
		t.Errorf("expected 0 triggered, got %d", len(result.Triggered))
	}
}

func TestEngine_RequestPolicyStrict(t *testing.T) {
	registry := map[string]Guardrail{
		"checker": &mockGuardrail{
			name:  "checker",
			stage: StagePre,
			result: &CheckResult{
				Pass:  true,
				Score: 0.01, // very low score
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "checker", Stage: "pre", Mode: "sync", Action: "warn", Threshold: 0.8},
		},
	}
	engine := NewEngine(cfg, registry)

	// Strict: threshold=0, action=block — even score 0.01 triggers.
	result := engine.RunPre(context.Background(), &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}, nil, policy.RequestPolicyStrict)

	if !result.Blocked {
		t.Fatal("strict policy should block even low scores")
	}
}

func TestEngine_RequestPolicyOverridesKeyPolicy(t *testing.T) {
	registry := map[string]Guardrail{
		"blocker": &mockGuardrail{
			name:  "blocker",
			stage: StagePre,
			result: &CheckResult{
				Pass:    false,
				Score:   0.95,
				Message: "triggered",
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "blocker", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.5},
		},
	}
	engine := NewEngine(cfg, registry)

	// Key policy says block, but request policy says log-only.
	p := &policy.Policy{
		Overrides: map[string]policy.Override{
			"blocker": {Action: "block"},
		},
	}

	result := engine.RunPre(context.Background(), &CheckInput{
		Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
		Metadata: map[string]string{},
	}, p, policy.RequestPolicyLogOnly)

	if result.Blocked {
		t.Fatal("request policy log-only should override key policy block")
	}
}

func TestPlugin_PolicyStoreIntegration(t *testing.T) {
	registry := map[string]Guardrail{
		"blocker": &mockGuardrail{
			name:  "blocker",
			stage: StagePre,
			result: &CheckResult{
				Pass:    false,
				Score:   0.95,
				Message: "blocked",
			},
		},
	}
	cfg := config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "blocker", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.5},
		},
	}
	engine := NewEngine(cfg, registry)

	// Set up policy store with key that disables the guardrail.
	pStore := policy.NewStore()
	pStore.Register("key_1", &config.KeyGuardrailPolicyConfig{
		Overrides: []config.KeyGuardrailOverride{
			{Name: "blocker", Disabled: true},
		},
	})

	plug := NewPlugin(engine, nil, nil, pStore, nil)

	// Request from key_1 — guardrail should be disabled.
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
	rc.Model = "gpt-4o"
	rc.Metadata["auth_key_id"] = "key_1"

	result := plug.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatal("key policy should disable guardrail, expected Continue")
	}
	if rc.Flags.GuardrailTriggered {
		t.Error("no guardrail should have triggered")
	}
}

func TestPlugin_RequestPolicyForbidden(t *testing.T) {
	engine := NewEngine(config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "g1", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.5},
		},
	}, map[string]Guardrail{
		"g1": &mockGuardrail{name: "g1", stage: StagePre, result: &CheckResult{Pass: true}},
	})

	plug := NewPlugin(engine, nil, nil, nil, nil)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
	rc.Metadata["x-guardrail-policy"] = "log-only"
	// key_allow_policy_override is NOT set

	result := plug.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("expected error when policy override not allowed")
	}
}

func TestPlugin_RequestPolicyAllowed(t *testing.T) {
	registry := map[string]Guardrail{
		"blocker": &mockGuardrail{
			name:  "blocker",
			stage: StagePre,
			result: &CheckResult{
				Pass:    false,
				Score:   0.95,
				Message: "blocked",
			},
		},
	}
	engine := NewEngine(config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "blocker", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.5},
		},
	}, registry)

	plug := NewPlugin(engine, nil, nil, nil, nil)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
	rc.Metadata["x-guardrail-policy"] = "disabled"
	rc.Metadata["key_allow_policy_override"] = "true"

	result := plug.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatal("disabled policy with allowed override should skip all guardrails")
	}
}

func TestPlugin_InvalidRequestPolicy(t *testing.T) {
	engine := NewEngine(config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "g1", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.5},
		},
	}, map[string]Guardrail{
		"g1": &mockGuardrail{name: "g1", stage: StagePre, result: &CheckResult{Pass: true}},
	})

	plug := NewPlugin(engine, nil, nil, nil, nil)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
	rc.Metadata["x-guardrail-policy"] = "bogus"
	rc.Metadata["key_allow_policy_override"] = "true"

	result := plug.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("expected error for invalid policy value")
	}
}
