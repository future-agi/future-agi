package server

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Which of an org's own providers serves a model that more than one of them
// lists: a model_map entry for the model wins; otherwise a plain id goes to
// the provider whose id sorts first (byte order), every time, and a
// "<provider>/<model>" id goes to the provider that prefix names. Calls that
// work today keep working: there is no refusal for a model several providers
// list. (The gateway's own config providers follow the same smallest-id rule
// for their fallback since the registry registers them in sorted order; that
// has its own tests.)

// orgRouteIDs are the provider ids these tests give an org.
var orgRouteIDs = []string{"alpha", "bravo", "charlie", "delta", "echo", "Zulu", "beta", "gamma", "omega", "aaa", "bbb", "zeta"}

// orgRouteHandlers is a Handlers with only the org provider cache orgRouteFor
// builds the org's providers with. Each provider id has an operator entry on
// a loopback address the org's instance inherits (the org config sets no
// base_url), and nothing is ever called.
func orgRouteHandlers() *Handlers {
	base := map[string]config.ProviderConfig{}
	for _, id := range orgRouteIDs {
		base[id] = config.ProviderConfig{BaseURL: "http://127.0.0.1:1", APIKey: "operator-key", APIFormat: "openai"}
	}
	return &Handlers{orgProviderCache: providers.NewOrgProviderCache(base, false)}
}

func orgRouteProvider(models ...string) *tenant.ProviderConfig {
	return &tenant.ProviderConfig{Enabled: true, APIKey: "org-key", APIFormat: "openai", Models: models}
}

func orgRouteConfig(listing map[string][]string) *tenant.OrgConfig {
	org := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{}}
	for id, models := range listing {
		org.Providers[id] = orgRouteProvider(models...)
	}
	return org
}

// orgRoute is the provider orgRouteFor picks for model, the model it sends
// and the id the provider listed that matched; it fails the test on an error
// or no provider.
func orgRoute(t *testing.T, h *Handlers, org *tenant.OrgConfig, model string) (providerID, upstream, host string) {
	t.Helper()
	p, providerID, upstream, host, err := h.orgRouteFor("org-route", org, model)
	if err != nil || p == nil {
		t.Fatalf("orgRouteFor(%q) = %v, %q, %v; want a provider", model, p, providerID, err)
	}
	return providerID, upstream, host
}

func TestOrgPlainModelGoesToTheSmallestProviderID(t *testing.T) {
	h := orgRouteHandlers()
	org := orgRouteConfig(map[string][]string{
		"echo": {"m"}, "bravo": {"m"}, "delta": {"m"}, "alpha": {"m"}, "charlie": {"m"}, "Zulu": {"m"},
	})
	for i := 0; i < 200; i++ {
		if id, upstream, _ := orgRoute(t, h, org, "m"); id != "Zulu" || upstream != "m" {
			// Byte order: an upper-case id sorts before every lower-case one.
			t.Fatalf("call %d: %q reached %q sent as %q, want Zulu (the smallest id in byte order)", i, "m", id, upstream)
		}
		if got := orgProviderServing(org, "m"); got != "Zulu" {
			t.Fatalf("call %d: orgProviderServing(m) = %q, want Zulu", i, got)
		}
	}
}

func TestOrgPrefixedModelGoesToTheProviderItNames(t *testing.T) {
	h := orgRouteHandlers()
	org := orgRouteConfig(map[string][]string{"alpha": {"m"}, "bravo": {"m"}, "charlie": {"m"}})
	for _, model := range []string{"charlie/m", "CHARLIE/m"} {
		if id, upstream, host := orgRoute(t, h, org, model); id != "charlie" || upstream != "m" || host != "m" {
			t.Errorf("%q reached %q sent as %q (listed %q), want charlie sent as m", model, id, upstream, host)
		}
	}
}

func TestOrgModelMapPinsAPlainModel(t *testing.T) {
	h := orgRouteHandlers()
	org := orgRouteConfig(map[string][]string{"alpha": {"m"}, "bravo": {"m"}, "delta": {"m"}})
	org.ModelMap = map[string]string{"m": "delta"}
	for i := 0; i < 50; i++ {
		if id, _, _ := orgRoute(t, h, org, "m"); id != "delta" {
			t.Fatalf("call %d: m reached %q, want delta (the model_map entry)", i, id)
		}
	}
}

// The plain-id winner follows the providers' ids: rename the first one so it
// sorts last, and the other serves. This documents the rule.
func TestOrgPlainModelWinnerFollowsProviderIDs(t *testing.T) {
	h := orgRouteHandlers()
	if id, _, _ := orgRoute(t, h, orgRouteConfig(map[string][]string{"beta": {"m"}, "gamma": {"m"}}), "m"); id != "beta" {
		t.Errorf("with beta and gamma, m reached %q, want beta", id)
	}
	if id, _, _ := orgRoute(t, h, orgRouteConfig(map[string][]string{"omega": {"m"}, "gamma": {"m"}}), "m"); id != "gamma" {
		t.Errorf("with beta renamed omega, m reached %q, want gamma", id)
	}
}
