package server

import (
	"encoding/json"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The console has a session, not a customer gateway key. Its authenticated
// backend must get the same org catalog without minting a key on a read.
func TestAdminDecisionModelsMatchesPublicOrgListing(t *testing.T) {
	org := defaultOrgA()
	org.Providers["perplexity"] = &tenant.ProviderConfig{
		APIKey: "synthetic-perplexity", APIFormat: "systemone",
		BaseURL: "https://api.perplexity.ai",
		Models:  []string{"pplx-decider-v1.1-27b"}, Enabled: true,
	}
	g := newDecisionGateway(t, decisionGatewayOptions{orgs: map[string]*tenant.OrgConfig{
		"org-a": org,
		"org-b": {Providers: map[string]*tenant.ProviderConfig{
			"typesafe": {APIKey: "synthetic-org-b", APIFormat: "systemone", Models: []string{"jev-latest"}, Enabled: true},
		}},
	}})
	for _, tc := range []struct{ org, key string }{
		{"org-a", keyByok}, {"org-b", keyByokOrgB}, {"org-empty", keyByokNoCred},
	} {
		t.Run(tc.org, func(t *testing.T) {
			public := g.do(t, http.MethodGet, "/v1/decisions/models", tc.key, "")
			admin := g.do(t, http.MethodGet, "/-/orgs/"+tc.org+"/decisions/models?org_id=other-org", decisionAdminToken, "")
			if public.Code != http.StatusOK || admin.Code != http.StatusOK {
				t.Fatalf("public status %d, admin status %d", public.Code, admin.Code)
			}
			if public.Body.String() != admin.Body.String() {
				t.Fatalf("admin listing differs from the org's public listing: %s != %s", admin.Body.String(), public.Body.String())
			}
			if admin.Header().Get("Cache-Control") != "no-store" {
				t.Fatal("org catalog must not be shared through caches")
			}
			if tc.org == "org-a" {
				var listing struct {
					Data []decisionModel `json:"data"`
				}
				if err := json.Unmarshal(admin.Body.Bytes(), &listing); err != nil {
					t.Fatal(err)
				}
				seen := map[string]bool{}
				for _, model := range listing.Data {
					seen[model.ID] = true
				}
				for _, id := range []string{"typesafe/jev-1.13.0", "perplexity/pplx-decider-v1.1-27b"} {
					if !seen[id] {
						t.Fatalf("missing canonical model %s", id)
					}
				}
			}
		})
	}
	expectNoCalls(t, g)
}

func TestAdminDecisionModelsRequiresAdmin(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{})
	for _, bearer := range []string{"", keyByok, keyInternal} {
		rr := g.do(t, http.MethodGet, "/-/orgs/org-a/decisions/models", bearer, "")
		if rr.Code != http.StatusUnauthorized {
			t.Fatalf("non-admin listing status = %d", rr.Code)
		}
	}
	expectNoCalls(t, g)
}

func TestAdminDecisionModelsDoesNotUseNodeProviders(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{authOff: true})
	rr := g.do(t, http.MethodGet, "/-/orgs/org-empty/decisions/models", decisionAdminToken, "")
	if rr.Code != http.StatusOK {
		t.Fatalf("empty org status = %d", rr.Code)
	}
	var listing struct {
		Object string          `json:"object"`
		Data   []decisionModel `json:"data"`
	}
	if err := json.Unmarshal(rr.Body.Bytes(), &listing); err != nil {
		t.Fatal(err)
	}
	if listing.Object != "list" || listing.Data == nil || len(listing.Data) != 0 {
		t.Fatalf("empty org fell back to node providers: %s", rr.Body.String())
	}
	expectNoCalls(t, g)
}
