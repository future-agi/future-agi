package modeldb

import (
	"math"
	"testing"
)

func TestParseLiteLLM_BasicChat(t *testing.T) {
	data := []byte(`{
		"gpt-4o": {
			"input_cost_per_token": 2.5e-6,
			"output_cost_per_token": 1e-5,
			"cache_read_input_token_cost": 1.25e-6,
			"input_cost_per_token_batches": 1.25e-6,
			"output_cost_per_token_batches": 5e-6,
			"litellm_provider": "openai",
			"mode": "chat",
			"max_input_tokens": 128000,
			"max_output_tokens": 16384,
			"max_tokens": 16384,
			"deprecation_date": "2025-12-31",
			"supported_regions": ["global", "us-east-1"],
			"supports_function_calling": true,
			"supports_parallel_function_calling": true,
			"supports_vision": true,
			"supports_audio_input": false,
			"supports_audio_output": false,
			"supports_pdf_input": true,
			"supports_response_schema": true,
			"supports_system_messages": true,
			"supports_prompt_caching": true,
			"supports_reasoning": false
		}
	}`)

	models, err := parseLiteLLM(data)
	if err != nil {
		t.Fatalf("parseLiteLLM error: %v", err)
	}

	m, ok := models["gpt-4o"]
	if !ok {
		t.Fatal("expected gpt-4o in parsed models")
	}

	// Provider and mode.
	if m.Provider != "openai" {
		t.Errorf("Provider = %q, want openai", m.Provider)
	}
	if m.Mode != ModeChat {
		t.Errorf("Mode = %q, want chat", m.Mode)
	}

	// Token limits.
	if m.MaxInputTokens != 128000 {
		t.Errorf("MaxInputTokens = %d, want 128000", m.MaxInputTokens)
	}
	if m.MaxOutputTokens != 16384 {
		t.Errorf("MaxOutputTokens = %d, want 16384", m.MaxOutputTokens)
	}

	// Pricing.
	if math.Abs(m.Pricing.InputPerToken-2.5e-6) > 1e-15 {
		t.Errorf("InputPerToken = %g, want 2.5e-6", m.Pricing.InputPerToken)
	}
	if math.Abs(m.Pricing.OutputPerToken-1e-5) > 1e-15 {
		t.Errorf("OutputPerToken = %g, want 1e-5", m.Pricing.OutputPerToken)
	}
	if math.Abs(m.Pricing.CachedInputPerToken-1.25e-6) > 1e-15 {
		t.Errorf("CachedInputPerToken = %g, want 1.25e-6", m.Pricing.CachedInputPerToken)
	}
	if math.Abs(m.Pricing.BatchInputPerToken-1.25e-6) > 1e-15 {
		t.Errorf("BatchInputPerToken = %g, want 1.25e-6", m.Pricing.BatchInputPerToken)
	}
	if math.Abs(m.Pricing.BatchOutputPerToken-5e-6) > 1e-15 {
		t.Errorf("BatchOutputPerToken = %g, want 5e-6", m.Pricing.BatchOutputPerToken)
	}

	// Capabilities.
	if !m.Capabilities.FunctionCalling {
		t.Error("expected FunctionCalling = true")
	}
	if !m.Capabilities.ParallelToolCalls {
		t.Error("expected ParallelToolCalls = true")
	}
	if !m.Capabilities.Vision {
		t.Error("expected Vision = true")
	}
	if m.Capabilities.AudioInput {
		t.Error("expected AudioInput = false")
	}
	if m.Capabilities.AudioOutput {
		t.Error("expected AudioOutput = false")
	}
	if !m.Capabilities.PDFInput {
		t.Error("expected PDFInput = true")
	}
	if !m.Capabilities.Streaming {
		t.Error("expected Streaming = true (chat mode default)")
	}
	if !m.Capabilities.ResponseSchema {
		t.Error("expected ResponseSchema = true")
	}
	if !m.Capabilities.SystemMessages {
		t.Error("expected SystemMessages = true")
	}
	if !m.Capabilities.PromptCaching {
		t.Error("expected PromptCaching = true")
	}
	if m.Capabilities.Reasoning {
		t.Error("expected Reasoning = false")
	}

	// Deprecation date.
	if m.DeprecationDate != "2025-12-31" {
		t.Errorf("DeprecationDate = %q, want 2025-12-31", m.DeprecationDate)
	}

	// Regions.
	if len(m.Regions) != 2 {
		t.Fatalf("Regions len = %d, want 2", len(m.Regions))
	}
	if m.Regions[0] != "global" || m.Regions[1] != "us-east-1" {
		t.Errorf("Regions = %v, want [global us-east-1]", m.Regions)
	}
}

func TestParseLiteLLM_SkipsSampleSpec(t *testing.T) {
	data := []byte(`{
		"sample_spec": {
			"input_cost_per_token": 0.0,
			"output_cost_per_token": 0.0,
			"litellm_provider": "example",
			"mode": "chat",
			"max_input_tokens": 1000,
			"max_output_tokens": 1000
		},
		"gpt-4o": {
			"input_cost_per_token": 2.5e-6,
			"output_cost_per_token": 1e-5,
			"litellm_provider": "openai",
			"mode": "chat",
			"max_input_tokens": 128000,
			"max_output_tokens": 16384
		}
	}`)

	models, err := parseLiteLLM(data)
	if err != nil {
		t.Fatalf("parseLiteLLM error: %v", err)
	}

	if _, ok := models["sample_spec"]; ok {
		t.Error("expected sample_spec to be skipped")
	}
	if _, ok := models["gpt-4o"]; !ok {
		t.Error("expected gpt-4o to be present")
	}
	if len(models) != 1 {
		t.Errorf("expected 1 model, got %d", len(models))
	}
}

func TestParseLiteLLM_ImageModel(t *testing.T) {
	data := []byte(`{
		"dall-e-3": {
			"litellm_provider": "openai",
			"mode": "image_generation",
			"max_input_tokens": 4000,
			"output_cost_per_image": 0.04,
			"input_cost_per_token": 0.0,
			"output_cost_per_token": 0.0
		}
	}`)

	models, err := parseLiteLLM(data)
	if err != nil {
		t.Fatalf("parseLiteLLM error: %v", err)
	}

	m, ok := models["dall-e-3"]
	if !ok {
		t.Fatal("expected dall-e-3 in parsed models")
	}
	if m.Mode != ModeImageGeneration {
		t.Errorf("Mode = %q, want image_generation", m.Mode)
	}
	if math.Abs(m.Pricing.OutputPerImage-0.04) > 1e-15 {
		t.Errorf("OutputPerImage = %g, want 0.04", m.Pricing.OutputPerImage)
	}
	// Image generation models should not require token pricing to be included.
}

func TestParseLiteLLM_ModeMapping(t *testing.T) {
	tests := []struct {
		mode string
		want ModelMode
	}{
		{"chat", ModeChat},
		{"completion", ModeChat},
		{"embedding", ModeEmbedding},
		{"image_generation", ModeImageGeneration},
		{"audio_transcription", ModeAudioTranscription},
		{"audio_speech", ModeAudioSpeech},
		{"rerank", ModeRerank},
		{"moderation", ModeModeration},
		{"unknown_mode", ModeChat}, // default
		{"", ModeChat},             // empty defaults to chat
	}

	for _, tc := range tests {
		t.Run("mode_"+tc.mode, func(t *testing.T) {
			data := []byte(`{
				"test-model": {
					"input_cost_per_token": 1e-6,
					"output_cost_per_token": 2e-6,
					"litellm_provider": "test",
					"mode": "` + tc.mode + `",
					"max_input_tokens": 1000,
					"max_output_tokens": 500
				}
			}`)

			models, err := parseLiteLLM(data)
			if err != nil {
				t.Fatalf("parseLiteLLM error: %v", err)
			}

			m, ok := models["test-model"]
			if !ok {
				t.Fatal("expected test-model in parsed models")
			}
			if m.Mode != tc.want {
				t.Errorf("Mode = %q, want %q", m.Mode, tc.want)
			}
		})
	}
}

func TestParseLiteLLM_ProviderPrefixStripped(t *testing.T) {
	// Litellm keys like "openai/gpt-4o" should be preserved as-is in the map.
	data := []byte(`{
		"openai/gpt-4o": {
			"input_cost_per_token": 2.5e-6,
			"output_cost_per_token": 1e-5,
			"litellm_provider": "openai",
			"mode": "chat",
			"max_input_tokens": 128000,
			"max_output_tokens": 16384
		}
	}`)

	models, err := parseLiteLLM(data)
	if err != nil {
		t.Fatalf("parseLiteLLM error: %v", err)
	}

	if _, ok := models["openai/gpt-4o"]; !ok {
		t.Error("expected key 'openai/gpt-4o' to be preserved as-is")
	}
}

func TestParseLiteLLM_SkipsZeroPricingChat(t *testing.T) {
	data := []byte(`{
		"free-chat-model": {
			"input_cost_per_token": 0.0,
			"output_cost_per_token": 0.0,
			"litellm_provider": "test",
			"mode": "chat",
			"max_input_tokens": 1000,
			"max_output_tokens": 500
		},
		"per-request-model": {
			"input_cost_per_token": 0.0,
			"output_cost_per_token": 2.8e-6,
			"litellm_provider": "test",
			"mode": "chat",
			"max_input_tokens": 1000,
			"max_output_tokens": 500
		},
		"paid-chat-model": {
			"input_cost_per_token": 1e-6,
			"output_cost_per_token": 2e-6,
			"litellm_provider": "test",
			"mode": "chat",
			"max_input_tokens": 1000,
			"max_output_tokens": 500
		}
	}`)

	models, err := parseLiteLLM(data)
	if err != nil {
		t.Fatalf("parseLiteLLM error: %v", err)
	}

	if _, ok := models["free-chat-model"]; ok {
		t.Error("expected zero-pricing chat model to be skipped")
	}
	if _, ok := models["per-request-model"]; ok {
		t.Error("expected per-request (zero input) chat model to be skipped")
	}
	if _, ok := models["paid-chat-model"]; !ok {
		t.Error("expected paid-chat-model to be present")
	}
}

func TestParseLiteLLM_ProviderNormalization(t *testing.T) {
	data := []byte(`{
		"bedrock-model": {
			"input_cost_per_token": 1e-6,
			"output_cost_per_token": 2e-6,
			"litellm_provider": "bedrock_converse",
			"mode": "chat",
			"max_input_tokens": 1000,
			"max_output_tokens": 500
		},
		"vertex-model": {
			"input_cost_per_token": 1e-6,
			"output_cost_per_token": 2e-6,
			"litellm_provider": "vertex_ai_beta",
			"mode": "chat",
			"max_input_tokens": 1000,
			"max_output_tokens": 500
		},
		"azure-model": {
			"input_cost_per_token": 1e-6,
			"output_cost_per_token": 2e-6,
			"litellm_provider": "azure_ai",
			"mode": "chat",
			"max_input_tokens": 1000,
			"max_output_tokens": 500
		}
	}`)

	models, err := parseLiteLLM(data)
	if err != nil {
		t.Fatalf("parseLiteLLM error: %v", err)
	}

	if m := models["bedrock-model"]; m.Provider != "bedrock" {
		t.Errorf("bedrock_converse → Provider = %q, want bedrock", m.Provider)
	}
	if m := models["vertex-model"]; m.Provider != "vertex_ai" {
		t.Errorf("vertex_ai_beta → Provider = %q, want vertex_ai", m.Provider)
	}
	if m := models["azure-model"]; m.Provider != "azure" {
		t.Errorf("azure_ai → Provider = %q, want azure", m.Provider)
	}
}

func TestParseLiteLLM_MaxTokensFallback(t *testing.T) {
	// When max_output_tokens is missing, should use max_tokens as fallback.
	data := []byte(`{
		"legacy-model": {
			"input_cost_per_token": 1e-6,
			"output_cost_per_token": 2e-6,
			"litellm_provider": "test",
			"mode": "chat",
			"max_input_tokens": 4096,
			"max_tokens": 2048
		}
	}`)

	models, err := parseLiteLLM(data)
	if err != nil {
		t.Fatalf("parseLiteLLM error: %v", err)
	}

	m := models["legacy-model"]
	if m.MaxOutputTokens != 2048 {
		t.Errorf("MaxOutputTokens = %d, want 2048 (from max_tokens fallback)", m.MaxOutputTokens)
	}
}

func TestParseLiteLLM_ActualFile(t *testing.T) {
	models, err := parseLiteLLM(liteLLMData)
	if err != nil {
		t.Fatalf("parseLiteLLM on actual file: %v", err)
	}

	if len(models) < 500 {
		t.Errorf("expected 500+ models from litellm.json, got %d", len(models))
	}

	// Spot-check well-known models.
	spotChecks := []struct {
		id       string
		provider string
		mode     ModelMode
	}{
		{"gpt-4o", "openai", ModeChat},
		{"claude-3-5-sonnet-20241022", "anthropic", ModeChat},
		{"text-embedding-3-small", "openai", ModeEmbedding},
	}

	for _, sc := range spotChecks {
		t.Run(sc.id, func(t *testing.T) {
			m, ok := models[sc.id]
			if !ok {
				t.Fatalf("expected model %q to exist", sc.id)
			}
			if m.Provider != sc.provider {
				t.Errorf("Provider = %q, want %q", m.Provider, sc.provider)
			}
			if m.Mode != sc.mode {
				t.Errorf("Mode = %q, want %q", m.Mode, sc.mode)
			}
			if m.MaxInputTokens <= 0 {
				t.Errorf("MaxInputTokens = %d, want > 0", m.MaxInputTokens)
			}
		})
	}

	// Verify gpt-4o pricing specifically.
	gpt4o := models["gpt-4o"]
	if math.Abs(gpt4o.Pricing.InputPerToken-2.5e-6) > 1e-15 {
		t.Errorf("gpt-4o InputPerToken = %g, want 2.5e-6", gpt4o.Pricing.InputPerToken)
	}
	if math.Abs(gpt4o.Pricing.OutputPerToken-1e-5) > 1e-15 {
		t.Errorf("gpt-4o OutputPerToken = %g, want 1e-5", gpt4o.Pricing.OutputPerToken)
	}

	// Verify sample_spec is excluded.
	if _, ok := models["sample_spec"]; ok {
		t.Error("sample_spec should be excluded")
	}
}
