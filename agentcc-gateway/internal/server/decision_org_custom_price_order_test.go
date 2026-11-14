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

// An org's custom decision prices are charged in one fixed order that does
// not depend on the form of the vendor's answer, and GET /v1/decisions/models
// lists the price a call of that entry is charged.
func TestDecisionOrgCustomPriceChargedAsListed(t *testing.T) {
	answers := map[string]string{
		"absent": `{"answers":{"resolved":{"type":"noul","noul":0.9}},"usage":{"input_tokens":1000000,"output_tokens":0}}`,
		"echoed": `{"model":"typesafe/jev-1.13","answers":{"resolved":{"type":"noul","noul":0.9}},"usage":{"input_tokens":1000000,"output_tokens":0}}`,
		"dated":  `{"model":"typesafe/jev-1.13-20260917","answers":{"resolved":{"type":"noul","noul":0.9}},"usage":{"input_tokens":1000000,"output_tokens":0}}`,
	}
	for entryName, entry := range map[string]string{
		"prefixed":   "openrouter/typesafe/jev-1.13",
		"unprefixed": "typesafe/jev-1.13",
	} {
		for form, answer := range answers {
			t.Run(entryName+"/"+form, func(t *testing.T) {
				org := &tenant.OrgConfig{
					Providers: map[string]*tenant.ProviderConfig{
						"openrouter": {APIKey: orgTypeSafeKey, APIFormat: "systemone", Enabled: true, Models: []string{entry}},
					},
					CostTracking: &tenant.CostTrackingConfig{Enabled: true, CustomPricing: map[string]*tenant.CustomPricing{
						"typesafe/jev-1.13":            {InputPerMTok: 0.7},
						"openrouter/typesafe/jev-1.13": {InputPerMTok: 0.9},
					}},
				}
				g := newDecisionGateway(t, decisionGatewayOptions{
					orgs: map[string]*tenant.OrgConfig{"org-a": org},
					config: func(cfg *config.Config, upstreamURL string) {
						// The org provider is layered onto this entry's upstream.
						cfg.Providers["openrouter"] = config.ProviderConfig{APIFormat: "systemone", BaseURL: upstreamURL}
					},
				})
				g.upstream.setReply(func(w http.ResponseWriter, _ *http.Request) {
					_, _ = io.WriteString(w, answer)
				})

				listed := g.do(t, http.MethodGet, "/v1/decisions/models", keyByok, "")
				var resp struct {
					Data []decisionModel `json:"data"`
				}
				if err := json.Unmarshal(listed.Body.Bytes(), &resp); listed.Code != http.StatusOK || err != nil || len(resp.Data) != 1 || resp.Data[0].Pricing == nil {
					t.Fatalf("listing %d: %s", listed.Code, listed.Body.String())
				}
				model := resp.Data[0]
				if model.ID != "openrouter/typesafe/jev-1.13" {
					t.Fatalf("listed id %q", model.ID)
				}

				body := fmt.Sprintf(`{"model":%q,"state":"synthetic","questions":{"resolved":{"type":"noul"}}}`, model.ID)
				rr := g.post(t, keyByok, body)
				if rr.Code != http.StatusOK {
					t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
				}
				want := fmt.Sprintf("%.6f", model.Pricing.InputPerMTok)
				if got := rr.Header().Get("x-agentcc-cost"); got != want {
					t.Errorf("charged %s for 1M input tokens; the listing shows %s", got, want)
				}
			})
		}
	}
}
