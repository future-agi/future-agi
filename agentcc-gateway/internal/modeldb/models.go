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

	if _, exists := BundledModels["minimax/MiniMax-M2.7"]; !exists {
		BundledModels["minimax/MiniMax-M2.7"] = &ModelInfo{
			Provider:       "minimax",
			Mode:           ModeChat,
			MaxInputTokens: 204800,
			Pricing: PricingInfo{
				InputPerToken:       0.3e-6,
				OutputPerToken:      1.2e-6,
				CachedInputPerToken: 0.06e-6,
			},
			Capabilities: CapabilityFlags{
				Streaming:     true,
				PromptCaching: true,
				Reasoning:     true,
			},
		}
	}
}
