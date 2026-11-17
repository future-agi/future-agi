package manifest

import (
	"os"
	"slices"
	"testing"
)

// TestRepoCatalogListsBatch3Connectors checks the fixed hosted endpoints and
// single-header credentials documented by the vendors, together with their
// transport sources and normal OSS listing. Mixpanel uses the US endpoint and
// the beta service-account authentication path; no credential is stored here.
func TestRepoCatalogListsBatch3Connectors(t *testing.T) {
	cat, err := validateRepoCatalog(os.DirFS(repoCatalogRoot))
	if err != nil {
		t.Fatalf("catalog/connectors is not valid:\n%v", err)
	}
	for _, c := range []struct {
		app, provider, kind, transport string
		url, auth                      string
		header, prefix                 string
		ossID, source                  string
		label, help                    string
	}{
		{
			app: "mixpanel", provider: "api-key", kind: KindVendorMCP, transport: "http",
			url:  "https://mcp.mixpanel.com/mcp",
			auth: AuthAPIKey, header: "Authorization", prefix: "Bearer Basic ",
			ossID:  "mixpanel",
			source: "https://docs.mixpanel.com/docs/mcp",
			label:  "base64(service-account username:secret)",
			help:   "Enter base64(service-account username:secret) only; the gateway adds 'Bearer Basic '. The AI assistant can read and write Mixpanel data on your behalf. Data is sent to the connected AI provider; review its data policies. Free plans may have Query API access limits.",
		},
	} {
		t.Run(c.app+"/"+c.provider, func(t *testing.T) {
			app := cat.App(c.app)
			if app == nil {
				t.Fatalf("no app %s", c.app)
			}
			p := app.Provider(c.provider)
			if p == nil {
				t.Fatalf("app %s has no provider %s", c.app, c.provider)
			}
			if p.Kind != c.kind || p.Publisher != "vendor" || p.Transport.Kind != c.transport || p.Auth.Kind != c.auth {
				t.Errorf("kind %s publisher %s transport %s auth %s; want %s vendor %s %s",
					p.Kind, p.Publisher, p.Transport.Kind, p.Auth.Kind, c.kind, c.transport, c.auth)
			}
			if p.Trusted {
				t.Error("a new vendor record is not trusted before a maintainer review")
			}
			if !p.Hosted {
				t.Error("a vendor HTTP endpoint must be hosted")
			}
			if p.Transport.URL != c.url {
				t.Errorf("url %q; want %q", p.Transport.URL, c.url)
			}
			if len(p.Auth.CredentialParts) != 1 {
				t.Fatalf("credential parts count %d; want exactly one", len(p.Auth.CredentialParts))
			}
			part := p.Auth.CredentialParts[0]
			if part.In != "header" || part.Name != c.header || part.Prefix != c.prefix || !part.Secret || part.Value != "" {
				t.Errorf("want a secret %s header with prefix %q and no value", c.header, c.prefix)
			}
			if part.Label != c.label {
				t.Errorf("credential label %q; want %q", part.Label, c.label)
			}
			if part.Help != c.help {
				t.Errorf("credential help %q; want %q", part.Help, c.help)
			}
			if part.HelpURL != c.source {
				t.Errorf("credential help URL %q; want %q", part.HelpURL, c.source)
			}
			if !slices.ContainsFunc(p.Sources, func(s Source) bool { return s.Fact == "auth.credentialParts" && s.URL == c.source }) {
				t.Errorf("no source cites %s for auth.credentialParts", c.source)
			}
			if !slices.ContainsFunc(p.Sources, func(s Source) bool { return s.Fact == "transport.url" && s.URL == c.source }) {
				t.Errorf("no source cites %s for transport.url", c.source)
			}
			if !slices.ContainsFunc(OSSListed(app), func(s OSSServer) bool { return s.ID == c.ossID && s.Provider == p }) {
				t.Errorf("OSS does not list %s as %s", c.app+"/"+c.provider, c.ossID)
			}
		})
	}
}
