package manifest

import (
	"strings"
	"testing"
	"testing/fstest"
)

// The fixture's Linear provider: oauth2 with PKCE, a pre-registered client
// or dynamic registration, vendor domain linear.app and GitHub org linear.
const pcLinear = "apps/linear/providers/linear-mcp.yaml"

// pcFact is an x-omega.publicClient block whose one evidence item is url.
// The fixture's facts are illustrative; the catalog's own cite saved pages.
func pcFact(supported, url string) string {
	return "x-omega:\n  publicClient:\n    supported: " + supported + "\n    evidence:\n" +
		"      - url: " + url + "\n        fetchedAt: 2026-10-22\n        sha256: " + strings.Repeat("ab", 32) + "\n" +
		"        quote: \"Illustrative: a public client sends client_id and code_verifier, no secret.\"\n"
}

// withFact gives the fixture's Linear provider the fact.
func withFact(t *testing.T, fsys fstest.MapFS, supported, url string) {
	t.Helper()
	edit(t, fsys, pcLinear, "x-gateway: { timeoutMs: 30000 }\n", "x-gateway: { timeoutMs: 30000 }\n"+pcFact(supported, url))
}

// TestPublicClientFact: x-omega.publicClient states whether a person can
// sign in to the provider the way OSS Omega's `omega mcp login` does: with a
// client id registered with the vendor beforehand, no client secret, PKCE
// and a loopback redirect (OSS:packages/cli/src/chat/mcp-oauth.ts). OSS's
// registry lists an OAuth server only when the fact says so (the OSS
// rule), so a true value must be one the record can back: an oauth2 provider
// whose endpoints omega mcp login can be given, with PKCE, that takes a
// pre-registered client, proven by the vendor's own pages.
func TestPublicClientFact(t *testing.T) {
	t.Run("a public client proven by the vendor's page", func(t *testing.T) {
		fsys := fixture(t)
		withFact(t, fsys, "true", "https://linear.app/developers/oauth-2-0-authentication")
		p := mustLoad(t, fsys).App("linear").Provider("linear-mcp")
		pc := p.XOmega.PublicClient
		if pc == nil || !pc.Supported || len(pc.Evidence) != 1 || pc.Evidence[0].SHA256 != strings.Repeat("ab", 32) {
			t.Fatalf("publicClient = %+v", pc)
		}
	})
	t.Run("the vendor's GitHub org is the vendor's", func(t *testing.T) {
		fsys := fixture(t)
		withFact(t, fsys, "true", "https://github.com/linear/linear/blob/main/README.md")
		mustLoad(t, fsys)
	})
	t.Run("no public client, stated from any page", func(t *testing.T) {
		fsys := fixture(t)
		withFact(t, fsys, "false", "https://example.org/a-blog-post")
		if pc := mustLoad(t, fsys).App("linear").Provider("linear-mcp").XOmega.PublicClient; pc == nil || pc.Supported {
			t.Fatalf("publicClient = %+v", pc)
		}
	})
	for _, tc := range []struct {
		name  string
		setup func(t *testing.T, fsys fstest.MapFS)
		want  string
	}{
		{"a page that is not the vendor's", func(t *testing.T, fsys fstest.MapFS) {
			withFact(t, fsys, "true", "https://example.org/linear-oauth")
		}, "/x-omega/publicClient/evidence/0/url: example.org is not the vendor's (linear.app, github.com/linear): a public client is proven only by the vendor's own page"},
		{"another GitHub org", func(t *testing.T, fsys fstest.MapFS) {
			withFact(t, fsys, "true", "https://github.com/someone/linear-notes")
		}, "/x-omega/publicClient/evidence/0/url: github.com/someone is not the vendor's (linear.app, github.com/linear)"},
		{"a look-alike host", func(t *testing.T, fsys fstest.MapFS) {
			withFact(t, fsys, "true", "https://notlinear.app/oauth")
		}, "notlinear.app is not the vendor's"},
		{"no PKCE", func(t *testing.T, fsys fstest.MapFS) {
			edit(t, fsys, pcLinear, "    pkce: true\n", "    pkce: false\n")
			withFact(t, fsys, "true", "https://linear.app/developers/oauth-2-0-authentication")
		}, "/x-omega/publicClient/supported: omega mcp login always sends PKCE (S256), and auth.oauth2.pkce is false"},
		{"only self-registering clients", func(t *testing.T, fsys fstest.MapFS) {
			edit(t, fsys, pcLinear, "registration: [preregistered, dcr]\n", "registration: [cimd, dcr]\n")
			withFact(t, fsys, "true", "https://linear.app/developers/oauth-2-0-authentication")
		}, "/x-omega/publicClient/supported: a public client is registered beforehand, and auth.oauth2.registration names no preregistered client"},
		{"registration not declared", func(t *testing.T, fsys fstest.MapFS) {
			edit(t, fsys, pcLinear, "    registration: [preregistered, dcr]\n", "")
			withFact(t, fsys, "true", "https://linear.app/developers/oauth-2-0-authentication")
		}, "auth.oauth2.registration names no preregistered client"},
		{"not an OAuth provider", func(t *testing.T, fsys fstest.MapFS) {
			edit(t, fsys, "apps/close/providers/close-mcp.yaml", "lifecycle: { status: active }\n",
				"lifecycle: { status: active }\n"+pcFact("false", "https://developer.close.com/mcp"))
		}, "/x-omega/publicClient: only an OAuth provider (auth.kind oauth2 or mcp_oauth) has a public client; this one is api_key"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			fsys := fixture(t)
			tc.setup(t, fsys)
			wantLoadError(t, fsys, tc.want)
		})
	}
}

// TestPublicClientFactOnMCPOAuth: an mcp_oauth record declares no authorize
// or token URL, which omega mcp login needs typed in, so it may state that
// no public client exists but never that one does.
func TestPublicClientFactOnMCPOAuth(t *testing.T) {
	app := &App{ID: "acme", Vendor: Vendor{Name: "Acme", Domains: []string{"acme.example.com"}}}
	p := &Provider{App: "acme", ID: "mcp", Auth: Auth{Kind: AuthMCPOAuth}, XOmega: &ProviderXOmega{PublicClient: &PublicClient{
		Supported: true,
		Evidence:  []PublicClientEvidence{{URL: "https://acme.example.com/docs/oauth", FetchedAt: "2026-10-22", SHA256: strings.Repeat("0", 64), Quote: "q"}},
	}}}
	errs := checkPublicClient(app, p)
	if len(errs) != 1 || !strings.Contains(errs[0].Error(),
		"/x-omega/publicClient/supported: omega mcp login needs the authorize and token URLs typed in, and an mcp_oauth record declares none; declare auth.kind oauth2 with them, or state supported: false") {
		t.Fatalf("errors = %v", errs)
	}
	p.XOmega.PublicClient.Supported = false
	if errs := checkPublicClient(app, p); len(errs) != 0 {
		t.Fatalf("supported: false on mcp_oauth: %v", errs)
	}
}

// TestVendorOwnedURL: a page is the vendor's when its host is one of the
// app's vendor domains or under one, or it is on github.com under one of the
// app's GitHub orgs.
func TestVendorOwnedURL(t *testing.T) {
	app := &App{Vendor: Vendor{Domains: []string{"airtable.com"}, GitHubOrgs: []string{"Airtable"}}}
	for url, want := range map[string]bool{
		"https://airtable.com/developers/web/api/oauth-reference":         true,
		"https://mcp.airtable.com/.well-known/oauth-authorization-server": true,
		"https://github.com/airtable/airtable.js":                         true, // orgs match case-insensitively
		"https://github.com/airtable":                                     true,
		"https://github.com/airtableX/repo":                               false,
		"https://github.com/":                                             false,
		"https://evilairtable.com/oauth":                                  false,
		"https://airtable.com.evil.example/oauth":                         false,
		"https://gist.github.com/airtable/1":                              false,
		"https://example.org/airtable.com":                                false,
	} {
		if got := VendorOwned(app, url); got != want {
			t.Errorf("VendorOwned(%s) = %v, want %v", url, got, want)
		}
	}
}
