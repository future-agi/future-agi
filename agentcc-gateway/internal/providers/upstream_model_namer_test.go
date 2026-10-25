package providers

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Each adapter says (UpstreamModelNamer) whether it sends a request's model
// to its host as it is. The server relies on the answer to send an org's own
// provider the id the org registered with its "<provider>/" prefix cut
// exactly once. The OpenAI adapter sends the model as it is; the others
// drop a first "/" segment, and leave a bare id alone.
func TestUpstreamModelNamerPerFormat(t *testing.T) {
	cases := []struct {
		format string
		cfg    config.ProviderConfig
		strips bool
	}{
		{"openai", config.ProviderConfig{BaseURL: "https://api.openai.com", APIKey: "k"}, false},
		{"anthropic", config.ProviderConfig{BaseURL: "https://api.anthropic.com", APIKey: "k"}, true},
		{"bedrock", config.ProviderConfig{BaseURL: "https://bedrock-runtime.us-east-1.amazonaws.com", AWSRegion: "us-east-1",
			AWSAccessKeyID: "AKIAEXAMPLE", AWSSecretAccessKey: "secret"}, true},
		{"gemini", config.ProviderConfig{BaseURL: "https://generativelanguage.googleapis.com", APIKey: "k"}, true},
		{"cohere", config.ProviderConfig{BaseURL: "https://api.cohere.com", APIKey: "k"}, true},
		{"azure", config.ProviderConfig{BaseURL: "https://example.openai.azure.com", APIKey: "k"}, true},
	}
	for _, c := range cases {
		t.Run(c.format, func(t *testing.T) {
			c.cfg.APIFormat = c.format
			p, err := createProvider(c.format, c.cfg)
			if err != nil {
				t.Fatalf("createProvider: %v", err)
			}
			n, ok := p.(UpstreamModelNamer)
			if !c.strips {
				if ok {
					t.Fatalf("the %s adapter is an UpstreamModelNamer; it sends the model as it is", c.format)
				}
				return
			}
			if !ok {
				t.Fatalf("the %s adapter is not an UpstreamModelNamer; it drops a first segment", c.format)
			}
			for in, want := range map[string]string{
				"vendor/model":      "model",
				"model":             "model",
				"vendor/org/model":  "org/model",
				"claude-haiku-4-5":  "claude-haiku-4-5",
				"us.anthropic.x:0":  "us.anthropic.x:0",
				"bedrock/arn:a:b/c": "arn:a:b/c",
			} {
				if got := n.UpstreamModelName(in); got != want {
					t.Errorf("UpstreamModelName(%q) = %q, want %q", in, got, want)
				}
			}
		})
	}
}
