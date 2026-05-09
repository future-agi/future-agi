package modeldb

import (
	"encoding/json"
	"math"
	"sort"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

func ptr[T any](v T) *T { return &v }

// smallBundled returns a minimal bundled map for deterministic unit tests.
func smallBundled() map[string]*ModelInfo {
	return map[string]*ModelInfo{
		"gpt-4o": {
			Provider:        "openai",
			Mode:            ModeChat,
			MaxInputTokens:  128000,
			MaxOutputTokens: 16384,
			Pricing: PricingInfo{
				InputPerToken:       2.5e-6,
				OutputPerToken:      10e-6,
				CachedInputPerToken: 1.25e-6,
			},
			Capabilities: CapabilityFlags{
				FunctionCalling:   true,
				ParallelToolCalls: true,
				Vision:            true,
				Streaming:         true,
				ResponseSchema:    true,
				SystemMessages:    true,
				PromptCaching:     true,
			},
		},
		"gpt-3.5-turbo": {
			Provider:        "openai",
			Mode:            ModeChat,
			MaxInputTokens:  16385,
			MaxOutputTokens: 4096,
			Pricing: PricingInfo{
				InputPerToken:  0.5e-6,
				OutputPerToken: 1.5e-6,
			},
			Capabilities: CapabilityFlags{
				FunctionCalling: true,
				Streaming:       true,
				SystemMessages:  true,
			},
		},
		"text-embedding-3-small": {
			Provider:       "openai",
			Mode:           ModeEmbedding,
			MaxInputTokens: 8191,
			Pricing: PricingInfo{
				InputPerToken: 0.02e-6,
			},
			Capabilities: CapabilityFlags{},
		},
		"claude-3-opus": {
			Provider:        "anthropic",
			Mode:            ModeChat,
			MaxInputTokens:  200000,
			MaxOutputTokens: 4096,
			Pricing: PricingInfo{
				InputPerToken:       15e-6,
				OutputPerToken:      75e-6,
				CachedInputPerToken: 7.5e-6,
			},
			Capabilities: CapabilityFlags{
				FunctionCalling: true,
				Vision:          true,
				Streaming:       true,
				SystemMessages:  true,
			},
		},
	}
}

// ---------------------------------------------------------------------------
// ModelDB Construction and Lookup
// ---------------------------------------------------------------------------

func TestNew_BundledOnly(t *testing.T) {
	db := New(smallBundled(), nil)

	m, ok := db.Get("gpt-4o")
	if !ok {
		t.Fatal("expected to find gpt-4o in bundled-only DB")
	}
	if m.Provider != "openai" {
		t.Errorf("provider = %q, want openai", m.Provider)
	}
	if m.Mode != ModeChat {
		t.Errorf("mode = %q, want chat", m.Mode)
	}
	if m.MaxInputTokens != 128000 {
		t.Errorf("max_input_tokens = %d, want 128000", m.MaxInputTokens)
	}
	if m.ID != "gpt-4o" {
		t.Errorf("ID = %q, want gpt-4o", m.ID)
	}
}

func TestNew_WithOverrides(t *testing.T) {
	overrides := map[string]ModelOverride{
		"gpt-4o": {
			Pricing: &PricingOverride{
				InputPerToken:  ptr(5.0e-6),
				OutputPerToken: ptr(20.0e-6),
			},
		},
	}
	db := New(smallBundled(), overrides)

	m, ok := db.Get("gpt-4o")
	if !ok {
		t.Fatal("expected gpt-4o")
	}
	if m.Pricing.InputPerToken != 5.0e-6 {
		t.Errorf("input_per_token = %g, want 5e-6", m.Pricing.InputPerToken)
	}
	if m.Pricing.OutputPerToken != 20.0e-6 {
		t.Errorf("output_per_token = %g, want 20e-6", m.Pricing.OutputPerToken)
	}
	// Non-overridden fields should survive.
	if m.Provider != "openai" {
		t.Errorf("provider should remain openai, got %q", m.Provider)
	}
	if m.MaxInputTokens != 128000 {
		t.Errorf("max_input_tokens should remain 128000, got %d", m.MaxInputTokens)
	}
	// Cached input should remain from bundled.
	if m.Pricing.CachedInputPerToken != 1.25e-6 {
		t.Errorf("cached_input_per_token = %g, want 1.25e-6", m.Pricing.CachedInputPerToken)
	}
}

func TestNew_NewModelViaOverride(t *testing.T) {
	overrides := map[string]ModelOverride{
		"my-custom-llm": {
			Provider: "custom-corp",
			Mode:     "chat",
			Pricing: &PricingOverride{
				InputPerToken:  ptr(1.0e-6),
				OutputPerToken: ptr(3.0e-6),
			},
			Capabilities: &CapOverride{
				FunctionCalling: ptr(true),
				Vision:          ptr(false),
			},
		},
	}
	db := New(smallBundled(), overrides)

	m, ok := db.Get("my-custom-llm")
	if !ok {
		t.Fatal("expected to find custom model added via override")
	}
	if m.Provider != "custom-corp" {
		t.Errorf("provider = %q, want custom-corp", m.Provider)
	}
	if m.Mode != ModeChat {
		t.Errorf("mode = %q, want chat", m.Mode)
	}
	if m.Pricing.InputPerToken != 1.0e-6 {
		t.Errorf("input_per_token = %g, want 1e-6", m.Pricing.InputPerToken)
	}
	if !m.Capabilities.FunctionCalling {
		t.Error("expected function_calling = true")
	}
	if m.Capabilities.Vision {
		t.Error("expected vision = false")
	}
}

func TestGet_PrefixStripping(t *testing.T) {
	db := New(smallBundled(), nil)

	m, ok := db.Get("openai/gpt-4o")
	if !ok {
		t.Fatal("expected prefix-stripped lookup to find gpt-4o")
	}
	if m.ID != "gpt-4o" {
		t.Errorf("ID = %q, want gpt-4o", m.ID)
	}

	// Also test anthropic prefix.
	m2, ok := db.Get("anthropic/claude-3-opus")
	if !ok {
		t.Fatal("expected prefix-stripped lookup to find claude-3-opus")
	}
	if m2.Provider != "anthropic" {
		t.Errorf("provider = %q, want anthropic", m2.Provider)
	}
}

func TestGet_NotFound(t *testing.T) {
	db := New(smallBundled(), nil)

	m, ok := db.Get("nonexistent-model-xyz")
	if ok {
		t.Errorf("expected not found, got %+v", m)
	}
	if m != nil {
		t.Errorf("expected nil, got %+v", m)
	}
}

func TestList_Sorted(t *testing.T) {
	db := New(smallBundled(), nil)
	list := db.List()

	if len(list) != 4 {
		t.Fatalf("expected 4 models, got %d", len(list))
	}

	for i := 1; i < len(list); i++ {
		if list[i-1].ID >= list[i].ID {
			t.Errorf("list not sorted: [%d]=%q >= [%d]=%q", i-1, list[i-1].ID, i, list[i].ID)
		}
	}
}

func TestListByProvider(t *testing.T) {
	db := New(smallBundled(), nil)

	openai := db.ListByProvider("openai")
	if len(openai) != 3 {
		t.Errorf("expected 3 openai models, got %d", len(openai))
	}
	for _, m := range openai {
		if m.Provider != "openai" {
			t.Errorf("expected provider openai, got %q for %q", m.Provider, m.ID)
		}
	}

	anthropic := db.ListByProvider("anthropic")
	if len(anthropic) != 1 {
		t.Errorf("expected 1 anthropic model, got %d", len(anthropic))
	}

	// Non-existent provider.
	none := db.ListByProvider("does-not-exist")
	if len(none) != 0 {
		t.Errorf("expected 0 models for unknown provider, got %d", len(none))
	}
}

func TestListByMode(t *testing.T) {
	db := New(smallBundled(), nil)

	chatModels := db.ListByMode(ModeChat)
	if len(chatModels) != 3 {
		t.Errorf("expected 3 chat models, got %d", len(chatModels))
	}
	for _, m := range chatModels {
		if m.Mode != ModeChat {
			t.Errorf("expected mode chat, got %q for %q", m.Mode, m.ID)
		}
	}

	embeddingModels := db.ListByMode(ModeEmbedding)
	if len(embeddingModels) != 1 {
		t.Errorf("expected 1 embedding model, got %d", len(embeddingModels))
	}
	if embeddingModels[0].ID != "text-embedding-3-small" {
		t.Errorf("expected text-embedding-3-small, got %q", embeddingModels[0].ID)
	}

	// ListByMode should return sorted results.
	for i := 1; i < len(chatModels); i++ {
		if chatModels[i-1].ID >= chatModels[i].ID {
			t.Errorf("chat models not sorted: %q >= %q", chatModels[i-1].ID, chatModels[i].ID)
		}
	}
}

func TestCount(t *testing.T) {
	db := New(smallBundled(), nil)
	if db.Count() != 4 {
		t.Errorf("count = %d, want 4", db.Count())
	}

	// Adding an override for a new model should increase count.
	overrides := map[string]ModelOverride{
		"new-model": {Provider: "test", Mode: "chat"},
	}
	db2 := New(smallBundled(), overrides)
	if db2.Count() != 5 {
		t.Errorf("count = %d, want 5", db2.Count())
	}
}

// ---------------------------------------------------------------------------
// Pricing
// ---------------------------------------------------------------------------

func TestPricingInfo_Calculate_Standard(t *testing.T) {
	p := PricingInfo{
		InputPerToken:  2.5e-6,
		OutputPerToken: 10e-6,
	}
	// 1000 input tokens, 500 output tokens.
	cost := p.Calculate(1000, 500, CostOptions{})
	expected := 1000*2.5e-6 + 500*10e-6
	if math.Abs(cost-expected) > 1e-12 {
		t.Errorf("cost = %g, want %g", cost, expected)
	}
}

func TestPricingInfo_Calculate_Cached(t *testing.T) {
	p := PricingInfo{
		InputPerToken:       2.5e-6,
		OutputPerToken:      10e-6,
		CachedInputPerToken: 1.25e-6,
	}
	cost := p.Calculate(1000, 500, CostOptions{Cached: true})
	expected := 1000*1.25e-6 + 500*10e-6
	if math.Abs(cost-expected) > 1e-12 {
		t.Errorf("cost = %g, want %g", cost, expected)
	}
}

func TestPricingInfo_Calculate_Cached_FallbackWhenZero(t *testing.T) {
	// When CachedInputPerToken is 0, should fall back to standard rate.
	p := PricingInfo{
		InputPerToken:       2.5e-6,
		OutputPerToken:      10e-6,
		CachedInputPerToken: 0,
	}
	cost := p.Calculate(1000, 500, CostOptions{Cached: true})
	expected := 1000*2.5e-6 + 500*10e-6
	if math.Abs(cost-expected) > 1e-12 {
		t.Errorf("cost = %g, want %g (should use standard rate when cached=0)", cost, expected)
	}
}

func TestPricingInfo_Calculate_Batch(t *testing.T) {
	p := PricingInfo{
		InputPerToken:       2.5e-6,
		OutputPerToken:      10e-6,
		BatchInputPerToken:  1.25e-6,
		BatchOutputPerToken: 5e-6,
	}
	cost := p.Calculate(1000, 500, CostOptions{Batch: true})
	expected := 1000*1.25e-6 + 500*5e-6
	if math.Abs(cost-expected) > 1e-12 {
		t.Errorf("cost = %g, want %g", cost, expected)
	}
}

func TestPricingInfo_Calculate_Batch_PartialOverride(t *testing.T) {
	// Only batch input set, not batch output -- output uses standard rate.
	p := PricingInfo{
		InputPerToken:      2.5e-6,
		OutputPerToken:     10e-6,
		BatchInputPerToken: 1.25e-6,
	}
	cost := p.Calculate(1000, 500, CostOptions{Batch: true})
	expected := 1000*1.25e-6 + 500*10e-6
	if math.Abs(cost-expected) > 1e-12 {
		t.Errorf("cost = %g, want %g", cost, expected)
	}
}

func TestPricingInfo_Calculate_ZeroTokens(t *testing.T) {
	p := PricingInfo{
		InputPerToken:  2.5e-6,
		OutputPerToken: 10e-6,
	}
	cost := p.Calculate(0, 0, CostOptions{})
	if cost != 0 {
		t.Errorf("cost for 0 tokens = %g, want 0", cost)
	}
}

func TestPricingInfo_InputPerMTok(t *testing.T) {
	p := PricingInfo{InputPerToken: 2.5e-6}
	mtok := p.InputPerMTok()
	if math.Abs(mtok-2.5) > 1e-9 {
		t.Errorf("InputPerMTok = %g, want 2.5", mtok)
	}
}

func TestPricingInfo_OutputPerMTok(t *testing.T) {
	p := PricingInfo{OutputPerToken: 10e-6}
	mtok := p.OutputPerMTok()
	if math.Abs(mtok-10.0) > 1e-9 {
		t.Errorf("OutputPerMTok = %g, want 10.0", mtok)
	}
}

func TestPricingInfo_HasPricing(t *testing.T) {
	tests := []struct {
		name string
		p    PricingInfo
		want bool
	}{
		{"both set", PricingInfo{InputPerToken: 1e-6, OutputPerToken: 2e-6}, true},
		{"only input", PricingInfo{InputPerToken: 1e-6}, true},
		{"only output", PricingInfo{OutputPerToken: 2e-6}, true},
		{"zero", PricingInfo{}, false},
		{"only cached", PricingInfo{CachedInputPerToken: 1e-6}, false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := tc.p.HasPricing(); got != tc.want {
				t.Errorf("HasPricing() = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestModelDB_CalculateCost(t *testing.T) {
	db := New(smallBundled(), nil)

	cost, ok := db.CalculateCost("gpt-4o", 1000, 500, CostOptions{})
	if !ok {
		t.Fatal("expected CalculateCost to return ok=true for gpt-4o")
	}
	expected := 1000*2.5e-6 + 500*10e-6
	if math.Abs(cost-expected) > 1e-12 {
		t.Errorf("cost = %g, want %g", cost, expected)
	}

	// Unknown model.
	_, ok = db.CalculateCost("nonexistent", 100, 100, CostOptions{})
	if ok {
		t.Error("expected ok=false for unknown model")
	}

	// Embedding model with zero output pricing.
	cost2, ok2 := db.CalculateCost("text-embedding-3-small", 1000, 0, CostOptions{})
	if !ok2 {
		t.Fatal("expected ok=true for embedding model")
	}
	expected2 := 1000 * 0.02e-6
	if math.Abs(cost2-expected2) > 1e-15 {
		t.Errorf("embedding cost = %g, want %g", cost2, expected2)
	}
}

func TestModelDB_CalculateCost_NoPricing(t *testing.T) {
	bundled := map[string]*ModelInfo{
		"no-price-model": {
			Provider: "test",
			Mode:     ModeChat,
			Pricing:  PricingInfo{}, // zero pricing
		},
	}
	db := New(bundled, nil)

	_, ok := db.CalculateCost("no-price-model", 1000, 500, CostOptions{})
	if ok {
		t.Error("expected ok=false for model with zero InputPerToken")
	}
}

// ---------------------------------------------------------------------------
// Capabilities — ValidateRequest
// ---------------------------------------------------------------------------

func TestCapabilityFlags_ValidateRequest_OK(t *testing.T) {
	caps := CapabilityFlags{
		FunctionCalling: true,
		Vision:          true,
		Streaming:       true,
		SystemMessages:  true,
	}
	req := &models.ChatCompletionRequest{
		Model: "gpt-4o",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"hello"`)},
		},
	}
	reason, ok := caps.ValidateRequest(req)
	if !ok {
		t.Errorf("expected OK, got blocked: %s", reason)
	}
}

func TestCapabilityFlags_ValidateRequest_ToolsBlocked(t *testing.T) {
	caps := CapabilityFlags{
		FunctionCalling: false,
		Streaming:       true,
	}
	req := &models.ChatCompletionRequest{
		Model: "no-tools-model",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"hello"`)},
		},
		Tools: []models.Tool{
			{
				Type: "function",
				Function: models.ToolFunction{
					Name:        "get_weather",
					Description: "Get weather",
				},
			},
		},
	}
	reason, ok := caps.ValidateRequest(req)
	if ok {
		t.Fatal("expected tools to be blocked")
	}
	if reason == "" {
		t.Error("expected a non-empty reason")
	}
}

func TestCapabilityFlags_ValidateRequest_VisionBlocked(t *testing.T) {
	caps := CapabilityFlags{
		FunctionCalling: true,
		Vision:          false,
		Streaming:       true,
	}
	req := &models.ChatCompletionRequest{
		Model: "no-vision-model",
		Messages: []models.Message{
			{
				Role:    "user",
				Content: json.RawMessage(`[{"type":"image_url","image_url":{"url":"data:image/png;base64,abc"}}]`),
			},
		},
	}
	reason, ok := caps.ValidateRequest(req)
	if ok {
		t.Fatal("expected vision to be blocked")
	}
	if reason == "" {
		t.Error("expected a non-empty reason")
	}
}

func TestCapabilityFlags_ValidateRequest_VisionBlocked_ImageType(t *testing.T) {
	// Test with "image" type (alternative to "image_url").
	caps := CapabilityFlags{Vision: false}
	req := &models.ChatCompletionRequest{
		Model: "no-vision",
		Messages: []models.Message{
			{
				Role:    "user",
				Content: json.RawMessage(`[{"type":"image","source":{"type":"base64","data":"abc"}}]`),
			},
		},
	}
	reason, ok := caps.ValidateRequest(req)
	if ok {
		t.Fatal("expected image type to be blocked on non-vision model")
	}
	if reason == "" {
		t.Error("expected a non-empty reason")
	}
}

func TestCapabilityFlags_ValidateRequest_SchemaBlocked(t *testing.T) {
	caps := CapabilityFlags{
		FunctionCalling: true,
		ResponseSchema:  false,
	}
	req := &models.ChatCompletionRequest{
		Model: "no-schema-model",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"hello"`)},
		},
		ResponseFormat: &models.ResponseFormat{
			Type: "json_schema",
		},
	}
	reason, ok := caps.ValidateRequest(req)
	if ok {
		t.Fatal("expected json_schema to be blocked")
	}
	if reason == "" {
		t.Error("expected a non-empty reason")
	}
}

func TestCapabilityFlags_ValidateRequest_JsonObjectAllowed(t *testing.T) {
	// json_object should NOT be blocked even without ResponseSchema.
	caps := CapabilityFlags{ResponseSchema: false}
	req := &models.ChatCompletionRequest{
		Model: "basic-model",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"hello"`)},
		},
		ResponseFormat: &models.ResponseFormat{
			Type: "json_object",
		},
	}
	reason, ok := caps.ValidateRequest(req)
	if !ok {
		t.Errorf("json_object should not be blocked, got: %s", reason)
	}
}

func TestCapabilityFlags_ValidateRequest_AllSupported(t *testing.T) {
	caps := CapabilityFlags{
		FunctionCalling:   true,
		ParallelToolCalls: true,
		Vision:            true,
		AudioInput:        true,
		AudioOutput:       true,
		PDFInput:          true,
		Streaming:         true,
		ResponseSchema:    true,
		SystemMessages:    true,
		PromptCaching:     true,
		Reasoning:         true,
	}
	req := &models.ChatCompletionRequest{
		Model: "all-caps-model",
		Messages: []models.Message{
			{
				Role:    "user",
				Content: json.RawMessage(`[{"type":"text","text":"describe this"},{"type":"image_url","image_url":{"url":"data:image/png;base64,abc"}}]`),
			},
		},
		Tools: []models.Tool{
			{
				Type: "function",
				Function: models.ToolFunction{
					Name: "do_something",
				},
			},
		},
		ResponseFormat: &models.ResponseFormat{
			Type: "json_schema",
		},
	}
	reason, ok := caps.ValidateRequest(req)
	if !ok {
		t.Errorf("expected all-caps model to pass everything, got blocked: %s", reason)
	}
}

func TestCapabilityFlags_ValidateRequest_EmptyRequest(t *testing.T) {
	// An empty capabilities model should pass an empty (minimal) request.
	caps := CapabilityFlags{}
	req := &models.ChatCompletionRequest{
		Model: "basic",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"hi"`)},
		},
	}
	reason, ok := caps.ValidateRequest(req)
	if !ok {
		t.Errorf("minimal request should pass on zero caps, got: %s", reason)
	}
}

func TestCapabilityFlags_ValidateRequest_NilContent(t *testing.T) {
	// Messages with nil Content should not cause vision false positive.
	caps := CapabilityFlags{Vision: false}
	req := &models.ChatCompletionRequest{
		Model: "basic",
		Messages: []models.Message{
			{Role: "assistant", Content: nil},
			{Role: "user", Content: json.RawMessage(`"hello"`)},
		},
	}
	reason, ok := caps.ValidateRequest(req)
	if !ok {
		t.Errorf("nil content should not trigger vision check, got: %s", reason)
	}
}

// ---------------------------------------------------------------------------
// Bundled Data Integrity
// ---------------------------------------------------------------------------

func TestBundledModels_NoDuplicates(t *testing.T) {
	// BundledModels is a map, so duplicates are impossible.
	// Verify the map is non-empty.
	if len(BundledModels) == 0 {
		t.Fatal("BundledModels is empty")
	}

	db := New(BundledModels, nil)
	if db.Count() == 0 {
		t.Fatal("ModelDB from BundledModels has count 0")
	}
	if db.Count() < 500 {
		t.Errorf("expected 500+ bundled models (litellm), got %d", db.Count())
	}
	if db.Count() != len(BundledModels) {
		t.Errorf("count mismatch: db=%d, bundled=%d", db.Count(), len(BundledModels))
	}
}

func TestBundledModels_RequiredFields(t *testing.T) {
	for id, m := range BundledModels {
		if m.Provider == "" {
			t.Errorf("model %q has empty Provider", id)
		}
		if m.Mode == "" {
			t.Errorf("model %q has empty Mode", id)
		}
	}
}

func TestBundledModels_ChatModelsHavePricing(t *testing.T) {
	for id, m := range BundledModels {
		if m.Mode == ModeChat {
			if m.Pricing.InputPerToken <= 0 {
				t.Errorf("chat model %q has InputPerToken = %g (expected > 0)", id, m.Pricing.InputPerToken)
			}
		}
	}
}

func TestBundledModels_NoNegativePrices(t *testing.T) {
	for id, m := range BundledModels {
		p := m.Pricing
		if p.InputPerToken < 0 {
			t.Errorf("model %q has negative InputPerToken: %g", id, p.InputPerToken)
		}
		if p.OutputPerToken < 0 {
			t.Errorf("model %q has negative OutputPerToken: %g", id, p.OutputPerToken)
		}
		if p.CachedInputPerToken < 0 {
			t.Errorf("model %q has negative CachedInputPerToken: %g", id, p.CachedInputPerToken)
		}
		if p.BatchInputPerToken < 0 {
			t.Errorf("model %q has negative BatchInputPerToken: %g", id, p.BatchInputPerToken)
		}
		if p.BatchOutputPerToken < 0 {
			t.Errorf("model %q has negative BatchOutputPerToken: %g", id, p.BatchOutputPerToken)
		}
		if p.InputPerImage < 0 {
			t.Errorf("model %q has negative InputPerImage: %g", id, p.InputPerImage)
		}
		if p.OutputPerImage < 0 {
			t.Errorf("model %q has negative OutputPerImage: %g", id, p.OutputPerImage)
		}
	}
}

func TestBundledModels_KnownModelExists(t *testing.T) {
	// Spot-check that well-known models are present.
	db := New(BundledModels, nil)
	known := []string{"gpt-4o", "claude-3-5-sonnet-20241022"}
	for _, id := range known {
		if _, ok := db.Get(id); !ok {
			t.Errorf("expected well-known model %q to exist in BundledModels", id)
		}
	}
}

func TestBundledModels_ListSorted(t *testing.T) {
	db := New(BundledModels, nil)
	list := db.List()
	if !sort.SliceIsSorted(list, func(i, j int) bool { return list[i].ID < list[j].ID }) {
		t.Error("List() from BundledModels is not sorted by ID")
	}
}

// ---------------------------------------------------------------------------
// Override Merge
// ---------------------------------------------------------------------------

func TestMergeOverride_ProviderOnly(t *testing.T) {
	overrides := map[string]ModelOverride{
		"gpt-4o": {Provider: "azure-openai"},
	}
	db := New(smallBundled(), overrides)

	m, ok := db.Get("gpt-4o")
	if !ok {
		t.Fatal("expected gpt-4o")
	}
	if m.Provider != "azure-openai" {
		t.Errorf("provider = %q, want azure-openai", m.Provider)
	}
	// Everything else should remain unchanged.
	if m.Mode != ModeChat {
		t.Errorf("mode = %q, want chat", m.Mode)
	}
	if m.MaxInputTokens != 128000 {
		t.Errorf("max_input_tokens = %d, want 128000", m.MaxInputTokens)
	}
	if m.Pricing.InputPerToken != 2.5e-6 {
		t.Errorf("pricing should be unchanged, got input_per_token=%g", m.Pricing.InputPerToken)
	}
	if !m.Capabilities.FunctionCalling {
		t.Error("function_calling should remain true")
	}
}

func TestMergeOverride_PricingPartial(t *testing.T) {
	overrides := map[string]ModelOverride{
		"gpt-4o": {
			Pricing: &PricingOverride{
				InputPerToken: ptr(99.0e-6),
				// OutputPerToken left nil -- should keep original.
			},
		},
	}
	db := New(smallBundled(), overrides)
	m, _ := db.Get("gpt-4o")

	if m.Pricing.InputPerToken != 99.0e-6 {
		t.Errorf("input_per_token = %g, want 99e-6", m.Pricing.InputPerToken)
	}
	if m.Pricing.OutputPerToken != 10e-6 {
		t.Errorf("output_per_token = %g, want 10e-6 (unchanged)", m.Pricing.OutputPerToken)
	}
	if m.Pricing.CachedInputPerToken != 1.25e-6 {
		t.Errorf("cached_input_per_token = %g, want 1.25e-6 (unchanged)", m.Pricing.CachedInputPerToken)
	}
}

func TestMergeOverride_CapabilitiesPartial(t *testing.T) {
	overrides := map[string]ModelOverride{
		"gpt-4o": {
			Capabilities: &CapOverride{
				Vision: ptr(false),
				// FunctionCalling left nil -- should remain true.
			},
		},
	}
	db := New(smallBundled(), overrides)
	m, _ := db.Get("gpt-4o")

	if m.Capabilities.Vision {
		t.Error("vision should be overridden to false")
	}
	if !m.Capabilities.FunctionCalling {
		t.Error("function_calling should remain true (not overridden)")
	}
	if !m.Capabilities.Streaming {
		t.Error("streaming should remain true (not overridden)")
	}
	if !m.Capabilities.ResponseSchema {
		t.Error("response_schema should remain true (not overridden)")
	}
}

func TestMergeOverride_MaxTokens(t *testing.T) {
	overrides := map[string]ModelOverride{
		"gpt-4o": {
			MaxInputTokens:  ptr(256000),
			MaxOutputTokens: ptr(32768),
		},
	}
	db := New(smallBundled(), overrides)
	m, _ := db.Get("gpt-4o")

	if m.MaxInputTokens != 256000 {
		t.Errorf("max_input_tokens = %d, want 256000", m.MaxInputTokens)
	}
	if m.MaxOutputTokens != 32768 {
		t.Errorf("max_output_tokens = %d, want 32768", m.MaxOutputTokens)
	}
}

func TestMergeOverride_Regions(t *testing.T) {
	overrides := map[string]ModelOverride{
		"gpt-4o": {
			Regions: []string{"us-east-1", "eu-west-1"},
		},
	}
	db := New(smallBundled(), overrides)
	m, _ := db.Get("gpt-4o")

	if len(m.Regions) != 2 {
		t.Fatalf("regions len = %d, want 2", len(m.Regions))
	}
	if m.Regions[0] != "us-east-1" || m.Regions[1] != "eu-west-1" {
		t.Errorf("regions = %v, want [us-east-1 eu-west-1]", m.Regions)
	}
}

func TestMergeOverride_DeprecationDate(t *testing.T) {
	overrides := map[string]ModelOverride{
		"gpt-4o": {DeprecationDate: "2025-12-31"},
	}
	db := New(smallBundled(), overrides)
	m, _ := db.Get("gpt-4o")

	if m.DeprecationDate != "2025-12-31" {
		t.Errorf("deprecation_date = %q, want 2025-12-31", m.DeprecationDate)
	}
}

// ---------------------------------------------------------------------------
// Deep copy / immutability
// ---------------------------------------------------------------------------

func TestNew_DeepCopy(t *testing.T) {
	bundled := smallBundled()
	db := New(bundled, nil)

	// Mutate the original bundled map -- DB should not be affected.
	bundled["gpt-4o"].Provider = "MUTATED"

	m, _ := db.Get("gpt-4o")
	if m.Provider == "MUTATED" {
		t.Error("ModelDB was not deep-copied; mutation of original leaked through")
	}
}

func TestNew_RegionsDeepCopy(t *testing.T) {
	bundled := map[string]*ModelInfo{
		"test-model": {
			Provider: "test",
			Mode:     ModeChat,
			Regions:  []string{"us-east-1", "eu-west-1"},
			Pricing:  PricingInfo{InputPerToken: 1e-6, OutputPerToken: 2e-6},
		},
	}
	db := New(bundled, nil)

	// Mutate original regions.
	bundled["test-model"].Regions[0] = "MUTATED"

	m, _ := db.Get("test-model")
	if m.Regions[0] == "MUTATED" {
		t.Error("Regions slice was not deep-copied")
	}
}

// ---------------------------------------------------------------------------
// GetPricing / GetCapabilities / GetLimits helpers
// ---------------------------------------------------------------------------

func TestGetPricing(t *testing.T) {
	db := New(smallBundled(), nil)

	p, ok := db.GetPricing("gpt-4o")
	if !ok {
		t.Fatal("expected GetPricing to find gpt-4o")
	}
	if p.InputPerToken != 2.5e-6 {
		t.Errorf("InputPerToken = %g, want 2.5e-6", p.InputPerToken)
	}

	_, ok = db.GetPricing("nonexistent")
	if ok {
		t.Error("expected GetPricing to return false for unknown model")
	}
}

func TestGetCapabilities(t *testing.T) {
	db := New(smallBundled(), nil)

	c, ok := db.GetCapabilities("gpt-4o")
	if !ok {
		t.Fatal("expected GetCapabilities to find gpt-4o")
	}
	if !c.FunctionCalling {
		t.Error("expected FunctionCalling = true")
	}
	if !c.Vision {
		t.Error("expected Vision = true")
	}

	_, ok = db.GetCapabilities("nonexistent")
	if ok {
		t.Error("expected GetCapabilities to return false for unknown model")
	}
}

func TestGetLimits(t *testing.T) {
	db := New(smallBundled(), nil)

	maxIn, maxOut, ok := db.GetLimits("gpt-4o")
	if !ok {
		t.Fatal("expected GetLimits to find gpt-4o")
	}
	if maxIn != 128000 {
		t.Errorf("maxInput = %d, want 128000", maxIn)
	}
	if maxOut != 16384 {
		t.Errorf("maxOutput = %d, want 16384", maxOut)
	}

	_, _, ok = db.GetLimits("nonexistent")
	if ok {
		t.Error("expected GetLimits to return false for unknown model")
	}
}

// ---------------------------------------------------------------------------
// ListByProvider sorted
// ---------------------------------------------------------------------------

func TestListByProvider_Sorted(t *testing.T) {
	db := New(smallBundled(), nil)

	openai := db.ListByProvider("openai")
	if !sort.SliceIsSorted(openai, func(i, j int) bool { return openai[i].ID < openai[j].ID }) {
		ids := make([]string, len(openai))
		for i, m := range openai {
			ids[i] = m.ID
		}
		t.Errorf("ListByProvider(openai) not sorted: %v", ids)
	}
}
