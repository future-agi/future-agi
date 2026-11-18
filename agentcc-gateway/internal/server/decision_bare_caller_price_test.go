package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A caller may name a decision model by the listed, provider-qualified id or
// by the bare name the provider's Models entry configures. Either way, with
// both names priced in the org's custom pricing, the call is charged the
// price GET /v1/decisions/models lists, whatever form the vendor's answer
// takes.
func TestDecisionBareCallerChargedTheListedOrgPrice(t *testing.T) {
	type setup struct {
		provider string
		entry    string // the provider's Models entry, the name a caller may send
		bare     string // the name sent upstream
	}
	setups := map[string]setup{
		"typesafe":              {provider: "typesafe", entry: "jev-1.13.0", bare: "jev-1.13.0"},
		"openrouter/unprefixed": {provider: "openrouter", entry: "typesafe/jev-1.13", bare: "typesafe/jev-1.13"},
		"openrouter/prefixed":   {provider: "openrouter", entry: "openrouter/typesafe/jev-1.13", bare: "typesafe/jev-1.13"},
		// The upstream name itself starts with the provider's prefix.
		"openrouter/doubled": {provider: "openrouter", entry: "openrouter/openrouter/auto", bare: "openrouter/auto"},
	}
	for setupName, s := range setups {
		id := s.provider + "/" + s.bare
		answers := map[string]string{
			"absent": `{"answers":{"resolved":{"type":"noul","noul":0.9}},"usage":{"input_tokens":1000000,"output_tokens":0}}`,
			"echoed": fmt.Sprintf(`{"model":%q,"answers":{"resolved":{"type":"noul","noul":0.9}},"usage":{"input_tokens":1000000,"output_tokens":0}}`, s.bare),
			"dated":  fmt.Sprintf(`{"model":%q,"answers":{"resolved":{"type":"noul","noul":0.9}},"usage":{"input_tokens":1000000,"output_tokens":0}}`, s.bare+"-20260917"),
		}
		for _, caller := range []string{"bare", "listed"} {
			for form, answer := range answers {
				t.Run(setupName+"/"+caller+"/"+form, func(t *testing.T) {
					org := &tenant.OrgConfig{
						Providers: map[string]*tenant.ProviderConfig{
							s.provider: {APIKey: orgTypeSafeKey, APIFormat: "systemone", Enabled: true, Models: []string{s.entry}},
						},
						CostTracking: &tenant.CostTrackingConfig{Enabled: true, CustomPricing: map[string]*tenant.CustomPricing{
							s.bare: {InputPerMTok: 0.7},
							id:     {InputPerMTok: 0.9},
						}},
					}
					g := newDecisionGateway(t, decisionGatewayOptions{
						orgs: map[string]*tenant.OrgConfig{"org-a": org},
						config: func(cfg *config.Config, upstreamURL string) {
							cfg.Providers[s.provider] = config.ProviderConfig{APIFormat: "systemone", BaseURL: upstreamURL}
						},
					})
					g.upstream.setReply(func(w http.ResponseWriter, _ *http.Request) {
						_, _ = io.WriteString(w, answer)
					})

					listed := g.do(t, http.MethodGet, "/v1/decisions/models", keyByok, "")
					var resp struct {
						Data []decisionModel `json:"data"`
					}
					if err := json.Unmarshal(listed.Body.Bytes(), &resp); listed.Code != http.StatusOK || err != nil {
						t.Fatalf("listing %d: %s", listed.Code, listed.Body.String())
					}
					var model *decisionModel
					for i := range resp.Data {
						if resp.Data[i].ID == id {
							model = &resp.Data[i]
						}
					}
					if model == nil || model.Pricing == nil {
						t.Fatalf("listing has no priced %s: %s", id, listed.Body.String())
					}
					if model.Pricing.InputPerMTok != 0.9 {
						t.Fatalf("listed %v for %s, want the provider-qualified id's 0.9", model.Pricing.InputPerMTok, id)
					}

					name := id
					if caller == "bare" {
						name = s.entry // for a prefixed entry, that is the listed id
					}
					body := fmt.Sprintf(`{"model":%q,"state":"synthetic","questions":{"resolved":{"type":"noul"}}}`, name)
					rr := g.post(t, keyByok, body)
					if rr.Code != http.StatusOK {
						t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
					}
					want := fmt.Sprintf("%.6f", model.Pricing.InputPerMTok)
					if got := rr.Header().Get("x-agentcc-cost"); got != want {
						t.Errorf("%q charged %s for 1M input tokens; the listing shows %s", name, got, want)
					}
				})
			}
		}
	}
}
