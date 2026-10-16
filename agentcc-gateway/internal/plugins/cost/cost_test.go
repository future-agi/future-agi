package cost

import (
	"context"
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
