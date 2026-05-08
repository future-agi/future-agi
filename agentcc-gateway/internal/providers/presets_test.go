package providers

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// ---------------------------------------------------------------------------
// 1. applyProviderPreset — empty Type does nothing
// ---------------------------------------------------------------------------

func TestPreset_EmptyType(t *testing.T) {
	cfg := &config.ProviderConfig{
		Type:      "",
		BaseURL:   "",
		APIFormat: "",
	}
	applyProviderPreset(cfg)

	if cfg.BaseURL != "" {
		t.Errorf("BaseURL = %q, want empty (no preset applied)", cfg.BaseURL)
	}
	if cfg.APIFormat != "" {
		t.Errorf("APIFormat = %q, want empty (no preset applied)", cfg.APIFormat)
	}
}

// ---------------------------------------------------------------------------
// 2. applyProviderPreset — unknown Type does nothing
// ---------------------------------------------------------------------------

func TestPreset_UnknownType(t *testing.T) {
	cfg := &config.ProviderConfig{
		Type:      "nonexistent-provider-xyz",
		BaseURL:   "",
		APIFormat: "",
	}
	applyProviderPreset(cfg)

	if cfg.BaseURL != "" {
		t.Errorf("BaseURL = %q, want empty (unknown type)", cfg.BaseURL)
	}
	if cfg.APIFormat != "" {
		t.Errorf("APIFormat = %q, want empty (unknown type)", cfg.APIFormat)
	}
}

// ---------------------------------------------------------------------------
// 3. applyProviderPreset — known type "groq" fills BaseURL and APIFormat
// ---------------------------------------------------------------------------

func TestPreset_Groq(t *testing.T) {
	cfg := &config.ProviderConfig{
		Type: "groq",
	}
	applyProviderPreset(cfg)

	wantURL := "https://api.groq.com/openai"
	wantFmt := "openai"

	if cfg.BaseURL != wantURL {
		t.Errorf("BaseURL = %q, want %q", cfg.BaseURL, wantURL)
	}
	if cfg.APIFormat != wantFmt {
		t.Errorf("APIFormat = %q, want %q", cfg.APIFormat, wantFmt)
	}
}

// ---------------------------------------------------------------------------
// 4. applyProviderPreset — "azure" fills only APIFormat (no BaseURL in preset)
// ---------------------------------------------------------------------------

func TestPreset_Azure(t *testing.T) {
	cfg := &config.ProviderConfig{
		Type: "azure",
	}
	applyProviderPreset(cfg)

	if cfg.BaseURL != "" {
		t.Errorf("BaseURL = %q, want empty (azure preset has no BaseURL)", cfg.BaseURL)
	}
	wantFmt := "azure"
	if cfg.APIFormat != wantFmt {
		t.Errorf("APIFormat = %q, want %q", cfg.APIFormat, wantFmt)
	}
}

// ---------------------------------------------------------------------------
// 5. applyProviderPreset — explicit BaseURL is NOT overridden by preset
// ---------------------------------------------------------------------------

func TestPreset_ExplicitBaseURLPreserved(t *testing.T) {
	customURL := "https://my-custom-groq-proxy.example.com"
	cfg := &config.ProviderConfig{
		Type:    "groq",
		BaseURL: customURL,
	}
	applyProviderPreset(cfg)

	if cfg.BaseURL != customURL {
		t.Errorf("BaseURL = %q, want %q (explicit value must not be overridden)", cfg.BaseURL, customURL)
	}
	// APIFormat should still be filled from the preset since it was empty.
	if cfg.APIFormat != "openai" {
		t.Errorf("APIFormat = %q, want %q", cfg.APIFormat, "openai")
	}
}

// ---------------------------------------------------------------------------
// 6. applyProviderPreset — explicit APIFormat is NOT overridden by preset
// ---------------------------------------------------------------------------

func TestPreset_ExplicitAPIFormatPreserved(t *testing.T) {
	customFmt := "custom-format"
	cfg := &config.ProviderConfig{
		Type:      "groq",
		APIFormat: customFmt,
	}
	applyProviderPreset(cfg)

	if cfg.APIFormat != customFmt {
		t.Errorf("APIFormat = %q, want %q (explicit value must not be overridden)", cfg.APIFormat, customFmt)
	}
	// BaseURL should still be filled from the preset since it was empty.
	if cfg.BaseURL != "https://api.groq.com/openai" {
		t.Errorf("BaseURL = %q, want %q", cfg.BaseURL, "https://api.groq.com/openai")
	}
}

// ---------------------------------------------------------------------------
// 7. KnownProviders — verify all expected providers exist with correct values
// ---------------------------------------------------------------------------

func TestPreset_KnownProvidersComplete(t *testing.T) {
	expected := map[string]ProviderPreset{
		"groq":        {BaseURL: "https://api.groq.com/openai", APIFormat: "openai"},
		"mistral":     {BaseURL: "https://api.mistral.ai", APIFormat: "openai"},
		"together":    {BaseURL: "https://api.together.xyz", APIFormat: "openai"},
		"fireworks":   {BaseURL: "https://api.fireworks.ai/inference", APIFormat: "openai"},
		"deepinfra":   {BaseURL: "https://api.deepinfra.com", APIFormat: "openai"},
		"perplexity":  {BaseURL: "https://api.perplexity.ai", APIFormat: "openai"},
		"cerebras":    {BaseURL: "https://api.cerebras.ai", APIFormat: "openai"},
		"xai":         {BaseURL: "https://api.x.ai", APIFormat: "openai"},
		"huggingface": {BaseURL: "https://api-inference.huggingface.co", APIFormat: "openai"},
		"anyscale":    {BaseURL: "https://api.endpoints.anyscale.com", APIFormat: "openai"},
		"replicate":   {BaseURL: "https://api.replicate.com", APIFormat: "openai"},
		"openrouter":  {BaseURL: "https://openrouter.ai/api", APIFormat: "openai"},
		"azure":       {BaseURL: "", APIFormat: "azure"},
	}

	// Verify every expected provider is present with the correct values.
	for name, want := range expected {
		t.Run(name, func(t *testing.T) {
			got, ok := KnownProviders[name]
			if !ok {
				t.Fatalf("KnownProviders[%q] not found", name)
			}
			if got.BaseURL != want.BaseURL {
				t.Errorf("BaseURL = %q, want %q", got.BaseURL, want.BaseURL)
			}
			if got.APIFormat != want.APIFormat {
				t.Errorf("APIFormat = %q, want %q", got.APIFormat, want.APIFormat)
			}
		})
	}

	// Verify no unexpected providers are in the map.
	for name := range KnownProviders {
		if _, ok := expected[name]; !ok {
			t.Errorf("unexpected provider %q found in KnownProviders", name)
		}
	}

	// Verify counts match.
	if len(KnownProviders) != len(expected) {
		t.Errorf("KnownProviders has %d entries, want %d", len(KnownProviders), len(expected))
	}
}

// ---------------------------------------------------------------------------
// 8. Table-driven test for all known providers — BaseURL and APIFormat
//    are non-empty where expected
// ---------------------------------------------------------------------------

func TestPreset_AllKnownProviders_ApplyDefaults(t *testing.T) {
	// For every known provider, verify that applyProviderPreset fills in
	// BaseURL and APIFormat correctly when the config starts empty.
	for name, preset := range KnownProviders {
		t.Run(name, func(t *testing.T) {
			cfg := &config.ProviderConfig{Type: name}
			applyProviderPreset(cfg)

			// APIFormat must always be set by a preset.
			if cfg.APIFormat == "" {
				t.Errorf("APIFormat is empty after applying preset for %q", name)
			}
			if cfg.APIFormat != preset.APIFormat {
				t.Errorf("APIFormat = %q, want %q", cfg.APIFormat, preset.APIFormat)
			}

			// BaseURL should match the preset (may be empty for azure).
			if cfg.BaseURL != preset.BaseURL {
				t.Errorf("BaseURL = %q, want %q", cfg.BaseURL, preset.BaseURL)
			}

			// For non-azure providers, BaseURL must be non-empty.
			if name != "azure" && cfg.BaseURL == "" {
				t.Errorf("BaseURL is empty after applying preset for %q (expected non-empty)", name)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// 9. Both explicit BaseURL and APIFormat preserved simultaneously
// ---------------------------------------------------------------------------

func TestPreset_BothExplicitFieldsPreserved(t *testing.T) {
	customURL := "https://proxy.example.com"
	customFmt := "custom"
	cfg := &config.ProviderConfig{
		Type:      "groq",
		BaseURL:   customURL,
		APIFormat: customFmt,
	}
	applyProviderPreset(cfg)

	if cfg.BaseURL != customURL {
		t.Errorf("BaseURL = %q, want %q", cfg.BaseURL, customURL)
	}
	if cfg.APIFormat != customFmt {
		t.Errorf("APIFormat = %q, want %q", cfg.APIFormat, customFmt)
	}
}
