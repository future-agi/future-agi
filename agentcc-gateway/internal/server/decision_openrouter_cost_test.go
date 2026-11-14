package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"testing"

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
