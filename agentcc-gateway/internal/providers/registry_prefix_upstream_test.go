package providers

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// A "<provider>/<model>" match is routed with the id the provider's adapter
// sends (UpstreamModelNamer), so the request carries what reaches the host.
// Otherwise a native pass-through, which no adapter cuts, sends the prefixed
// id, and eligibility judges "alpha alpha/route-model" for a model the host
// knows as "route-model" (C26Q-COUNT-ROUTE-PREFIX).
func TestResolveWithRouting_PrefixMatchRoutesWithTheUpstreamModel(t *testing.T) {
	mock := httptest.NewServer(http.NotFoundHandler())
	defer mock.Close()
	off := false

	const (
		arn         = "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/abc123def456"
		bedrockBare = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
	)
	cfg := config.DefaultConfig()
	cfg.Providers["alpha"] = config.ProviderConfig{BaseURL: mock.URL, APIKey: "k", APIFormat: "anthropic", Models: []string{"route-model"}, AutoDiscover: &off, Local: &off}
	cfg.Providers["gem"] = config.ProviderConfig{BaseURL: mock.URL, APIKey: "k", APIFormat: "gemini", Models: []string{"gem-model"}, AutoDiscover: &off, Local: &off}
	cfg.Providers["oai"] = config.ProviderConfig{BaseURL: mock.URL, APIKey: "k", APIFormat: "openai", Models: []string{"gpt-4o"}, AutoDiscover: &off, Local: &off}
	cfg.Providers["bedrock"] = config.ProviderConfig{BaseURL: mock.URL, APIFormat: "bedrock", AWSRegion: "us-east-1",
		AWSAccessKeyID: "AKIAEXAMPLE", AWSSecretAccessKey: "secret", Models: []string{bedrockBare}, AutoDiscover: &off, Local: &off}
	cfg.Providers["credits"] = config.ProviderConfig{BaseURL: mock.URL, APIKey: "k", APIFormat: "anthropic", Managed: true, Models: []string{"managed-model"}, AutoDiscover: &off, Local: &off}

	registry, err := newRegistryForTest(cfg)
	if err != nil {
		t.Fatalf("NewRegistry failed: %v", err)
	}
	defer registry.Close()

	for _, tc := range []struct {
		name, model, provider, wantOverride string
	}{
		{"anthropic adapter drops the prefix", "alpha/route-model", "alpha", "route-model"},
		{"gemini adapter drops the prefix", "gem/gem-model", "gem", "gem-model"},
		{"bedrock id without a slash of its own", "bedrock/" + bedrockBare, "bedrock", bedrockBare},
		{"an unlisted id is cut the same way", "alpha/unlisted-model", "alpha", "unlisted-model"},
		// The adapter cuts a first segment on every call, so an id that still
		// holds a "/" after the cut must reach it as named: cut twice it would
		// name another model upstream.
		{"bedrock ARN is cut exactly once, by the adapter", "bedrock/" + arn, "bedrock", ""},
		{"anthropic id with a slash of its own is cut once, by the adapter", "alpha/vendor/route-model", "alpha", ""},
		// An adapter that sends its model as it is keeps the id as named.
		{"openai adapter sends the model as it is", "oai/gpt-4o", "oai", ""},
		// A managed provider's id is cut by ResolveManaged, not here.
		{"managed provider keeps the id as named", "credits/managed-model", "credits", ""},
		// Direct lookups are not prefix matches.
		{"listed model is not a prefix match", "route-model", "alpha", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			result, err := registry.ResolveWithRouting(tc.model)
			if err != nil {
				t.Fatalf("ResolveWithRouting(%q): %v", tc.model, err)
			}
			if result.Provider.ID() != tc.provider {
				t.Fatalf("provider = %q, want %q", result.Provider.ID(), tc.provider)
			}
			if result.ModelOverride != tc.wantOverride {
				t.Fatalf("ModelOverride = %q, want %q", result.ModelOverride, tc.wantOverride)
			}
		})
	}
}
