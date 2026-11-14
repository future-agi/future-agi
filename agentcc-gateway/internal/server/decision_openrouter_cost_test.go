package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A Jev decision sent through OpenRouter under OpenRouter's own name, and
// answered with the dated model OpenRouter resolves it to, reports the vendor
// price in x-agentcc-cost, the value Request Logs and budgets read.
func TestDecisionOpenRouterJevIsPriced(t *testing.T) {
	g := openRouterGateway(t)
	for _, tt := range []struct{ routerModel, resolved string }{
		{"typesafe/jev-1.13", "typesafe/jev-1.13-20260917"},
		{"typesafe/jev-1.14", "typesafe/jev-1.14-20261015"},
		{"~typesafe/jev-latest", "typesafe/jev-1.14-20261015"},
	} {
		answer := fmt.Sprintf(`{"model":%q,"answers":{"q":{"type":"refusal"}},"usage":{"input_tokens":1000000,"output_tokens":5}}`, tt.resolved)
		g.upstream.setReply(func(w http.ResponseWriter, _ *http.Request) {
			io.WriteString(w, answer)
		})
		body := fmt.Sprintf(`{"model":"openrouter/%s","state":"synthetic-state","questions":{"q":{"type":"noul"}}}`, tt.routerModel)
		rr := g.post(t, keyInternal, body, models.ProviderKeyHeader, callerOwnKey)
		if rr.Code != http.StatusOK || rr.Body.String() != answer {
			t.Fatalf("%s: status %d body %s", tt.routerModel, rr.Code, rr.Body.String())
		}
		if got := g.upstream.sentModel(t); got != tt.routerModel {
			t.Errorf("%s: upstream model = %q", tt.routerModel, got)
		}
		if got := rr.Header().Get("x-agentcc-cost"); got != "0.042000" {
			t.Errorf("%s answered as %s: x-agentcc-cost = %q, want 0.042000", tt.routerModel, tt.resolved, got)
		}
	}
}

// An org's OpenRouter connection lists Jev under OpenRouter's names with the
// vendor price; an unknown Jev version is listed with no price.
func TestDecisionModelsListingPricesOpenRouterJev(t *testing.T) {
	org := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openrouter": {
			APIKey: orgTypeSafeKey, APIFormat: "systemone", BaseURL: "https://203.0.113.7", Enabled: true,
			Models: []string{"typesafe/jev-1.13", "typesafe/jev-1.14", "typesafe/jev-9.9"},
		},
	}}
	g := newDecisionGateway(t, decisionGatewayOptions{orgs: map[string]*tenant.OrgConfig{"org-a": org}})
	rr := g.do(t, http.MethodGet, "/v1/decisions/models", keyByok, "")
	var resp struct {
		Data []decisionModel `json:"data"`
	}
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); rr.Code != http.StatusOK || err != nil {
		t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
	}
	jev := &decisionModelPricing{InputPerMTok: 0.042}
	want := []decisionModel{
		{ID: "openrouter/typesafe/jev-1.13", Object: "model", Provider: "openrouter", Format: "systemone", Pricing: jev},
		{ID: "openrouter/typesafe/jev-1.14", Object: "model", Provider: "openrouter", Format: "systemone", Pricing: jev},
		{ID: "openrouter/typesafe/jev-9.9", Object: "model", Provider: "openrouter", Format: "systemone"},
	}
	if gotJSON, wantJSON := mustJSON(resp.Data), mustJSON(want); gotJSON != wantJSON {
		t.Fatalf("models =\n%s\nwant\n%s", gotJSON, wantJSON)
	}
	expectNoCalls(t, g)
}

// An operator override keyed on any name a Jev model goes by is what both
// GET /v1/decisions/models shows and the call through OpenRouter is charged,
// including when OpenRouter answers with a dated release the bundled prices
// would normalize to jev-1.13.0 or jev-1.14.0.
func TestDecisionOpenRouterJevOverrideListingMatchesCharge(t *testing.T) {
	const overridePerMTok = 0.5
	price := overridePerMTok / 1_000_000
	for _, tt := range []struct {
		name, key, routerModel, resolved string
	}{
		{"upstream name", "typesafe/jev-1.13", "typesafe/jev-1.13", "typesafe/jev-1.13-20260917"},
		{"provider-qualified name", "openrouter/typesafe/jev-1.13", "typesafe/jev-1.13", "typesafe/jev-1.13-20260917"},
		{"jev-latest answered as dated jev-1.14", "jev-latest", "~typesafe/jev-latest", "typesafe/jev-1.14-20261015"},
		{"short name", "jev-1.13", "typesafe/jev-1.13", "typesafe/jev-1.13-20260917"},
		{"provider-qualified name, exact resolved", "openrouter/typesafe/jev-1.13", "typesafe/jev-1.13", "typesafe/jev-1.13.0"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			org := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
				"openrouter": {
					APIKey: orgTypeSafeKey, APIFormat: "systemone", BaseURL: "https://203.0.113.7", Enabled: true,
					Models: []string{tt.routerModel},
				},
			}}
			g := newDecisionGateway(t, decisionGatewayOptions{
				orgs: map[string]*tenant.OrgConfig{"org-a": org},
				modelOverrides: map[string]modeldb.ModelOverride{
					modeldb.DecisionKeyPrefix + tt.key: {Mode: string(modeldb.ModeDecision), Pricing: &modeldb.PricingOverride{InputPerToken: &price}},
				},
				config: func(cfg *config.Config, upstreamURL string) {
					cfg.Providers["openrouter"] = config.ProviderConfig{
						APIFormat: "systemone", BaseURL: upstreamURL, APIKey: fagiHeldKey,
						Models: []string{tt.routerModel},
					}
				},
			})

			rr := g.do(t, http.MethodGet, "/v1/decisions/models", keyByok, "")
			var resp struct {
				Data []decisionModel `json:"data"`
			}
			if err := json.Unmarshal(rr.Body.Bytes(), &resp); rr.Code != http.StatusOK || err != nil {
				t.Fatalf("listing status %d: %s", rr.Code, rr.Body.String())
			}
			id := "openrouter/" + tt.routerModel
			var listed *decisionModelPricing
			for _, entry := range resp.Data {
				if entry.ID == id {
					listed = entry.Pricing
				}
			}
			if listed == nil || listed.InputPerMTok != overridePerMTok {
				t.Fatalf("listing for %s = %+v, want input_per_mtok %v", id, listed, overridePerMTok)
			}

			answer := fmt.Sprintf(`{"model":%q,"answers":{"q":{"type":"refusal"}},"usage":{"input_tokens":1000000,"output_tokens":5}}`, tt.resolved)
			g.upstream.setReply(func(w http.ResponseWriter, _ *http.Request) {
				io.WriteString(w, answer)
			})
			body := fmt.Sprintf(`{"model":%q,"state":"synthetic-state","questions":{"q":{"type":"noul"}}}`, id)
			rr = g.post(t, keyInternal, body, models.ProviderKeyHeader, callerOwnKey)
			if rr.Code != http.StatusOK {
				t.Fatalf("call status %d: %s", rr.Code, rr.Body.String())
			}
			if got := rr.Header().Get("x-agentcc-cost"); got != "0.500000" {
				t.Fatalf("%s answered as %s: x-agentcc-cost = %q, want the listed 0.500000", id, tt.resolved, got)
			}
		})
	}
}

// listedDecisionPrice returns GET /v1/decisions/models's input price for id,
// or -1 when the entry is missing or unpriced.
func listedDecisionPrice(t *testing.T, g *decisionGateway, key, id string) float64 {
	t.Helper()
	rr := g.do(t, http.MethodGet, "/v1/decisions/models", key, "")
	var resp struct {
		Data []decisionModel `json:"data"`
	}
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); rr.Code != http.StatusOK || err != nil {
		t.Fatalf("listing status %d: %s", rr.Code, rr.Body.String())
	}
	for _, entry := range resp.Data {
		if entry.ID == id && entry.Pricing != nil {
			return entry.Pricing.InputPerMTok
		}
	}
	return -1
}

// chargedDecisionCost calls model, answered as resolved, and returns
// x-agentcc-cost for 1M input tokens.
func chargedDecisionCost(t *testing.T, g *decisionGateway, key, model, resolved string, headers ...string) string {
	t.Helper()
	answer := fmt.Sprintf(`{"model":%q,"answers":{"q":{"type":"refusal"}},"usage":{"input_tokens":1000000,"output_tokens":5}}`, resolved)
	g.upstream.setReply(func(w http.ResponseWriter, _ *http.Request) {
		io.WriteString(w, answer)
	})
	body := fmt.Sprintf(`{"model":%q,"state":"synthetic-state","questions":{"q":{"type":"noul"}}}`, model)
	rr := g.post(t, key, body, headers...)
	if rr.Code != http.StatusOK {
		t.Fatalf("%s: call status %d: %s", model, rr.Code, rr.Body.String())
	}
	return rr.Header().Get("x-agentcc-cost")
}

func decisionPriceOverrides(perMTok map[string]float64) map[string]modeldb.ModelOverride {
	out := map[string]modeldb.ModelOverride{}
	for key, v := range perMTok {
		price := v / 1_000_000
		out[modeldb.DecisionKeyPrefix+key] = modeldb.ModelOverride{Mode: string(modeldb.ModeDecision), Pricing: &modeldb.PricingOverride{InputPerToken: &price}}
	}
	return out
}

// A Models entry written with the provider's own prefix is sent upstream
// without it, so it is listed and charged at the upstream name's price, on
// the org path and on the auth-off registry path.
func TestDecisionPrefixedModelsEntryListingMatchesCharge(t *testing.T) {
	const entry = "openrouter/typesafe/jev-1.13"
	overrides := decisionPriceOverrides(map[string]float64{"typesafe/jev-1.13": 0.5})
	registry := func(cfg *config.Config, upstreamURL string) {
		cfg.Providers["openrouter"] = config.ProviderConfig{
			APIFormat: "systemone", BaseURL: upstreamURL, APIKey: fagiHeldKey, Models: []string{entry},
		}
	}

	t.Run("org", func(t *testing.T) {
		org := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
			"openrouter": {
				APIKey: orgTypeSafeKey, APIFormat: "systemone", BaseURL: "https://203.0.113.7", Enabled: true,
				Models: []string{entry},
			},
		}}
		g := newDecisionGateway(t, decisionGatewayOptions{
			orgs: map[string]*tenant.OrgConfig{"org-a": org}, modelOverrides: overrides, config: registry,
		})
		if got := listedDecisionPrice(t, g, keyByok, entry); got != 0.5 {
			t.Fatalf("listing for %s = %v, want 0.5", entry, got)
		}
		if got := chargedDecisionCost(t, g, keyInternal, entry, "typesafe/jev-1.13-20260917", models.ProviderKeyHeader, callerOwnKey); got != "0.500000" {
			t.Fatalf("charged %q, want the listed 0.500000", got)
		}
		if got := g.upstream.sentModel(t); got != "typesafe/jev-1.13" {
			t.Errorf("upstream model = %q", got)
		}
	})

	t.Run("org custom pricing", func(t *testing.T) {
		org := &tenant.OrgConfig{
			Providers: map[string]*tenant.ProviderConfig{
				"openrouter": {
					APIKey: orgTypeSafeKey, APIFormat: "systemone", BaseURL: "https://203.0.113.7", Enabled: true,
					Models: []string{entry},
				},
			},
			CostTracking: &tenant.CostTrackingConfig{Enabled: true, CustomPricing: map[string]*tenant.CustomPricing{
				"typesafe/jev-1.13": {InputPerMTok: 0.7},
			}},
		}
		g := newDecisionGateway(t, decisionGatewayOptions{orgs: map[string]*tenant.OrgConfig{"org-a": org}})
		if got := listedDecisionPrice(t, g, keyByok, entry); got != 0.7 {
			t.Fatalf("listing for %s = %v, want the org's 0.7", entry, got)
		}
	})

	t.Run("registry", func(t *testing.T) {
		g := newDecisionGateway(t, decisionGatewayOptions{authOff: true, modelOverrides: overrides, config: registry})
		if got := listedDecisionPrice(t, g, "", entry); got != 0.5 {
			t.Fatalf("listing for %s = %v, want 0.5", entry, got)
		}
		if got := chargedDecisionCost(t, g, "", entry, "typesafe/jev-1.13-20260917"); got != "0.500000" {
			t.Fatalf("charged %q, want the listed 0.500000", got)
		}
	})
}

// A provider-specific override on the caller's qualified name beats a generic
// one on the version's short name, whether OpenRouter answers with an undated
// or a dated name, and the listing shows the same price.
func TestDecisionQualifiedOverrideBeatsGenericLastSegment(t *testing.T) {
	const id = "openrouter/typesafe/jev-1.13"
	g := newDecisionGateway(t, decisionGatewayOptions{
		modelOverrides: decisionPriceOverrides(map[string]float64{"jev-1.13.0": 0.3, id: 0.5}),
		config: func(cfg *config.Config, upstreamURL string) {
			cfg.Providers["openrouter"] = config.ProviderConfig{
				APIFormat: "systemone", BaseURL: upstreamURL, APIKey: fagiHeldKey, Models: []string{"typesafe/jev-1.13"},
			}
		},
		orgs: map[string]*tenant.OrgConfig{"org-a": {Providers: map[string]*tenant.ProviderConfig{
			"openrouter": {
				APIKey: orgTypeSafeKey, APIFormat: "systemone", BaseURL: "https://203.0.113.7", Enabled: true,
				Models: []string{"typesafe/jev-1.13"},
			},
		}}},
	})
	if got := listedDecisionPrice(t, g, keyByok, id); got != 0.5 {
		t.Fatalf("listing for %s = %v, want 0.5", id, got)
	}
	for _, resolved := range []string{"typesafe/jev-1.13.0", "typesafe/jev-1.13-20260917"} {
		if got := chargedDecisionCost(t, g, keyInternal, id, resolved, models.ProviderKeyHeader, callerOwnKey); got != "0.500000" {
			t.Errorf("answered as %s: charged %q, want the listed 0.500000", resolved, got)
		}
	}
}

// Documented difference: the listing cannot know what an alias resolves to. An
// override on the resolved version is charged, while the alias's entry shows
// the bundled jev-latest price.
func TestDecisionAliasListingCannotKnowTheResolvedVersion(t *testing.T) {
	const id = "openrouter/~typesafe/jev-latest"
	g := newDecisionGateway(t, decisionGatewayOptions{
		modelOverrides: decisionPriceOverrides(map[string]float64{"jev-1.14.0": 0.5}),
		config: func(cfg *config.Config, upstreamURL string) {
			cfg.Providers["openrouter"] = config.ProviderConfig{
				APIFormat: "systemone", BaseURL: upstreamURL, APIKey: fagiHeldKey, Models: []string{"~typesafe/jev-latest"},
			}
		},
		orgs: map[string]*tenant.OrgConfig{"org-a": {Providers: map[string]*tenant.ProviderConfig{
			"openrouter": {
				APIKey: orgTypeSafeKey, APIFormat: "systemone", BaseURL: "https://203.0.113.7", Enabled: true,
				Models: []string{"~typesafe/jev-latest"},
			},
		}}},
	})
	if got := listedDecisionPrice(t, g, keyByok, id); got != 0.042 {
		t.Fatalf("listing for %s = %v, want the bundled 0.042", id, got)
	}
	if got := chargedDecisionCost(t, g, keyInternal, id, "typesafe/jev-1.14-20261015", models.ProviderKeyHeader, callerOwnKey); got != "0.500000" {
		t.Fatalf("charged %q, want the resolved version's override 0.500000", got)
	}
}

// Org custom pricing in the listing follows tryOrgPricing's order: the listed
// id the caller sends wins over the upstream name, for a prefixed and an
// unprefixed Models entry alike.
func TestDecisionOrgCustomPricingListingMatchesCharge(t *testing.T) {
	for _, entry := range []string{"openrouter/typesafe/jev-1.13", "typesafe/jev-1.13"} {
		t.Run(entry, func(t *testing.T) {
			const id = "openrouter/typesafe/jev-1.13"
			provider := &tenant.ProviderConfig{APIKey: orgTypeSafeKey, APIFormat: "systemone", Enabled: true, Models: []string{entry}}
			org := &tenant.OrgConfig{
				Providers: map[string]*tenant.ProviderConfig{"openrouter": provider},
				CostTracking: &tenant.CostTrackingConfig{Enabled: true, CustomPricing: map[string]*tenant.CustomPricing{
					"typesafe/jev-1.13": {InputPerMTok: 0.7},
					id:                  {InputPerMTok: 0.9},
				}},
			}
			g := newDecisionGateway(t, decisionGatewayOptions{
				orgs: map[string]*tenant.OrgConfig{"org-a": org},
				config: func(cfg *config.Config, upstreamURL string) {
					cfg.Providers["openrouter"] = config.ProviderConfig{APIFormat: "systemone", BaseURL: upstreamURL, Models: []string{entry}}
				},
			})
			if got := listedDecisionPrice(t, g, keyByok, id); got != 0.9 {
				t.Fatalf("listing for %s = %v, want the listed id's 0.9", id, got)
			}
			if got := chargedDecisionCost(t, g, keyByok, id, "typesafe/jev-1.13-20260917"); got != "0.900000" {
				t.Fatalf("charged %q, want the listed 0.900000", got)
			}
			expectOnlyKey(t, g, orgTypeSafeKey, 1)
		})
	}
}
