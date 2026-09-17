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
		"groq":        {BaseURL: "https://api.groq.com/openai", APIFormat: "openai", PathPrefix: "/v1"},
		"mistral":     {BaseURL: "https://api.mistral.ai", APIFormat: "openai", PathPrefix: "/v1"},
		"together":    {BaseURL: "https://api.together.xyz", APIFormat: "openai", PathPrefix: "/v1"},
		"fireworks":   {BaseURL: "https://api.fireworks.ai/inference", APIFormat: "openai", PathPrefix: "/v1"},
		"deepinfra":   {BaseURL: "https://api.deepinfra.com", APIFormat: "openai", PathPrefix: "/v1"},
		"perplexity":  {BaseURL: "https://api.perplexity.ai", APIFormat: "openai", PathPrefix: "/v1"},
		"cerebras":    {BaseURL: "https://api.cerebras.ai", APIFormat: "openai", PathPrefix: "/v1"},
		"xai":         {BaseURL: "https://api.x.ai", APIFormat: "openai", PathPrefix: "/v1"},
		"huggingface": {BaseURL: "https://api-inference.huggingface.co", APIFormat: "openai", PathPrefix: "/v1"},
		"anyscale":    {BaseURL: "https://api.endpoints.anyscale.com", APIFormat: "openai", PathPrefix: "/v1"},
		"replicate":   {BaseURL: "https://api.replicate.com", APIFormat: "openai", PathPrefix: "/v1"},
		"openrouter":  {BaseURL: "https://openrouter.ai/api", APIFormat: "openai", PathPrefix: "/v1"},
		"azure":       {BaseURL: "", APIFormat: "azure", PathPrefix: ""},
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
			// PathPrefix is a plain string, so a row that simply omits it reads
			// as "" — which means "no version segment" and silently strips /v1.
			// Assert it per row so that omission fails here rather than in prod.
			if got.PathPrefix != want.PathPrefix {
				t.Errorf("PathPrefix = %q, want %q", got.PathPrefix, want.PathPrefix)
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

			// The preset's prefix must reach the config, and an openai-format
			// preset must state one: an omitted row would resolve to "" and
			// drop the version segment from every URL the provider builds.
			if preset.APIFormat == "openai" {
				if cfg.APIPathPrefix == nil {
					t.Fatalf("APIPathPrefix is nil after applying preset for %q", name)
				}
				if *cfg.APIPathPrefix != preset.PathPrefix {
					t.Errorf("APIPathPrefix = %q, want %q", *cfg.APIPathPrefix, preset.PathPrefix)
				}
				if preset.PathPrefix == "" {
					t.Errorf("preset %q states no PathPrefix; an openai-format preset "+
						"must state one explicitly, or every URL loses its version segment", name)
				}
			}
		})
	}
}

// ---------------------------------------------------------------------------
// 10. A preset prefix is a default, not an override — an explicit
//     api_path_prefix in the config must survive applyProviderPreset.
// ---------------------------------------------------------------------------

func TestPreset_ExplicitPathPrefixPreserved(t *testing.T) {
	for _, want := range []string{"", "/v1/openai"} {
		t.Run("prefix="+want, func(t *testing.T) {
			explicit := want
			cfg := &config.ProviderConfig{Type: "groq", APIPathPrefix: &explicit}
			applyProviderPreset(cfg)

			if cfg.APIPathPrefix == nil {
				t.Fatal("APIPathPrefix became nil")
			}
			if *cfg.APIPathPrefix != want {
				t.Errorf("APIPathPrefix = %q, want %q (explicit value must not be overridden)", *cfg.APIPathPrefix, want)
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
