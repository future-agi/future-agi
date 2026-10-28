package contractwatch

import (
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// TestPresetSpecsMatchProviders pins each OpenAI-compatible preset section's
// lister to the preset the gateway (and cmd/contract-recorder) calls: the
// same base URL and API path prefix (providers.KnownProviders), and the
// recorder's <PRESET>_API_KEY variable, so one set of secrets serves both.
func TestPresetSpecsMatchProviders(t *testing.T) {
	for _, name := range []string{"groq", "mistral", "cerebras", "together", "fireworks", "openrouter"} {
		spec, ok := Specs[name]
		if !ok || spec.NewLister == nil {
			t.Errorf("%s: no lister", name)
			continue
		}
		p, ok := providers.KnownProviders[name]
		if !ok || p.APIFormat != "openai" {
			t.Errorf("%s: not an OpenAI-compatible preset in providers.KnownProviders", name)
			continue
		}
		env := strings.ToUpper(name) + "_API_KEY"
		if len(spec.Env) != 1 || spec.Env[0] != env {
			t.Errorf("%s: Env %v, want [%s] (the recorder's variable)", name, spec.Env, env)
		}
		l, ok := spec.NewLister(func(string) string { return "k" }, &http.Client{}).(OpenAILister)
		if !ok {
			t.Errorf("%s: lister is not an OpenAILister", name)
			continue
		}
		if got, want := l.BaseURL+l.Path, p.BaseURL+p.PathPrefix+"/models"; got != want {
			t.Errorf("%s: lists %s, want %s (the preset's base URL and path prefix)", name, got, want)
		}
	}
}

// TestCommittedSectionsPerFixtureProvider: every preset section of the
// committed registry files its fixtures under the adapter the recorder
// records it with: the openai suite for an OpenAI-compatible preset, the
// gemini suite for vertex.
func TestCommittedSectionsPerFixtureProvider(t *testing.T) {
	reg, _ := loadCommitted(t)
	for _, name := range reg.Names() {
		s := reg.Providers[name]
		want, prefix := name, ""
		switch {
		case name == "vertex":
			want, prefix = "gemini", "vertex@"
		case providers.KnownProviders[name].APIFormat == "openai":
			want, prefix = "openai", name+"@"
		}
		if got := s.FixtureProvider(name); got != want {
			t.Errorf("%s: fixtures under %s, want %s", name, got, want)
		}
		if got := s.FixtureKey(""); got != prefix {
			t.Errorf("%s: fixture prefix %q, want %q", name, got, prefix)
		}
	}
}
