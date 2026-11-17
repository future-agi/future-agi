package providers

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// A model a provider lists by its full name is served by that provider, even
// when the name starts with another configured provider's id: the listed
// models resolve before the "<provider>/<model>" prefix, so the known-models
// rule never reaches them.
func TestResolveKnownWithRoutingServesAModelListedUnderAnotherProvidersPrefix(t *testing.T) {
	off := false
	cfg := config.DefaultConfig()
	cfg.Providers["vertex_ai"] = config.ProviderConfig{BaseURL: "http://vertex-ai.invalid", APIKey: "k", APIFormat: "openai",
		Models: []string{"vertex_ai/gemini-2.5-flash"}, AutoDiscover: &off}
	cfg.Providers["vertex_ai_global"] = config.ProviderConfig{BaseURL: "http://vertex-ai-global.invalid", APIKey: "k", APIFormat: "openai",
		Models: []string{"vertex_ai/gemini-3.7-flash", "vertex_ai/gemini-3.8-flash"}, AutoDiscover: &off}
	cfg.Providers["google"] = config.ProviderConfig{BaseURL: "http://google.invalid", APIKey: "k", APIFormat: "openai",
		Models: []string{"gemini-3.7-flash"}, AutoDiscover: &off}
	r, err := NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { r.Close() })

	for _, tc := range []struct{ model, provider string }{
		{"vertex_ai/gemini-3.7-flash", "vertex_ai_global"},
		{"vertex_ai/gemini-3.8-flash", "vertex_ai_global"},
		{"gemini-3.7-flash", "google"},
		{"vertex_ai/gemini-2.5-flash", "vertex_ai"},
	} {
		res, err := r.ResolveKnownWithRouting(tc.model, nil)
		if err != nil || res.Provider.ID() != tc.provider {
			t.Fatalf("%s: (%v, %v), want provider %s", tc.model, res, err, tc.provider)
		}
	}
	// A name no provider lists, under a configured provider's prefix, is
	// still not found.
	_, err = r.ResolveKnownWithRouting("vertex_ai/gemini-unlisted", nil)
	wantModelNotFound(t, "vertex_ai/gemini-unlisted", err)
}
