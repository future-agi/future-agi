package config

import (
	"strings"
	"testing"
)

// managed: true settles org credits on the adapter's usage
// accounting, so only adapters the usage-presence matrices verify may carry
// it. "google" builds the same adapter as "gemini"; "bedrock" covers both its
// invoke and converse paths.
func TestManagedProviderNeedsPresenceVerifiedAdapter(t *testing.T) {
	provider := func(format string, managed bool) *Config {
		c := DefaultConfig()
		c.Providers["p"] = ProviderConfig{BaseURL: "https://upstream.invalid", APIFormat: format, Managed: managed}
		return c
	}
	for _, format := range []string{"openai", "azure", "anthropic", "gemini", "google", "cohere", "bedrock"} {
		if err := provider(format, true).Validate(); err != nil {
			t.Errorf("managed %q refused: %v", format, err)
		}
	}
	for _, format := range []string{"deepgram", "cartesia", "acme", "OpenAI", "vllm"} {
		err := provider(format, true).Validate()
		if err == nil || !strings.Contains(err.Error(), "managed") || !strings.Contains(err.Error(), `"`+format+`"`) {
			t.Errorf("managed %q: Validate = %v, want a managed api_format refusal naming it", format, err)
		}
		if err := provider(format, false).Validate(); err != nil {
			t.Errorf("unmanaged %q must stay allowed: %v", format, err)
		}
	}
	err := loadW2cYAML(t, "providers:\n  stt:\n    base_url: https://upstream.invalid\n    api_format: deepgram\n    managed: true\n")
	if err == nil || !strings.Contains(err.Error(), "managed") {
		t.Errorf("Load of a managed deepgram provider = %v, want a managed api_format refusal", err)
	}
	if err := loadW2cYAML(t, "providers:\n  stt:\n    base_url: https://upstream.invalid\n    api_format: deepgram\n"); err != nil {
		t.Errorf("Load of an unmanaged deepgram provider: %v", err)
	}
}
