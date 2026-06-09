package validation

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

// buildTestDB creates a ModelDB with a few models for testing:
//   - "test-model":        chat, function_calling=true, vision=false, response_schema=false,
//     max_input=4096, max_output=2048, has pricing
//   - "test-vision-model": chat, vision=true, function_calling=true, response_schema=true,
//     max_input=128000
//   - "deprecated-model":  chat, deprecation_date="2025-12-31", max_input=8192
func buildTestDB() *modeldb.ModelDB {
	bundled := map[string]*modeldb.ModelInfo{
		"test-model": {
			ID:              "test-model",
			Provider:        "openai",
			Mode:            modeldb.ModeChat,
			MaxInputTokens:  4096,
			MaxOutputTokens: 2048,
			Pricing: modeldb.PricingInfo{
				InputPerToken:  0.000003,
				OutputPerToken: 0.000006,
			},
			Capabilities: modeldb.CapabilityFlags{
				FunctionCalling: true,
				Vision:          false,
				ResponseSchema:  false,
				Streaming:       true,
				SystemMessages:  true,
			},
		},
		"test-vision-model": {
			ID:              "test-vision-model",
			Provider:        "openai",
			Mode:            modeldb.ModeChat,
			MaxInputTokens:  128000,
			MaxOutputTokens: 4096,
			Capabilities: modeldb.CapabilityFlags{
				FunctionCalling: true,
				Vision:          true,
				ResponseSchema:  true,
				Streaming:       true,
				SystemMessages:  true,
			},
		},
		"deprecated-model": {
			ID:              "deprecated-model",
			Provider:        "openai",
			Mode:            modeldb.ModeChat,
			MaxInputTokens:  8192,
			MaxOutputTokens: 4096,
			DeprecationDate: "2025-12-31",
			Capabilities: modeldb.CapabilityFlags{
				FunctionCalling: true,
				Vision:          false,
				ResponseSchema:  false,
				Streaming:       true,
				SystemMessages:  true,
			},
		},
	}
	return modeldb.New(bundled, nil)
}

// newRC creates a minimal RequestContext for testing.
func newRC(model string, req *models.ChatCompletionRequest) *models.RequestContext {
	return &models.RequestContext{
		RequestID: "test-req-001",
		Model:     model,
		Request:   req,
		Metadata:  make(map[string]string),
		Timings:   make(map[string]time.Duration),
	}
}

// simpleRequest creates a ChatCompletionRequest with a single user text message.
func simpleRequest() *models.ChatCompletionRequest {
	return &models.ChatCompletionRequest{
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"hello"`)},
		},
	}
}

// ---------------------------------------------------------------------------
// Plugin basics
// ---------------------------------------------------------------------------

func TestPlugin_Name(t *testing.T) {
	p := New(true, func() *modeldb.ModelDB { return nil })
	if got := p.Name(); got != "validation" {
		t.Errorf("Name() = %q, want %q", got, "validation")
	}
}

func TestPlugin_Priority(t *testing.T) {
	p := New(true, func() *modeldb.ModelDB { return nil })
	if got := p.Priority(); got != 70 {
		t.Errorf("Priority() = %d, want %d", got, 70)
	}
}

func TestPlugin_ProcessResponse_Noop(t *testing.T) {
	db := buildTestDB()
	p := New(true, func() *modeldb.ModelDB { return db })

	rc := newRC("test-model", simpleRequest())
	result := p.ProcessResponse(context.Background(), rc)

	if result.Action != pipeline.Continue {
		t.Errorf("ProcessResponse action = %v, want Continue", result.Action)
	}
	if result.Error != nil {
		t.Errorf("ProcessResponse error = %v, want nil", result.Error)
	}
}

// ---------------------------------------------------------------------------
// Request validation
// ---------------------------------------------------------------------------

func TestProcessRequest_UnknownModel(t *testing.T) {
	db := buildTestDB()
	p := New(true, func() *modeldb.ModelDB { return db })

	rc := newRC("nonexistent-model", simpleRequest())
	result := p.ProcessRequest(context.Background(), rc)

	if result.Action != pipeline.Continue {
		t.Errorf("action = %v, want Continue for unknown model", result.Action)
	}
	if result.Error != nil {
		t.Errorf("error = %v, want nil for unknown model", result.Error)
	}
}

func TestProcessRequest_ToolsBlocked(t *testing.T) {
	// Build a custom DB with a non-function-calling model.
	noToolsDB := modeldb.New(map[string]*modeldb.ModelInfo{
		"no-tools-model": {
			ID:              "no-tools-model",
			Provider:        "openai",
			Mode:            modeldb.ModeChat,
			MaxInputTokens:  4096,
			MaxOutputTokens: 2048,
			Capabilities: modeldb.CapabilityFlags{
				FunctionCalling: false,
				Vision:          false,
				ResponseSchema:  false,
				Streaming:       true,
			},
		},
	}, nil)
	pNoTools := New(true, func() *modeldb.ModelDB { return noToolsDB })

	req := &models.ChatCompletionRequest{
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"use the tool"`)},
		},
		Tools: []models.Tool{
			{
				Type: "function",
				Function: models.ToolFunction{
					Name:        "test",
					Description: "test",
				},
			},
		},
	}
	rc := newRC("no-tools-model", req)
	result := pNoTools.ProcessRequest(context.Background(), rc)

	if result.Error == nil {
		t.Fatal("expected error for tools on non-function-calling model, got nil")
	}
	if result.Error.Status != http.StatusBadRequest {
		t.Errorf("error status = %d, want %d", result.Error.Status, http.StatusBadRequest)
	}
	if result.Error.Code != "model_capability_error" {
		t.Errorf("error code = %q, want %q", result.Error.Code, "model_capability_error")
	}
	if !strings.Contains(result.Error.Message, "function calling") {
		t.Errorf("error message = %q, want it to mention 'function calling'", result.Error.Message)
	}
}

func TestProcessRequest_VisionBlocked(t *testing.T) {
	db := buildTestDB()
	p := New(true, func() *modeldb.ModelDB { return db })

	// "test-model" has vision=false, so sending image content should be blocked.
	req := &models.ChatCompletionRequest{
		Messages: []models.Message{
			{
				Role:    "user",
				Content: json.RawMessage(`[{"type":"image_url","image_url":{"url":"https://example.com/image.png"}}]`),
			},
		},
	}
	rc := newRC("test-model", req)
	result := p.ProcessRequest(context.Background(), rc)

	if result.Error == nil {
		t.Fatal("expected error for image content on non-vision model, got nil")
	}
	if result.Error.Status != http.StatusBadRequest {
		t.Errorf("error status = %d, want %d", result.Error.Status, http.StatusBadRequest)
	}
	if result.Error.Code != "model_capability_error" {
		t.Errorf("error code = %q, want %q", result.Error.Code, "model_capability_error")
	}
	if !strings.Contains(result.Error.Message, "vision") {
		t.Errorf("error message = %q, want it to mention 'vision'", result.Error.Message)
	}
}

func TestProcessRequest_SchemaBlocked(t *testing.T) {
	db := buildTestDB()
	p := New(true, func() *modeldb.ModelDB { return db })

	// "test-model" has response_schema=false, so json_schema should be blocked.
	req := &models.ChatCompletionRequest{
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"return structured"`)},
		},
		ResponseFormat: &models.ResponseFormat{Type: "json_schema"},
	}
	rc := newRC("test-model", req)
	result := p.ProcessRequest(context.Background(), rc)

	if result.Error == nil {
		t.Fatal("expected error for json_schema on non-schema model, got nil")
	}
	if result.Error.Status != http.StatusBadRequest {
		t.Errorf("error status = %d, want %d", result.Error.Status, http.StatusBadRequest)
	}
	if result.Error.Code != "model_capability_error" {
		t.Errorf("error code = %q, want %q", result.Error.Code, "model_capability_error")
	}
	if !strings.Contains(result.Error.Message, "json_schema") {
		t.Errorf("error message = %q, want it to mention 'json_schema'", result.Error.Message)
	}
}

func TestProcessRequest_ValidRequest(t *testing.T) {
	db := buildTestDB()
	p := New(true, func() *modeldb.ModelDB { return db })

	// "test-vision-model" supports everything; send a plain text message.
	rc := newRC("test-vision-model", simpleRequest())
	result := p.ProcessRequest(context.Background(), rc)

	if result.Action != pipeline.Continue {
		t.Errorf("action = %v, want Continue for valid request", result.Action)
	}
	if result.Error != nil {
		t.Errorf("error = %v, want nil for valid request", result.Error)
	}
}

func TestProcessRequest_Disabled(t *testing.T) {
	db := buildTestDB()
	p := New(false, func() *modeldb.ModelDB { return db })

	// Even with image content on a non-vision model, disabled plugin should pass through.
	req := &models.ChatCompletionRequest{
		Messages: []models.Message{
			{
				Role:    "user",
				Content: json.RawMessage(`[{"type":"image_url","image_url":{"url":"https://example.com/image.png"}}]`),
			},
		},
	}
	rc := newRC("test-model", req)
	result := p.ProcessRequest(context.Background(), rc)

	if result.Action != pipeline.Continue {
		t.Errorf("action = %v, want Continue when plugin is disabled", result.Action)
	}
	if result.Error != nil {
		t.Errorf("error = %v, want nil when plugin is disabled", result.Error)
	}
}

// ---------------------------------------------------------------------------
// Deprecation warning
// ---------------------------------------------------------------------------

func TestProcessRequest_DeprecatedModel(t *testing.T) {
	db := buildTestDB()
	p := New(true, func() *modeldb.ModelDB { return db })

	rc := newRC("deprecated-model", simpleRequest())
	result := p.ProcessRequest(context.Background(), rc)

	// Should continue (not error), but set deprecation metadata.
	if result.Action != pipeline.Continue {
		t.Errorf("action = %v, want Continue for deprecated model", result.Action)
	}
	if result.Error != nil {
		t.Errorf("error = %v, want nil for deprecated model", result.Error)
	}

	deprecation, ok := rc.Metadata["model_deprecated"]
	if !ok {
		t.Fatal("expected metadata key 'model_deprecated' to be set")
	}
	if deprecation != "2025-12-31" {
		t.Errorf("model_deprecated = %q, want %q", deprecation, "2025-12-31")
	}
}

// ---------------------------------------------------------------------------
// Token estimation
// ---------------------------------------------------------------------------

func TestProcessRequest_InputTooLarge(t *testing.T) {
	db := buildTestDB()
	p := New(true, func() *modeldb.ModelDB { return db })

	// "test-model" has max_input=4096. At ~4 chars/token, we need content that
	// estimates to > 4096 * 1.5 = 6144 tokens. 100_000 chars ≈ 25_000 tokens.
	largeContent := `"` + strings.Repeat("a", 100000) + `"`
	req := &models.ChatCompletionRequest{
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(largeContent)},
		},
	}
	rc := newRC("test-model", req)
	result := p.ProcessRequest(context.Background(), rc)

	if result.Error == nil {
		t.Fatal("expected error for input exceeding 1.5x token limit, got nil")
	}
	if result.Error.Status != http.StatusRequestEntityTooLarge {
		t.Errorf("error status = %d, want %d", result.Error.Status, http.StatusRequestEntityTooLarge)
	}
	if result.Error.Code != "input_too_large" {
		t.Errorf("error code = %q, want %q", result.Error.Code, "input_too_large")
	}
	if !strings.Contains(result.Error.Message, "exceed") {
		t.Errorf("error message = %q, want it to mention 'exceed'", result.Error.Message)
	}
}

func TestProcessRequest_InputApproachingLimit(t *testing.T) {
	db := buildTestDB()
	p := New(true, func() *modeldb.ModelDB { return db })

	// "test-model" has max_input=4096. 95% threshold = 3891 tokens.
	// 1.5x threshold = 6144 tokens. We need estimated tokens between 3891 and 6144.
	// At 4 chars per token, we need ~3900*4 = 15600 chars of content.
	// But we also have the JSON quotes and role overhead, so aim for ~15600 chars in Content.
	// estimateTokens counts len(Content) + len(Role) + 4, divided by 4.
	// Content = `"` + Nchars + `"` => len = N + 2 in raw JSON.
	// Total bytes = (N+2) + len("user") + 4 = N + 10
	// Tokens = (N + 10) / 4
	// Need tokens > 4096*0.95 = 3891.2 => N > 3891.2*4 - 10 = 15554.8 => N >= 15555
	// Need tokens < 4096*1.5  = 6144   => N < 6144*4 - 10 = 24566
	// Use N = 16000 => tokens = (16000+10)/4 = 4002.5 ≈ 4002 (int division)
	// 4002 > 3891 (95% of 4096) => warning.  4002 < 6144 (1.5x) => no error.
	content := `"` + strings.Repeat("x", 16000) + `"`
	req := &models.ChatCompletionRequest{
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(content)},
		},
	}
	rc := newRC("test-model", req)
	result := p.ProcessRequest(context.Background(), rc)

	// Should continue (no error).
	if result.Error != nil {
		t.Fatalf("expected no error for approaching limit, got: %v", result.Error)
	}
	if result.Action != pipeline.Continue {
		t.Errorf("action = %v, want Continue", result.Action)
	}

	// Should set warning metadata.
	warning, ok := rc.Metadata["input_token_warning"]
	if !ok {
		t.Fatal("expected metadata key 'input_token_warning' to be set")
	}
	if !strings.Contains(warning, "approaching") {
		t.Errorf("input_token_warning = %q, want it to mention 'approaching'", warning)
	}
}

// ---------------------------------------------------------------------------
// Model limits metadata
// ---------------------------------------------------------------------------

func TestProcessRequest_StoresMaxOutputTokens(t *testing.T) {
	db := buildTestDB()
	p := New(true, func() *modeldb.ModelDB { return db })

	rc := newRC("test-model", simpleRequest())
	result := p.ProcessRequest(context.Background(), rc)

	if result.Error != nil {
		t.Fatalf("unexpected error: %v", result.Error)
	}

	maxOutput, ok := rc.Metadata["model_max_output_tokens"]
	if !ok {
		t.Fatal("expected metadata key 'model_max_output_tokens' to be set")
	}
	if maxOutput != "2048" {
		t.Errorf("model_max_output_tokens = %q, want %q", maxOutput, "2048")
	}
}

// ---------------------------------------------------------------------------
// Nil ModelDB
// ---------------------------------------------------------------------------

func TestProcessRequest_NilModelDB(t *testing.T) {
	p := New(true, func() *modeldb.ModelDB { return nil })

	rc := newRC("test-model", simpleRequest())
	result := p.ProcessRequest(context.Background(), rc)

	if result.Action != pipeline.Continue {
		t.Errorf("action = %v, want Continue when ModelDB is nil", result.Action)
	}
	if result.Error != nil {
		t.Errorf("error = %v, want nil when ModelDB is nil", result.Error)
	}
}

// ---------------------------------------------------------------------------
// Multimodal token estimation
//
// Regression test for the base64-inflates-token-count bug: when a message
// content is a structured array with media blocks (image_url / input_audio /
// file), the base64 payload bytes must NOT be counted as if they were text,
// otherwise a small audio/pdf blows past ``max_input_tokens * 1.5`` and the
// request is wrongly rejected with ``input_too_large`` before the provider
// ever sees it.
// ---------------------------------------------------------------------------

func TestEstimateContentBytes_PlainString(t *testing.T) {
	// Plain JSON string → len is just the string length.
	content := json.RawMessage(`"hello world"`)
	got := estimateContentBytes(content)
	want := len("hello world")
	if got != want {
		t.Errorf("estimateContentBytes(plain string) = %d, want %d", got, want)
	}
}

func TestEstimateContentBytes_ImageBlockIsBudgeted(t *testing.T) {
	// A ~6MB base64 image payload would be counted as ~1.5M tokens under the
	// raw-bytes approach. Per-type budget should return exactly the image
	// constant, ignoring the giant base64 payload.
	bigPayload := strings.Repeat("A", 6_000_000)
	content := json.RawMessage(`[{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,` + bigPayload + `"}}]`)
	got := estimateContentBytes(content)
	if got != _multimodalImageBudget {
		t.Errorf("image_url block budget = %d, want %d (should not count raw base64 bytes)", got, _multimodalImageBudget)
	}
}

func TestEstimateContentBytes_AudioBlockIsBudgeted(t *testing.T) {
	bigAudio := strings.Repeat("B", 6_000_000)
	content := json.RawMessage(`[{"type":"input_audio","input_audio":{"data":"` + bigAudio + `","format":"wav"}}]`)
	got := estimateContentBytes(content)
	if got != _multimodalAudioBudget {
		t.Errorf("input_audio block budget = %d, want %d", got, _multimodalAudioBudget)
	}
}

func TestEstimateContentBytes_FileBlockIsBudgeted(t *testing.T) {
	bigPdf := strings.Repeat("C", 6_000_000)
	content := json.RawMessage(`[{"type":"file","file":{"file_data":"` + bigPdf + `"}}]`)
	got := estimateContentBytes(content)
	if got != _multimodalFileBudget {
		t.Errorf("file block budget = %d, want %d", got, _multimodalFileBudget)
	}
}

func TestEstimateContentBytes_MixedTextAndMedia(t *testing.T) {
	// Text counts normally; media parts get their fixed budget.
	msg := strings.Repeat("x", 100)
	content := json.RawMessage(`[{"type":"text","text":"` + msg + `"},{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,YYYY"}}]`)
	got := estimateContentBytes(content)
	want := 100 + _multimodalImageBudget
	if got != want {
		t.Errorf("mixed content estimate = %d, want %d", got, want)
	}
}

// End-to-end check: an audio eval request that used to blow the 1.5x
// threshold under the old byte-counting path now stays well within budget.
func TestProcessRequest_MultimodalDoesNotFalselyReject(t *testing.T) {
	db := buildTestDB()
	p := New(true, func() *modeldb.ModelDB { return db })

	// ~1MB base64 audio payload. Raw-byte estimate = ~250k tokens → would
	// fail the 1.5x check on test-vision-model (max_input=128_000 * 1.5 =
	// 192_000). Under per-type budget it's 6000/4 = 1500 tokens — well under.
	bigAudio := strings.Repeat("A", 1_000_000)
	content := json.RawMessage(`[{"type":"input_audio","input_audio":{"data":"` + bigAudio + `","format":"wav"}}]`)
	req := &models.ChatCompletionRequest{
		Messages: []models.Message{{Role: "user", Content: content}},
	}
	rc := newRC("test-vision-model", req)
	result := p.ProcessRequest(context.Background(), rc)

	if result.Error != nil {
		t.Fatalf("multimodal audio wrongly rejected: status=%d code=%q msg=%q",
			result.Error.Status, result.Error.Code, result.Error.Message)
	}
	if result.Action != pipeline.Continue {
		t.Errorf("action = %v, want Continue", result.Action)
	}
}
