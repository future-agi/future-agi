package vendors_test

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credentials"
)

// Fixtures explicitly declare org client support; the checked-in ISO bundle
// has no clientSources and must not receive an implicit legacy exemption.
// Copy only the records we change, preserving directory GeneratedTools.
func withClientSources(t *testing.T, cat *manifest.Catalog, sources []string) *manifest.Catalog {
	t.Helper()
	raw, err := json.Marshal(map[string]any{"auth": map[string]any{"oauth2": map[string]any{"clientSources": sources}}, "x-gateway": map[string]any{"connectionEnablement": map[string]any{"schemaVersion": 1, "enabled": true, "operations": []string{"connect", "list_tools", "invoke"}}}})
	if err != nil {
		t.Fatal(err)
	}
	out := *cat
	out.Apps = make([]*manifest.App, 0, len(cat.Apps))
	for _, app := range cat.Apps {
		a := *app
		a.Providers = make([]*manifest.Provider, 0, len(app.Providers))
		for _, provider := range app.Providers {
			p := *provider
			if p.ID == "futureagi-rest" {
				if p.Auth.OAuth2 != nil {
					oauth := *p.Auth.OAuth2
					p.Auth.OAuth2 = &oauth
				}
				if p.XGateway != nil {
					gateway := *p.XGateway
					p.XGateway = &gateway
					p.XGateway.ConnectionEnablement = nil
				}
				// These legacy adapter tests use explicit synthetic enablement, not REGEN-012 Gmail availability.
				if err := json.Unmarshal(raw, &p); err != nil {
					t.Fatal(err)
				}
			}
			a.Providers = append(a.Providers, &p)
		}
		out.Apps = append(out.Apps, &a)
	}
	return &out
}

// Uses the real Registry -> Resolver -> synthetic token channel -> loopback
// MCP path. It needs TEST_REDIS_ADDR; no live provider is involved.
func TestK2ServedSetTokenBoundary(t *testing.T) {
	for _, item := range []struct{ app, adapter string }{
		{"google-docs", "google"}, {"google-drive", "google"},
		{"google-meet", "google"}, {"google-sheets", "google"},
		{"google-slides", "google"}, {"microsoft-excel", "microsoft"},
		{"microsoft-onedrive", "microsoft"}, {"microsoft-sharepoint", "microsoft"},
	} {
		for _, tc := range []struct {
			name    string
			sources []string
			admit   bool
		}{
			{"org", []string{"org"}, true},
			{"platform-only", []string{"platform"}, false},
			{"missing", nil, false},
			{"unknown", []string{"org", "unknown"}, false},
		} {
			t.Run(item.app+"/"+tc.name, func(t *testing.T) {
				f := newFixture(t, item.adapter, item.app, func(f *fixture) {
					f.catalog = withClientSources(t, f.catalog, tc.sources)
				})
				tok := f.give(t, "org-a", "u1")
				answer := f.call(t, userCaller("org-a", "u1"))
				if tc.admit {
					connected(t, answer)
					equal(t, f.fingerprints(), []string{bearerHash(tok)})
					equal(t, f.cp.fetched(), []credentials.Key{key("org-a", "u1")})
				} else {
					denied(t, answer, mcp.CodePolicyUnavailable)
					equal(t, len(f.cp.fetched()), 0)
					equal(t, len(f.fingerprints()), 0)
				}
			})
		}
	}
}
