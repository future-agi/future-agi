package modeldb

import (
	_ "embed"
	"log"
)

//go:embed litellm.json
var liteLLMData []byte

var BundledModels map[string]*ModelInfo

func init() {
	var err error
	BundledModels, err = parseLiteLLM(liteLLMData)
	if err != nil {
		log.Fatalf("failed to parse embedded litellm pricing data: %v", err)
	}

	minimaxM3 := BundledModels["minimax/MiniMax-M3"]
	if minimaxM3 == nil {
		minimaxM3 = &ModelInfo{}
		BundledModels["minimax/MiniMax-M3"] = minimaxM3
	}
	minimaxM3.Provider = "minimax"
	minimaxM3.Mode = ModeChat
	minimaxM3.MaxInputTokens = 1_000_000
	minimaxM3.Pricing.InputPerToken = 0.6e-6
	minimaxM3.Pricing.OutputPerToken = 2.4e-6
	minimaxM3.Pricing.CachedInputPerToken = 0.12e-6
	minimaxM3.Capabilities.Vision = true
	minimaxM3.Capabilities.Streaming = true
	minimaxM3.Capabilities.PromptCaching = true
	minimaxM3.Capabilities.Reasoning = true

	minimaxM27 := BundledModels["minimax/MiniMax-M2.7"]
	if minimaxM27 == nil {
		minimaxM27 = &ModelInfo{}
		BundledModels["minimax/MiniMax-M2.7"] = minimaxM27
	}
	minimaxM27.Provider = "minimax"
	minimaxM27.Mode = ModeChat
	minimaxM27.MaxInputTokens = 204800
	minimaxM27.Pricing.InputPerToken = 0.3e-6
	minimaxM27.Pricing.OutputPerToken = 1.2e-6
	minimaxM27.Pricing.CachedInputPerToken = 0.06e-6
	minimaxM27.Capabilities.Vision = false
	minimaxM27.Capabilities.Streaming = true
	minimaxM27.Capabilities.PromptCaching = true
	minimaxM27.Capabilities.Reasoning = true
}
