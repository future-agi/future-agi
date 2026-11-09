package emit

import (
	"fmt"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// The tests below build catalogs as values, not files, so the emitters'
// own guards are what refuse a bad catalog: the loader's L0 would refuse
// most of these first.

func oauthProvider(app, id, kind, url string) *manifest.Provider {
	p := &manifest.Provider{
		App: app, ID: id, Version: "1.0.0", Kind: kind, Publisher: "vendor",
		VendorStatus: manifest.VendorStatus{Label: "ga"},
		Transport:    manifest.Transport{Kind: "http", URL: url, Variant: "streamable"},
		Hosted:       true, AccountModes: []string{"agent", "user"},
		Auth: manifest.Auth{Kind: manifest.AuthOAuth2, OAuth2: &manifest.OAuth2{
			AuthorizeURL: "https://auth." + app + ".example.com/authorize", TokenURL: "https://auth." + app + ".example.com/token",
			Scopes: []string{"read"}, PKCE: true, TokenBody: "form", ClientAuth: "body",
		}},
		Tools:     manifest.Tools{Source: manifest.ToolsUpstreamMCP, DefaultPosture: manifest.PostureAsk},
		Pricing:   manifest.Pricing{Model: "free"},
		Quality:   manifest.Quality{Tier: "listed"},
		Lifecycle: manifest.ProviderLifecycle{Status: "active"},
	}
	if kind == manifest.KindREST {
		p.Publisher = "futureagi"
		p.Tools.Source = manifest.ToolsOperations
	}
	return p
}

func appWith(id string, providers ...*manifest.Provider) *manifest.App {
	return &manifest.App{
		ID: id, Version: "1.0.0", Name: strings.ToUpper(id[:1]) + id[1:], Description: "What " + id + " does.",
		Vendor: manifest.Vendor{Name: id, Domains: []string{id + ".example.com"}}, EECategory: "developer", Group: "other",
		Categories: []string{"developer"}, DocsURL: "https://" + id + ".example.com/docs",
		DefaultProvider: providers[0].ID, Lifecycle: manifest.Lifecycle{Status: "active"}, Providers: providers,
	}
}

// vendorApps is n apps, each with a vendor MCP server at a fixed URL.
func vendorApps(n int) *manifest.Catalog {
	cat := &manifest.Catalog{}
	for i := 0; i < n; i++ {
		id := fmt.Sprintf("app%02d", i)
		cat.Apps = append(cat.Apps, appWith(id, oauthProvider(id, "mcp", manifest.KindVendorMCP, "https://mcp."+id+".example.com/mcp")))
	}
	return cat
}

var testMeta = Meta{Version: "2026.10.20.0", SHA256: strings.Repeat("ab", 32)}

func wantError(t *testing.T, err error, contains string) {
	t.Helper()
	if err == nil || !strings.Contains(err.Error(), contains) {
		t.Fatalf("got %v; want an error containing %q", err, contains)
	}
}
