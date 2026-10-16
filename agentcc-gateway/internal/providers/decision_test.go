package providers

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
	"github.com/futureagi/agentcc-gateway/internal/providers/systemone"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestRegistrySystemOne(t *testing.T) {
	for _, format := range []string{"systemone", "typesafe"} {
		t.Run(format, func(t *testing.T) {
			cfg := config.DefaultConfig()
			cfg.Providers = map[string]config.ProviderConfig{"typesafe": {APIFormat: format, Models: []string{"jev-1.13.0", "jev-latest"}}}
			registry, err := NewRegistry(cfg)
			if err != nil {
				t.Fatal(err)
			}
			defer registry.Close()
			for _, model := range []string{"jev-1.13.0", "jev-latest"} {
				p, err := registry.Resolve(model)
				if err != nil {
					t.Fatal(err)
				}
				if _, ok := p.(*systemone.Provider); !ok || p.ID() != "typesafe" {
					t.Fatalf("%s resolved to %T %s", model, p, p.ID())
				}
				if _, ok := p.(SystemOneProvider); !ok {
					t.Fatal("missing SystemOneProvider interface")
				}
			}
		})
	}
}

func TestOpenAIProviderServesDecisions(t *testing.T) {
	var p Provider
	p, err := openai.New("openai", config.ProviderConfig{BaseURL: "https://api.openai.com", APIFormat: "openai"})
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := p.(DecisionsProvider); !ok {
		t.Fatal("the openai provider does not implement DecisionsProvider")
	}
	if _, ok := p.(SystemOneProvider); ok {
		t.Fatal("the openai provider must not speak System One")
	}
}

func TestUnsupportedFormatErrorListsSystemOne(t *testing.T) {
	_, err := createProvider("x", config.ProviderConfig{APIFormat: "nope"})
	if err == nil || !strings.Contains(err.Error(), "systemone") {
		t.Fatalf("err = %v", err)
	}
}

// An org's System One credential becomes a System One provider whether its
// api_format says systemone, says the typesafe alias, or is unset on a
// provider named typesafe.
func TestOrgProviderSystemOne(t *testing.T) {
	cache := NewOrgProviderCache(nil, false)
	defer cache.EvictAll()
	for _, tt := range []struct{ id, format string }{
		{"typesafe", "systemone"},
		{"typesafe", "typesafe"},
		{"typesafe", ""},
		{"perplexity-decider", "systemone"},
	} {
		p, err := cache.GetOrCreateWithTenantConfig("org-"+tt.id+tt.format, tt.id, "synthetic-org-key", &tenant.ProviderConfig{APIFormat: tt.format, Models: []string{"m"}})
		if err != nil {
			t.Fatalf("%s/%s: %v", tt.id, tt.format, err)
		}
		if _, ok := p.(SystemOneProvider); !ok {
			t.Fatalf("%s/%s: org provider = %T, want SystemOneProvider", tt.id, tt.format, p)
		}
	}
	if got := inferAPIFormat("typesafe"); got != "systemone" {
		t.Errorf("inferAPIFormat(typesafe) = %q", got)
	}
}

// A decision provider saved through a Gateway > Providers preset arrives as
// the backend pushes it, through the admin contract, and becomes a System One
// provider posting to that vendor's own endpoint with the org's key. The
// fixture is shared with the backend's test of the same save and push.
func TestDecisionProvidersPushedByTheBackend(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "api_contracts", "gateway", "decision-providers.json"))
	if err != nil {
		t.Fatalf("reading the shared decision provider cases: %v", err)
	}
	var fixture struct {
		DNS     map[string][]string `json:"dns"`
		Pushed  json.RawMessage     `json:"pushed"`
		Gateway map[string]struct {
			Dialect  string `json:"dialect"`
			Endpoint string `json:"endpoint"`
		} `json:"gateway"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}

	// Decoded as PUT /-/orgs/{id}/config decodes it.
	var contract gatewayadmin.OrgConfig
	decoder := json.NewDecoder(bytes.NewReader(fixture.Pushed))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&contract); err != nil {
		t.Fatalf("the pushed config does not fit the admin contract: %v", err)
	}
	encoded, _ := json.Marshal(contract)
	var org tenant.OrgConfig
	if err := json.Unmarshal(encoded, &org); err != nil {
		t.Fatal(err)
	}
	if len(org.Providers) != len(fixture.Gateway) {
		t.Fatalf("pushed %d providers, expected %d", len(org.Providers), len(fixture.Gateway))
	}

	stubLookupIP(t, fixture.DNS)
	cache := NewOrgProviderCache(nil, false)
	defer cache.EvictAll()
	for id, want := range fixture.Gateway {
		pcfg := org.Providers[id]
		if pcfg == nil || pcfg.APIFormat != APIFormatSystemOne || !pcfg.Enabled || !pcfg.HasCredentials() {
			t.Fatalf("%s arrived as %+v", id, pcfg)
		}
		p, err := cache.GetOrCreateWithTenantConfig("org-presets", id, pcfg.APIKey, pcfg)
		if err != nil {
			t.Fatalf("%s: %v", id, err)
		}
		sp, ok := p.(*systemone.Provider)
		if !ok {
			t.Fatalf("%s: built %T, want a System One provider", id, p)
		}
		if sp.Dialect().Name != want.Dialect || sp.Endpoint() != want.Endpoint {
			t.Errorf("%s: dialect %q posting to %q, want %q posting to %q", id, sp.Dialect().Name, sp.Endpoint(), want.Dialect, want.Endpoint)
		}
	}
}

func TestDecisionDialectBaseURLs(t *testing.T) {
	got := DecisionDialectBaseURLs(map[string]config.ProviderConfig{
		// Only a base_url is ever read; the key is irrelevant here.
		"typesafe":           {APIFormat: "systemone", BaseURL: "https://typesafe.internal", APIKey: "FAGI-HELD-KEY"},
		"a-typesafe-mirror":  {APIFormat: "typesafe", BaseURL: "https://api.typesafe.ai"},
		"perplexity":         {APIFormat: "openai", BaseURL: "https://api.perplexity.ai"}, // Sonar chat, not the Decider
		"perplexity-decider": {APIFormat: "systemone"},                                    // no base_url: dialect default
		"openai":             {APIFormat: "openai", BaseURL: "https://openai-proxy.internal"},
		"groq":               {APIFormat: "openai", BaseURL: "https://api.groq.com/openai"},
		"custom-systemone":   {APIFormat: "systemone", BaseURL: "https://foundry.example.com"},
	})
	want := map[string]string{
		// ID order: a-typesafe-mirror comes before typesafe.
		"typesafe": "https://api.typesafe.ai",
		"openai":   "https://openai-proxy.internal",
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("bases = %v, want %v", got, want)
	}
}

func TestNewKeyedDecisionProvider(t *testing.T) {
	for _, tt := range []struct {
		dialect, base string
		format        string
	}{
		{"typesafe", "", models.DecisionFormatSystemOne},
		{"perplexity", "", models.DecisionFormatSystemOne},
		{"typesafe", "https://typesafe.internal", models.DecisionFormatSystemOne},
		{"openai", "", models.DecisionFormatOpenAI},
	} {
		p, err := NewKeyedDecisionProvider(tt.dialect, tt.base, "caller-key")
		if err != nil {
			t.Fatalf("%s: %v", tt.dialect, err)
		}
		switch tt.format {
		case models.DecisionFormatSystemOne:
			sp, ok := p.(*systemone.Provider)
			if !ok || sp.Dialect().Name != tt.dialect {
				t.Fatalf("%s: built %T", tt.dialect, p)
			}
		case models.DecisionFormatOpenAI:
			if _, ok := p.(DecisionsProvider); !ok {
				t.Fatalf("%s: built %T", tt.dialect, p)
			}
		}
		if format, ok := DecisionDialectFormat(tt.dialect); !ok || format != tt.format {
			t.Errorf("DecisionDialectFormat(%q) = %q", tt.dialect, format)
		}
		p.Close()
	}
	if _, err := NewKeyedDecisionProvider("generic", "", "k"); err == nil {
		t.Error("built a provider for an unknown dialect")
	}
	if _, ok := DecisionDialectFormat("anthropic"); ok {
		t.Error("anthropic is not a decision dialect")
	}
}

func TestProvidersFor(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{
		"typesafe-b": {APIFormat: "systemone", Models: []string{"jev-latest"}},
		"typesafe-a": {APIFormat: "systemone", Models: []string{"jev-latest", "jev-1.13.0"}},
	}
	cfg.ModelMap = map[string]string{"alias": "typesafe-b"}
	registry, err := NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer registry.Close()
	if got := registry.ProvidersFor("jev-latest"); !reflect.DeepEqual(got, []string{"typesafe-a", "typesafe-b"}) {
		t.Errorf("jev-latest: %v", got)
	}
	if got := registry.ProvidersFor("alias"); !reflect.DeepEqual(got, []string{"typesafe-b"}) {
		t.Errorf("alias: %v", got)
	}
	// No guessing: neither a prefix nor the only-provider default.
	if got := registry.ProvidersFor("typesafe-a/jev-latest"); len(got) != 0 {
		t.Errorf("prefixed: %v", got)
	}
}

func TestAutoDiscoverSkipsSystemOne(t *testing.T) {
	cfg := config.ProviderConfig{APIFormat: "systemone", BaseURL: "http://127.0.0.1:1"}
	if got := autoDiscoverModels("typesafe", &cfg); got != nil {
		t.Fatalf("discovered %v", got)
	}
}
