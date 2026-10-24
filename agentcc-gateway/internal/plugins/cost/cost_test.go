package cost

import (
	"bytes"
	"context"
	"fmt"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

func newRC(model string, promptTokens, completionTokens int) *models.RequestContext {
	return &models.RequestContext{
		Model: model,
		Response: &models.ChatCompletionResponse{
			Usage: &models.Usage{
				PromptTokens:     promptTokens,
				CompletionTokens: completionTokens,
				TotalTokens:      promptTokens + completionTokens,
			},
		},
		Metadata: make(map[string]string),
		Timings:  make(map[string]time.Duration),
	}
}

// testDB creates a small ModelDB for testing cost calculations.
func testDB() *modeldb.ModelDB {
	return modeldb.New(map[string]*modeldb.ModelInfo{
		"gpt-4o": {
			ID:       "gpt-4o",
			Provider: "openai",
			Mode:     modeldb.ModeChat,
			Pricing: modeldb.PricingInfo{
				InputPerToken:  2.50 / 1_000_000,
				OutputPerToken: 10.00 / 1_000_000,
			},
		},
		"gpt-4o-mini": {
			ID:       "gpt-4o-mini",
			Provider: "openai",
			Mode:     modeldb.ModeChat,
			Pricing: modeldb.PricingInfo{
				InputPerToken:       0.15 / 1_000_000,
				OutputPerToken:      0.60 / 1_000_000,
				CachedInputPerToken: 0.075 / 1_000_000,
			},
		},
		"text-embedding-3-small": {
			ID:       "text-embedding-3-small",
			Provider: "openai",
			Mode:     modeldb.ModeEmbedding,
			Pricing: modeldb.PricingInfo{
				InputPerToken:  0.02 / 1_000_000,
				OutputPerToken: 0,
			},
		},
	}, nil)
}

func testPlugin() *Plugin {
	db := testDB()
	return New(true, func() *modeldb.ModelDB { return db }, nil, nil)
}

func disabledPlugin() *Plugin {
	db := testDB()
	return New(false, func() *modeldb.ModelDB { return db }, nil, nil)
}

// ---------------------------------------------------------------------------
// Plugin basics
// ---------------------------------------------------------------------------

func TestPlugin_Name(t *testing.T) {
	t.Parallel()
	p := testPlugin()
	if got := p.Name(); got != "cost" {
		t.Fatalf("Name() = %q, want %q", got, "cost")
	}
}

func TestPlugin_Priority(t *testing.T) {
	t.Parallel()
	p := testPlugin()
	if got := p.Priority(); got != 500 {
		t.Fatalf("Priority() = %d, want 500", got)
	}
}

func TestPlugin_ProcessRequest_Noop(t *testing.T) {
	t.Parallel()
	p := testPlugin()
	rc := newRC("gpt-4o", 100, 50)
	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatalf("ProcessRequest Action = %v, want Continue (%v)", result.Action, pipeline.Continue)
	}
}

// ---------------------------------------------------------------------------
// Plugin ProcessResponse
// ---------------------------------------------------------------------------

func TestProcessResponse_CalculatesCost(t *testing.T) {
	t.Parallel()
	p := testPlugin()
	rc := newRC("gpt-4o", 1000, 500)

	result := p.ProcessResponse(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatalf("ProcessResponse Action = %v, want Continue", result.Action)
	}

	costStr, ok := rc.Metadata["cost"]
	if !ok {
		t.Fatal("Metadata[cost] not set")
	}
	// gpt-4o: 1000 * 2.50/1e6 + 500 * 10.00/1e6 = 0.0025 + 0.005 = 0.0075
	want := "0.007500"
	if costStr != want {
		t.Fatalf("Metadata[cost] = %q, want %q", costStr, want)
	}
}

func TestProcessResponse_NilUsage(t *testing.T) {
	t.Parallel()
	p := testPlugin()
	rc := &models.RequestContext{
		Model: "gpt-4o",
		Response: &models.ChatCompletionResponse{
			Usage: nil,
		},
		Metadata: make(map[string]string),
		Timings:  make(map[string]time.Duration),
	}

	p.ProcessResponse(context.Background(), rc)

	if _, ok := rc.Metadata["cost"]; ok {
		t.Fatal("Metadata[cost] should not be set when Usage is nil")
	}
}

func TestProcessResponse_NilResponse(t *testing.T) {
	t.Parallel()
	p := testPlugin()
	rc := &models.RequestContext{
		Model:    "gpt-4o",
		Response: nil,
		Metadata: make(map[string]string),
		Timings:  make(map[string]time.Duration),
	}

	p.ProcessResponse(context.Background(), rc)

	if _, ok := rc.Metadata["cost"]; ok {
		t.Fatal("Metadata[cost] should not be set when Response is nil")
	}
}

func TestProcessResponse_UnknownModel(t *testing.T) {
	t.Parallel()
	p := testPlugin()
	rc := newRC("nonexistent-model", 1000, 500)

	p.ProcessResponse(context.Background(), rc)

	if _, ok := rc.Metadata["cost"]; ok {
		t.Fatal("Metadata[cost] should not be set for unknown model")
	}
}

func TestProcessResponse_Disabled(t *testing.T) {
	t.Parallel()
	p := disabledPlugin()
	rc := newRC("gpt-4o", 1000, 500)

	p.ProcessResponse(context.Background(), rc)

	if _, ok := rc.Metadata["cost"]; ok {
		t.Fatal("Metadata[cost] should not be set when plugin is disabled")
	}
}

func TestProcessResponse_ZeroTokens(t *testing.T) {
	t.Parallel()
	p := testPlugin()
	rc := newRC("gpt-4o", 0, 0)

	p.ProcessResponse(context.Background(), rc)

	costStr, ok := rc.Metadata["cost"]
	if !ok {
		t.Fatal("Metadata[cost] not set for zero tokens")
	}
	if costStr != "0.000000" {
		t.Fatalf("Metadata[cost] = %q, want %q", costStr, "0.000000")
	}
}

func TestProcessResponse_ResolvedModelFallback(t *testing.T) {
	t.Parallel()
	p := testPlugin()

	// ResolvedModel is unknown, Model is known. Should fall back to Model.
	rc := newRC("gpt-4o", 1000, 500)
	rc.ResolvedModel = "some-unknown-resolved-model"

	p.ProcessResponse(context.Background(), rc)

	costStr, ok := rc.Metadata["cost"]
	if !ok {
		t.Fatal("Metadata[cost] not set; should have fallen back to rc.Model")
	}
	want := "0.007500"
	if costStr != want {
		t.Fatalf("Metadata[cost] = %q, want %q (expected fallback to rc.Model pricing)", costStr, want)
	}
}

func TestProcessResponse_ResolvedModelPreferred(t *testing.T) {
	t.Parallel()

	// Use custom models so we can distinguish which was used.
	db := modeldb.New(map[string]*modeldb.ModelInfo{
		"resolved-model-x": {
			ID: "resolved-model-x", Provider: "test", Mode: modeldb.ModeChat,
			Pricing: modeldb.PricingInfo{InputPerToken: 100.0 / 1_000_000, OutputPerToken: 200.0 / 1_000_000},
		},
		"requested-model-y": {
			ID: "requested-model-y", Provider: "test", Mode: modeldb.ModeChat,
			Pricing: modeldb.PricingInfo{InputPerToken: 1.0 / 1_000_000, OutputPerToken: 2.0 / 1_000_000},
		},
	}, nil)
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)

	rc := newRC("requested-model-y", 1000, 500)
	rc.ResolvedModel = "resolved-model-x"

	p.ProcessResponse(context.Background(), rc)

	costStr, ok := rc.Metadata["cost"]
	if !ok {
		t.Fatal("Metadata[cost] not set")
	}

	// resolved-model-x: 1000*100/1e6 + 500*200/1e6 = 0.1 + 0.1 = 0.2
	want := "0.200000"
	if costStr != want {
		t.Fatalf("Metadata[cost] = %q, want %q (expected resolved model pricing)", costStr, want)
	}
}

func TestProcessResponse_CachedCost(t *testing.T) {
	t.Parallel()
	p := testPlugin()
	rc := newRC("gpt-4o-mini", 1000, 500)
	rc.Metadata["cache_status"] = "hit"

	p.ProcessResponse(context.Background(), rc)

	costStr, ok := rc.Metadata["cost"]
	if !ok {
		t.Fatal("Metadata[cost] not set for cached request")
	}
	// gpt-4o-mini cached: 1000 * 0.075/1e6 + 500 * 0.60/1e6 = 0.000075 + 0.0003 = 0.000375
	want := "0.000375"
	if costStr != want {
		t.Fatalf("Metadata[cost] = %q, want %q (expected cached pricing)", costStr, want)
	}
}

func TestProcessResponse_NilModelDB(t *testing.T) {
	t.Parallel()
	p := New(true, func() *modeldb.ModelDB { return nil }, nil, nil)
	rc := newRC("gpt-4o", 1000, 500)

	p.ProcessResponse(context.Background(), rc)

	if _, ok := rc.Metadata["cost"]; ok {
		t.Fatal("Metadata[cost] should not be set when ModelDB is nil")
	}
}

// ---------------------------------------------------------------------------
// Endpoint-type-specific cost tests
// ---------------------------------------------------------------------------

func TestProcessResponse_EmbeddingCost(t *testing.T) {
	t.Parallel()
	p := testPlugin()
	rc := &models.RequestContext{
		Model:        "text-embedding-3-small",
		EndpointType: "embedding",
		EmbeddingResponse: &models.EmbeddingResponse{
			Object: "list",
			Model:  "text-embedding-3-small",
			Usage: &models.EmbeddingUsage{
				PromptTokens: 1000,
				TotalTokens:  1000,
			},
		},
		Metadata: make(map[string]string),
		Timings:  make(map[string]time.Duration),
	}

	result := p.ProcessResponse(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatalf("ProcessResponse Action = %v, want Continue", result.Action)
	}

	costStr, ok := rc.Metadata["cost"]
	if !ok {
		t.Fatal("Metadata[cost] not set for embedding endpoint")
	}
	// text-embedding-3-small: 1000 * 0.02/1e6 = 0.000020 (output tokens = 0)
	want := "0.000020"
	if costStr != want {
		t.Fatalf("Metadata[cost] = %q, want %q", costStr, want)
	}
}

func TestProcessResponse_ImageNoCost(t *testing.T) {
	t.Parallel()
	p := testPlugin()
	rc := &models.RequestContext{
		Model:        "gpt-4o",
		EndpointType: "image",
		Metadata:     make(map[string]string),
		Timings:      make(map[string]time.Duration),
	}

	result := p.ProcessResponse(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatalf("ProcessResponse Action = %v, want Continue", result.Action)
	}

	if _, ok := rc.Metadata["cost"]; ok {
		t.Fatal("Metadata[cost] should not be set for image endpoint")
	}
}

func TestProcessResponse_RerankNoCost(t *testing.T) {
	t.Parallel()
	p := testPlugin()
	rc := &models.RequestContext{
		Model:        "gpt-4o",
		EndpointType: "rerank",
		Metadata:     make(map[string]string),
		Timings:      make(map[string]time.Duration),
	}

	result := p.ProcessResponse(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatalf("ProcessResponse Action = %v, want Continue", result.Action)
	}

	if _, ok := rc.Metadata["cost"]; ok {
		t.Fatal("Metadata[cost] should not be set for rerank endpoint")
	}
}

func decisionRC(model, upstream, resolved string, input, output int) *models.RequestContext {
	rc := newRC(model, input, output)
	rc.EndpointType = "decision"
	rc.ResolvedModel = resolved
	rc.DecisionRequest = &models.DecisionRequest{Model: model, UpstreamModel: upstream}
	return rc
}

// Decision calls are priced from the "decision:" keys, never from a chat
// model of the same name.
func TestCostPlugin_Decision(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	for _, tt := range []struct {
		name                      string
		model, upstream, resolved string
		input                     int
		want                      string
	}{
		{"resolved", "typesafe/jev-latest", "jev-latest", "jev-1.13.0", 1_000_000, "0.042000"},
		{"upstream_when_resolved_unknown", "typesafe/jev-latest", "jev-latest", "jev-9.9.9", 1_000_000, "0.042000"},
		{"perplexity", "perplexity-decider/pplx-decider-v1.1-27b", "pplx-decider-v1.1-27b", "pplx-decider-v1.1-27b", 500_000, "0.010000"},
		{"openai_luna", "openai/gpt-6-luna", "gpt-6-luna", "gpt-6-luna", 2_000_000, "0.200000"},
		{"openrouter_slash", "openrouter/typesafe/jev-1.13.0", "typesafe/jev-1.13.0", "", 1_000_000, "0.042000"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			rc := decisionRC(tt.model, tt.upstream, tt.resolved, tt.input, 99)
			p.ProcessResponse(context.Background(), rc)
			if got := rc.Metadata["cost"]; got != tt.want {
				t.Fatalf("cost = %q, want %q", got, tt.want)
			}
		})
	}

	// An unpriced decision model has no cost; it is never priced as chat.
	rc := decisionRC("x/gpt-4o", "gpt-4o", "gpt-4o", 1_000_000, 0)
	p.ProcessResponse(context.Background(), rc)
	if got, ok := rc.Metadata["cost"]; ok {
		t.Fatalf("unpriced decision model got cost %q", got)
	}
}

// gpt-6-luna's decision price does not change what a chat call costs.
func TestCostPlugin_DecisionPriceDoesNotLeakIntoChat(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	if _, ok := db.Get("gpt-6-luna"); ok {
		t.Skip("the bundled chat prices list gpt-6-luna; this test assumes they do not")
	}
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	rc := newRC("gpt-6-luna", 1_000_000, 0)
	p.ProcessResponse(context.Background(), rc)
	if got, ok := rc.Metadata["cost"]; ok {
		t.Fatalf("chat gpt-6-luna priced at the decision price: %q", got)
	}
}

// The org's custom pricing wins over the bundled decision price, under any
// name the call goes by.
func TestCostPlugin_DecisionOrgCustomPricing(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	for _, key := range []string{"jev-1.13.0", "jev-latest", "typesafe/jev-latest"} {
		t.Run(key, func(t *testing.T) {
			store := tenant.NewStore()
			store.Set("org-1", &tenant.OrgConfig{CostTracking: &tenant.CostTrackingConfig{
				Enabled:       true,
				CustomPricing: map[string]*tenant.CustomPricing{key: {InputPerMTok: 1.0}},
			}})
			p := New(true, func() *modeldb.ModelDB { return db }, store, nil)
			rc := decisionRC("typesafe/jev-latest", "jev-latest", "jev-1.13.0", 1_000_000, 0)
			rc.Metadata[tenant.MetadataKeyOrgID] = "org-1"
			p.ProcessResponse(context.Background(), rc)
			if rc.Metadata["cost"] != "1.000000" || rc.Metadata["cost_source"] != "org_custom" {
				t.Fatalf("cost = %q source = %q", rc.Metadata["cost"], rc.Metadata["cost_source"])
			}
		})
	}
}

// ---------------------------------------------------------------------------
// Unpriced models (#3445): usage with no known price must not look free
// ---------------------------------------------------------------------------

func TestProcessResponse_UnknownModel_MarkedUnpriced(t *testing.T) {
	t.Parallel()
	p := testPlugin()
	rc := newRC("glm-5.3-flash", 1000, 500)

	p.ProcessResponse(context.Background(), rc)

	if got := rc.Metadata["cost_source"]; got != CostSourceUnpriced {
		t.Fatalf("Metadata[cost_source] = %q, want %q", got, CostSourceUnpriced)
	}
	if _, ok := rc.Metadata["cost"]; ok {
		t.Fatal("Metadata[cost] must stay unset for an unpriced model, not be recorded as 0")
	}
}

func TestProcessResponse_UnknownModel_EmbeddingMarkedUnpriced(t *testing.T) {
	t.Parallel()
	p := testPlugin()
	rc := &models.RequestContext{
		Model:        "some-custom-embedder",
		EndpointType: "embedding",
		EmbeddingResponse: &models.EmbeddingResponse{
			Usage: &models.EmbeddingUsage{PromptTokens: 800, TotalTokens: 800},
		},
		Metadata: make(map[string]string),
		Timings:  make(map[string]time.Duration),
	}

	p.ProcessResponse(context.Background(), rc)

	if got := rc.Metadata["cost_source"]; got != CostSourceUnpriced {
		t.Fatalf("Metadata[cost_source] = %q, want %q", got, CostSourceUnpriced)
	}
}

func TestProcessResponse_UnknownModel_ZeroTokensNotMarked(t *testing.T) {
	t.Parallel()
	p := testPlugin()
	rc := newRC("glm-5.3-flash", 0, 0)

	p.ProcessResponse(context.Background(), rc)

	if _, ok := rc.Metadata["cost_source"]; ok {
		t.Fatal("a request that consumed no tokens has nothing to price and must not be flagged")
	}
}

func TestProcessResponse_KnownModel_NotMarkedUnpriced(t *testing.T) {
	t.Parallel()
	p := testPlugin()
	rc := newRC("gpt-4o", 1000, 500)

	p.ProcessResponse(context.Background(), rc)

	if got, ok := rc.Metadata["cost_source"]; ok {
		t.Fatalf("priced model got cost_source=%q", got)
	}
}

// Not parallel: it swaps the process-wide slog default to capture output.
func TestProcessResponse_UnknownModel_WarnsOncePerModel(t *testing.T) {
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })

	p := testPlugin()
	for i := 0; i < 5; i++ {
		p.ProcessResponse(context.Background(), newRC("glm-5.3-flash", 1000, 500))
	}
	p.ProcessResponse(context.Background(), newRC("another-unpriced-model", 10, 10))
	p.ProcessResponse(context.Background(), newRC("gpt-4o", 1000, 500)) // priced: no warning

	out := buf.String()
	if n := strings.Count(out, "model=glm-5.3-flash"); n != 1 {
		t.Errorf("warned %d times for glm-5.3-flash, want exactly 1\n%s", n, out)
	}
	if n := strings.Count(out, "model=another-unpriced-model"); n != 1 {
		t.Errorf("warned %d times for another-unpriced-model, want exactly 1\n%s", n, out)
	}
	if strings.Contains(out, "model=gpt-4o") {
		t.Errorf("priced model must not warn\n%s", out)
	}
	if !strings.Contains(out, "model_database.overrides") {
		t.Errorf("warning should tell the operator how to fix it\n%s", out)
	}
}

// Not parallel: it swaps the process-wide slog default to capture output.
func TestProcessResponse_UnknownModel_WarningSetIsBounded(t *testing.T) {
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })

	p := testPlugin()
	for i := 0; i < maxUnpricedWarnings+50; i++ {
		rc := newRC(fmt.Sprintf("unpriced-%d", i), 10, 10)
		p.ProcessResponse(context.Background(), rc)
		// Every request is still flagged; only the log lines are capped.
		if rc.Metadata["cost_source"] != CostSourceUnpriced {
			t.Fatalf("request %d not flagged unpriced", i)
		}
	}

	if got := strings.Count(buf.String(), "no pricing for model"); got != maxUnpricedWarnings {
		t.Fatalf("logged %d warnings, want the cap of %d", got, maxUnpricedWarnings)
	}
}

// ---------------------------------------------------------------------------
// Unpriced audio (#3445): speech is billed in seconds and characters, not
// tokens, so an unpriced speech model must be flagged on those units
// ---------------------------------------------------------------------------

// audioRC is a speech or transcription request carrying its billed amount in
// the metadata key the audio handlers record it under.
func audioRC(model, endpoint, key, amount string) *models.RequestContext {
	rc := &models.RequestContext{
		Model:        model,
		EndpointType: endpoint,
		Metadata:     make(map[string]string),
		Timings:      make(map[string]time.Duration),
	}
	if amount != "" {
		rc.Metadata[key] = amount
	}
	return rc
}

func audioTestPlugin() *Plugin {
	db := modeldb.New(map[string]*modeldb.ModelInfo{
		"whisper-1": {
			ID:       "whisper-1",
			Provider: "openai",
			Pricing:  modeldb.PricingInfo{InputPerSecond: 0.0001},
		},
		"tts-1": {
			ID:       "tts-1",
			Provider: "openai",
			Pricing:  modeldb.PricingInfo{InputPerCharacter: 0.000015},
		},
	}, nil)
	return New(true, func() *modeldb.ModelDB { return db }, nil, nil)
}

func TestProcessResponse_UnpricedAudio_MarkedUnpriced(t *testing.T) {
	t.Parallel()
	cases := []struct{ endpoint, key, amount string }{
		{"transcription", "audio_seconds", "12.500000"},
		{"translation", "audio_seconds", "3.000000"},
		{"transcription_relay", "audio_seconds", "0.250000"},
		{"speech", "input_characters", "120"},
		{"speech_stream", "input_characters", "1"},
		{"speech_relay", "input_characters", "4096"},
	}
	for _, tc := range cases {
		t.Run(tc.endpoint, func(t *testing.T) {
			t.Parallel()
			rc := audioRC("custom-speech-model", tc.endpoint, tc.key, tc.amount)

			audioTestPlugin().ProcessResponse(context.Background(), rc)

			if got := rc.Metadata["cost_source"]; got != CostSourceUnpriced {
				t.Fatalf("Metadata[cost_source] = %q, want %q", got, CostSourceUnpriced)
			}
			if _, ok := rc.Metadata["cost"]; ok {
				t.Fatal("Metadata[cost] must stay unset for an unpriced model")
			}
		})
	}
}

func TestProcessResponse_UnpricedAudio_NothingUsedNotMarked(t *testing.T) {
	t.Parallel()
	cases := []struct{ name, endpoint, key, amount string }{
		{"zero seconds", "transcription", "audio_seconds", "0.000000"},
		{"no duration known", "transcription", "audio_seconds", ""},
		{"zero characters", "speech", "input_characters", "0"},
		{"no character count", "speech_relay", "input_characters", ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			rc := audioRC("custom-speech-model", tc.endpoint, tc.key, tc.amount)

			audioTestPlugin().ProcessResponse(context.Background(), rc)

			if got, ok := rc.Metadata["cost_source"]; ok {
				t.Fatalf("nothing billable was used, but cost_source=%q", got)
			}
		})
	}
}

func TestProcessResponse_PricedAudio_NotMarkedUnpriced(t *testing.T) {
	t.Parallel()
	cases := []struct {
		model, endpoint, key, amount, want string
	}{
		{"whisper-1", "transcription", "audio_seconds", "60.000000", "0.006000"}, // 60 s * $0.0001
		{"tts-1", "speech", "input_characters", "1000", "0.015000"},              // 1000 chars * $0.000015
	}
	for _, tc := range cases {
		t.Run(tc.endpoint, func(t *testing.T) {
			t.Parallel()
			rc := audioRC(tc.model, tc.endpoint, tc.key, tc.amount)

			audioTestPlugin().ProcessResponse(context.Background(), rc)

			if got := rc.Metadata["cost"]; got != tc.want {
				t.Fatalf("Metadata[cost] = %q, want %q", got, tc.want)
			}
			if got, ok := rc.Metadata["cost_source"]; ok {
				t.Fatalf("priced model got cost_source=%q", got)
			}
		})
	}
}

// Not parallel: it swaps the process-wide slog default to capture output.
func TestProcessResponse_UnpricedWarning_HintNamesUnitAndBudgetKey(t *testing.T) {
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })

	p := audioTestPlugin()
	p.ProcessResponse(context.Background(), newRC("unpriced-chat", 1000, 500))
	p.ProcessResponse(context.Background(), audioRC("unpriced-stt", "transcription", "audio_seconds", "9.000000"))
	p.ProcessResponse(context.Background(), audioRC("unpriced-tts", "speech", "input_characters", "42"))

	warning := func(model string) string {
		for _, line := range strings.Split(buf.String(), "\n") {
			if strings.Contains(line, "model="+model+" ") {
				return line
			}
		}
		t.Fatalf("no warning for %s\n%s", model, buf.String())
		return ""
	}
	for model, field := range map[string]string{
		"unpriced-chat": "input_per_token",
		"unpriced-stt":  "input_per_second",
		"unpriced-tts":  "input_per_character",
	} {
		line := warning(model)
		if !strings.Contains(line, "model_database.overrides") || !strings.Contains(line, field) {
			t.Errorf("%s: hint should name model_database.overrides and %s\n%s", model, field, line)
		}
		// Budgets price from their own table, so the cost plugin can neither
		// claim the request escaped them nor leave out the key they read.
		if !strings.Contains(line, "cost_tracking.custom_pricing") {
			t.Errorf("%s: hint should name cost_tracking.custom_pricing, which budgets price from\n%s", model, line)
		}
		if strings.Contains(line, "not counted against budgets") {
			t.Errorf("%s: cost warning must not claim budgets skip the request\n%s", model, line)
		}
	}
}

// An unpriced decision model is marked unpriced (never recorded as free) and
// the warning names the "decision:" override the decision path reads, not the
// chat override it never consults.
func TestCostPlugin_DecisionUnpriced_MarkedWithDecisionHint(t *testing.T) {
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })

	db := modeldb.New(modeldb.BundledModels, nil)
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	rc := decisionRC("selfhosted/decider-x", "decider-x", "decider-x", 1_000, 0)
	p.ProcessResponse(context.Background(), rc)

	if _, ok := rc.Metadata["cost"]; ok {
		t.Fatalf("unpriced decision model got cost %q", rc.Metadata["cost"])
	}
	if got := rc.Metadata["cost_source"]; got != CostSourceUnpriced {
		t.Fatalf("cost_source = %q, want %q", got, CostSourceUnpriced)
	}
	if !strings.Contains(buf.String(), `decision:<model>`) {
		t.Fatalf("warning should name the decision: override key\n%s", buf.String())
	}
}

// A decision model that shares an unpriced chat model's name still warns with
// the decision hint: the chat warning for that name names the wrong key.
func TestCostPlugin_DecisionUnpriced_WarnsAfterChatOfTheSameName(t *testing.T) {
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })

	p := testPlugin()
	p.ProcessResponse(context.Background(), newRC("decider-x", 1_000, 0))
	p.ProcessResponse(context.Background(), decisionRC("selfhosted/decider-x", "decider-x", "decider-x", 1_000, 0))
	p.ProcessResponse(context.Background(), decisionRC("selfhosted/decider-x", "decider-x", "decider-x", 1_000, 0))

	out := buf.String()
	if n := strings.Count(out, "model=decider-x"); n != 2 {
		t.Fatalf("warned %d times for decider-x, want once as chat and once as a decision\n%s", n, out)
	}
	if !strings.Contains(out, `decision:<model>`) {
		t.Fatalf("the decision warning was swallowed by the chat one\n%s", out)
	}
}

// A caller sending the bare upstream name is charged the org price the
// models listing shows under the provider-qualified id.
func TestCostPlugin_DecisionOrgPricingByProviderQualifiedID(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	store := tenant.NewStore()
	store.Set("org-1", &tenant.OrgConfig{CostTracking: &tenant.CostTrackingConfig{
		Enabled:       true,
		CustomPricing: map[string]*tenant.CustomPricing{"typesafe/jev-latest": {InputPerMTok: 2.0}},
	}})
	p := New(true, func() *modeldb.ModelDB { return db }, store, nil)
	rc := decisionRC("jev-latest", "jev-latest", "jev-1.13.0", 1_000_000, 0)
	rc.Provider = "typesafe"
	rc.Metadata[tenant.MetadataKeyOrgID] = "org-1"
	p.ProcessResponse(context.Background(), rc)
	if rc.Metadata["cost"] != "2.000000" || rc.Metadata["cost_source"] != "org_custom" {
		t.Fatalf("cost = %q source = %q", rc.Metadata["cost"], rc.Metadata["cost_source"])
	}
}
