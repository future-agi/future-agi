package modeldb

import (
	"encoding/json"
	"fmt"
	"log/slog"
)

type liteLLMEntry struct {
	InputCostPerToken               float64  `json:"input_cost_per_token"`
	OutputCostPerToken              float64  `json:"output_cost_per_token"`
	CacheReadInputTokenCost         float64  `json:"cache_read_input_token_cost"`
	InputCostPerTokenBatches        float64  `json:"input_cost_per_token_batches"`
	OutputCostPerTokenBatches       float64  `json:"output_cost_per_token_batches"`
	OutputCostPerImage              float64  `json:"output_cost_per_image"`
	Provider                        string   `json:"litellm_provider"`
	Mode                            string   `json:"mode"`
	MaxInputTokens                  float64  `json:"max_input_tokens"`
	MaxOutputTokens                 float64  `json:"max_output_tokens"`
	MaxTokens                       float64  `json:"max_tokens"`
	DeprecationDate                 string   `json:"deprecation_date"`
	SupportedRegions                []string `json:"supported_regions"`
	SupportsFunctionCalling         bool     `json:"supports_function_calling"`
	SupportsParallelFunctionCalling bool     `json:"supports_parallel_function_calling"`
	SupportsVision                  bool     `json:"supports_vision"`
	SupportsAudioInput              bool     `json:"supports_audio_input"`
	SupportsAudioOutput             bool     `json:"supports_audio_output"`
	SupportsPDFInput                bool     `json:"supports_pdf_input"`
	SupportsResponseSchema          bool     `json:"supports_response_schema"`
	SupportsSystemMessages          bool     `json:"supports_system_messages"`
	SupportsPromptCaching           bool     `json:"supports_prompt_caching"`
	SupportsReasoning               bool     `json:"supports_reasoning"`
}

func parseLiteLLM(data []byte) (map[string]*ModelInfo, error) {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil, fmt.Errorf("unmarshal litellm JSON: %w", err)
	}

	models := make(map[string]*ModelInfo, len(raw))
	skipped := 0

	for key, rawEntry := range raw {
		if key == "sample_spec" {
			continue
		}

		var entry liteLLMEntry
		if err := json.Unmarshal(rawEntry, &entry); err != nil {
			slog.Warn("skipping unparseable litellm entry", "key", key, "error", err)
			skipped++
			continue
		}

		mode := mapMode(entry.Mode)

		if isZeroPricing(mode, entry) {
			skipped++
			continue
		}

		maxOut := entry.MaxOutputTokens
		if maxOut == 0 {
			maxOut = entry.MaxTokens
		}

		m := &ModelInfo{
			ID:              key,
			Provider:        normalizeProvider(entry.Provider),
			Mode:            mode,
			MaxInputTokens:  int(entry.MaxInputTokens),
			MaxOutputTokens: int(maxOut),
			Pricing: PricingInfo{
				InputPerToken:       entry.InputCostPerToken,
				OutputPerToken:      entry.OutputCostPerToken,
				CachedInputPerToken: entry.CacheReadInputTokenCost,
				BatchInputPerToken:  entry.InputCostPerTokenBatches,
				BatchOutputPerToken: entry.OutputCostPerTokenBatches,
				OutputPerImage:      entry.OutputCostPerImage,
			},
			Capabilities: CapabilityFlags{
				FunctionCalling:   entry.SupportsFunctionCalling,
				ParallelToolCalls: entry.SupportsParallelFunctionCalling,
				Vision:            entry.SupportsVision,
				AudioInput:        entry.SupportsAudioInput,
				AudioOutput:       entry.SupportsAudioOutput,
				PDFInput:          entry.SupportsPDFInput,
				Streaming:         mode == ModeChat,
				ResponseSchema:    entry.SupportsResponseSchema,
				SystemMessages:    entry.SupportsSystemMessages,
				PromptCaching:     entry.SupportsPromptCaching,
				Reasoning:         entry.SupportsReasoning,
			},
			DeprecationDate: entry.DeprecationDate,
			Regions:         entry.SupportedRegions,
		}

		models[key] = m
	}

	slog.Info("parsed litellm pricing data", "loaded", len(models), "skipped", skipped)
	return models, nil
}

func mapMode(s string) ModelMode {
	switch s {
	case "chat", "completion":
		return ModeChat
	case "embedding":
		return ModeEmbedding
	case "image_generation":
		return ModeImageGeneration
	case "audio_transcription":
		return ModeAudioTranscription
	case "audio_speech":
		return ModeAudioSpeech
	case "rerank":
		return ModeRerank
	case "moderation":
		return ModeModeration
	default:
		return ModeChat
	}
}

func normalizeProvider(p string) string {
	switch p {
	case "bedrock_converse":
		return "bedrock"
	case "vertex_ai", "vertex_ai_beta":
		return "vertex_ai"
	case "azure_ai":
		return "azure"
	default:
		return p
	}
}

func isZeroPricing(mode ModelMode, entry liteLLMEntry) bool {
	switch mode {
	case ModeChat:
		return entry.InputCostPerToken == 0
	case ModeEmbedding:
		return entry.InputCostPerToken == 0 && entry.OutputCostPerToken == 0
	default:
		return false
	}
}
