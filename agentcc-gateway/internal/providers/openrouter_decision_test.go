package providers

import (
	"errors"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/providers/systemone"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestIsOpenRouterChat(t *testing.T) {
	for _, tt := range []struct {
		apiFormat, id, baseURL string
		want                   bool
	}{
		{"openai", "openrouter", "", true},
		{"openai", "openrouter", "https://openrouter.ai/api/v1", true},
		{"openai", "my-router", "https://openrouter.ai/api/v1", true},
		{"openai", "openrouter-eu", "https://eu.openrouter.ai/api/v1", true},
		{"systemone", "openrouter", "", false},                     // already System One
		{"openai", "typesafe", "", false},                          // TypeSafe saved as openai stays refused
		{"openai", "openrouter", "https://api.typesafe.ai", false}, // the host wins over the ID
		{"openai", "perplexity", "https://api.perplexity.ai", false},
		{"openai", "groq", "https://api.groq.com/openai", false},
		{"anthropic", "openrouter", "", false},
	} {
		if got := IsOpenRouterChat(tt.apiFormat, tt.id, tt.baseURL); got != tt.want {
			t.Errorf("IsOpenRouterChat(%q, %q, %q) = %v, want %v", tt.apiFormat, tt.id, tt.baseURL, got, tt.want)
		}
	}
}

// An OpenAI-format OpenRouter connection's decisions go to the origin of its
// chat base_url plus /api/alpha/decisions, never to its /api/v1 chat path.
func TestNewOpenRouterDecisionProviderEndpoint(t *testing.T) {
	prefix := "/v1"
	for base, want := range map[string]string{
		"":                             "https://openrouter.ai/api/alpha/decisions",
		"https://openrouter.ai/api/v1": "https://openrouter.ai/api/alpha/decisions",
		"https://openrouter.ai/api":    "https://openrouter.ai/api/alpha/decisions",
		"http://router.internal:8080/openrouter/api/v1": "http://router.internal:8080/api/alpha/decisions",
	} {
		p, err := NewOpenRouterDecisionProvider("openrouter", config.ProviderConfig{APIFormat: "openai", BaseURL: base, APIKey: "k", APIPathPrefix: &prefix})
		if err != nil {
			t.Fatalf("%q: %v", base, err)
		}
		sp := p.(*systemone.Provider)
		if sp.Dialect().Name != "openrouter" || sp.Endpoint() != want {
			t.Errorf("%q: dialect %q posting to %q, want openrouter posting to %q", base, sp.Dialect().Name, sp.Endpoint(), want)
		}
		_ = p.Close()
	}
}

// An org that states its own base_url gets it checked like any org base_url,
// and the decision provider is cached and evicted with the org's others.
func TestOrgProviderCacheOpenRouterDecision(t *testing.T) {
	stubLookupIP(t, map[string][]string{"openrouter.ai": {"203.0.113.7"}, "router.lan": {"10.1.2.3"}})
	cache := NewOrgProviderCache(map[string]config.ProviderConfig{
		// config.yaml's chat entry; its key is the operator's and never used.
		"openrouter": {APIFormat: "openai", BaseURL: "https://openrouter.ai/api", APIKey: "OPERATOR-KEY"},
	}, false)
	defer cache.EvictAll()

	stated := &tenant.ProviderConfig{APIKey: "ORG-KEY", APIFormat: "openai", BaseURL: "https://openrouter.ai/api/v1", Enabled: true}
	p, err := cache.GetOrCreateOpenRouterDecision("org-a", "openrouter", stated.APIKey, stated)
	if err != nil {
		t.Fatal(err)
	}
	if sp := p.(*systemone.Provider); sp.Endpoint() != "https://openrouter.ai/api/alpha/decisions" {
		t.Errorf("endpoint = %q", sp.Endpoint())
	}
	if again, _ := cache.GetOrCreateOpenRouterDecision("org-a", "openrouter", stated.APIKey, stated); again != p {
		t.Error("the decision provider was not cached")
	}

	// Layered onto config.yaml's entry: its upstream, not its key.
	layered := &tenant.ProviderConfig{APIKey: "ORG-KEY", APIFormat: "openai", Enabled: true}
	lp, err := cache.GetOrCreateOpenRouterDecision("org-b", "openrouter", layered.APIKey, layered)
	if err != nil {
		t.Fatal(err)
	}
	if sp := lp.(*systemone.Provider); sp.Endpoint() != "https://openrouter.ai/api/alpha/decisions" {
		t.Errorf("layered endpoint = %q", sp.Endpoint())
	}

	// A refused base_url is refused here too.
	for _, base := range []string{"http://127.0.0.1:8080/api/v1", "https://router.lan/api/v1", "http://169.254.169.254/api/v1"} {
		bad := &tenant.ProviderConfig{APIKey: "ORG-KEY", APIFormat: "openai", BaseURL: base, Enabled: true}
		_, err := cache.GetOrCreateOpenRouterDecision("org-c", "openrouter", bad.APIKey, bad)
		var urlErr *BaseURLError
		if !errors.As(err, &urlErr) {
			t.Errorf("%s: err = %v, want a BaseURLError", base, err)
		}
	}

	if n := cache.Count(); n != 2 {
		t.Fatalf("count = %d, want 2", n)
	}
	cache.Evict("org-a")
	if again, _ := cache.GetOrCreateOpenRouterDecision("org-a", "openrouter", stated.APIKey, stated); again == p || cache.Count() != 2 {
		t.Fatalf("Evict left the decision provider (count %d)", cache.Count())
	}
}

// A provider whose ID looks like a decision cache entry cannot be returned as
// another provider's decision provider.
func TestOrgProviderCacheOpenRouterDecisionDoesNotAliasProviderIDs(t *testing.T) {
	stubLookupIP(t, map[string][]string{"openrouter.ai": {"203.0.113.7"}})
	cache := NewOrgProviderCache(nil, false)
	defer cache.EvictAll()
	odd := &tenant.ProviderConfig{APIKey: "OTHER-KEY", APIFormat: "openai", BaseURL: "https://openrouter.ai/api/v1", Enabled: true}
	chat, err := cache.GetOrCreateWithTenantConfig("org-a", "openrouter#decision", odd.APIKey, odd)
	if err != nil {
		t.Fatal(err)
	}
	cfg := &tenant.ProviderConfig{APIKey: "ORG-KEY", APIFormat: "openai", BaseURL: "https://openrouter.ai/api/v1", Enabled: true}
	p, err := cache.GetOrCreateOpenRouterDecision("org-a", "openrouter", cfg.APIKey, cfg)
	if err != nil {
		t.Fatal(err)
	}
	if p == chat {
		t.Fatal("the decision provider is another provider's cached chat provider")
	}
	if _, ok := p.(*systemone.Provider); !ok {
		t.Fatalf("built %T, want a System One provider", p)
	}
}

// One OpenRouter entry whose decision route cannot be built does not stop the
// gateway: its chat provider is registered, and decisions fall back to it.
func TestRegistryKeepsStartingWithABadOpenRouterDecisionRoute(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Providers["openrouter"] = config.ProviderConfig{
		APIFormat: "openai", BaseURL: "ftp://openrouter.ai/api/v1", APIKey: "synthetic", Models: []string{"typesafe/jev-1.13"},
	}
	r, err := NewRegistry(cfg)
	if err != nil {
		t.Fatalf("NewRegistry: %v", err)
	}
	defer func() { _ = r.Close() }()
	chat, ok := r.GetProvider("openrouter")
	if !ok {
		t.Fatal("chat provider not registered")
	}
	if dp, _ := r.DecisionProvider("openrouter"); dp != chat {
		t.Errorf("decision provider = %T, want the chat provider when the decision route failed", dp)
	}
}
