package server

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestKeylessGenericDecisionProviderRoutesAndLists(t *testing.T) {
	for _, format := range []string{"systemone", "typesafe"} {
		t.Run(format, func(t *testing.T) {
			org := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
				"self-hosted": {APIFormat: format, BaseURL: "https://203.0.113.7", Models: []string{"judge-1"}, Enabled: true},
			}}
			g := newDecisionGateway(t, decisionGatewayOptions{orgs: map[string]*tenant.OrgConfig{"org-a": org}})
			h := g.srv.handlers
			if got := h.orgDecisionModels(org); len(got) != 1 || got[0].ID != "self-hosted/judge-1" || got[0].Format != "systemone" {
				t.Fatalf("keyless endpoint listing = %+v", got)
			}
			target, err := h.orgDecisionTarget(&models.RequestContext{}, http.Header{},
				&models.DecisionRequest{Model: "self-hosted/judge-1", Format: models.DecisionFormatSystemOne}, "org-a", org)
			if err != nil || target == nil || target.providerID != "self-hosted" || target.upstreamModel != "judge-1" {
				t.Fatalf("keyless endpoint target = %+v, error = %v", target, err)
			}
			expectNoCalls(t, g)
		})
	}
}

func TestKeylessDecisionProvidersCannotBypassVendorKeysOrURLChecks(t *testing.T) {
	for _, tt := range []struct {
		name, id, format, base string
		enabled                bool
	}{
		{"disabled", "custom", "systemone", "https://203.0.113.7", false},
		{"no endpoint", "custom", "systemone", "", true},
		{"invalid endpoint", "custom", "systemone", "ftp://203.0.113.7", true},
		{"loopback", "custom", "systemone", "http://127.0.0.1:9", true},
		{"metadata", "custom", "systemone", "http://169.254.169.254", true},
		{"private", "custom", "systemone", "http://10.0.0.1", true},
		{"typesafe ID", "typesafe-custom", "systemone", "https://203.0.113.7", true},
		{"typesafe host", "custom", "systemone", "https://api.typesafe.ai", true},
		{"perplexity ID", "perplexity-custom", "systemone", "https://203.0.113.7", true},
		{"perplexity host", "custom", "systemone", "https://api.perplexity.ai", true},
		{"openai", "openai", "openai", "https://203.0.113.7", true},
	} {
		t.Run(tt.name, func(t *testing.T) {
			org := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
				tt.id: {APIFormat: tt.format, BaseURL: tt.base, Models: []string{"judge-1"}, Enabled: tt.enabled},
			}}
			g := newDecisionGateway(t, decisionGatewayOptions{orgs: map[string]*tenant.OrgConfig{"org-a": org}})
			h := g.srv.handlers
			if got := h.orgDecisionModels(org); len(got) != 0 {
				t.Fatalf("unusable keyless provider was listed: %+v", got)
			}
			_, err := h.orgDecisionTarget(&models.RequestContext{}, http.Header{},
				&models.DecisionRequest{Model: tt.id + "/judge-1", Format: models.DecisionFormatSystemOne}, "org-a", org)
			if err == nil {
				t.Fatal("unusable keyless provider was routed")
			}
			expectNoCalls(t, g)
		})
	}
}
