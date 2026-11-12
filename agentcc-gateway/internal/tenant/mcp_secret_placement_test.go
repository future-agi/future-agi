package tenant

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
)

// placementCatalog is a credtemplate.Catalog of providers keyed app/provider.
type placementCatalog map[string]struct {
	version string
	parts   []credtemplate.Declared
}

func (c placementCatalog) CredentialParts(app, provider string) (string, []credtemplate.Declared, bool) {
	p, ok := c[app+"/"+provider]
	return p.version, p.parts, ok
}

// The gateway sends a pushed MCP secret only in the header
// MCPSecretHeader names, so UnappliedMCPSecret refuses every other placement
// and every name that is not that header.

func TestMCPSecretHeader(t *testing.T) {
	for _, tc := range []struct {
		sec       MCPCredentialSecret
		reference string
		want      string
	}{
		{MCPCredentialSecret{Type: "bearer", Header: "X-Api-Key"}, "", "Authorization"},
		{MCPCredentialSecret{Type: "api_key"}, "", "Authorization"},
		{MCPCredentialSecret{Type: "api_key", Header: "X-Api-Key"}, "X-Other", "X-Api-Key"},
		{MCPCredentialSecret{Type: "api_key"}, "X-Other", "X-Other"},
	} {
		if got := MCPSecretHeader(&tc.sec, tc.reference); got != tc.want {
			t.Errorf("MCPSecretHeader(%+v, %q) = %q, want %q", tc.sec, tc.reference, got, tc.want)
		}
	}
}

func TestUnappliedMCPSecret(t *testing.T) {
	const value = "test-placement-value-not-real"
	for _, tc := range []struct {
		name      string
		sec       *MCPCredentialSecret
		reference string
		field     string
	}{
		{"none", nil, "", ""},
		{"today's push form", &MCPCredentialSecret{Type: "bearer", Header: "Authorization", Value: value}, "", ""},
		{"api key header", &MCPCredentialSecret{Type: "api_key", Header: "x-api-key", Value: value}, "", ""},
		{"in header, name its header", &MCPCredentialSecret{Type: "api_key", Header: "x-api-key", In: "header", Name: "X-API-KEY", Value: value}, "", ""},
		{"in header, no name", &MCPCredentialSecret{Type: "api_key", Header: "x-api-key", In: "header", Value: value}, "", ""},
		{"name the reference header", &MCPCredentialSecret{Type: "api_key", In: "header", Name: "x-api-key", Value: value}, "X-Api-Key", ""},
		{"bearer named Authorization", &MCPCredentialSecret{Type: "bearer", In: "header", Name: "authorization", Value: value}, "", ""},
		{"parts", &MCPCredentialSecret{Type: "api_key", Header: "DD-API-KEY", Value: value,
			Parts: []MCPCredentialSecretPart{{In: "header", Name: "DD-APPLICATION-KEY", Value: value}}}, "", "parts"},
		{"parts before in", &MCPCredentialSecret{Type: "api_key", In: "query", Name: "api_key", Value: value,
			Parts: []MCPCredentialSecretPart{{In: "query", Name: "app_key", Value: value}}}, "", "parts"},
		{"query", &MCPCredentialSecret{Type: "api_key", In: "query", Name: "api_key", Value: value}, "", "in"},
		{"url_variable", &MCPCredentialSecret{Type: "api_key", In: "url_variable", Name: "token", Value: value}, "", "in"},
		{"outside the enum", &MCPCredentialSecret{Type: "api_key", In: "cookie", Name: "session", Value: value}, "", "in"},
		{"query wins over a name check", &MCPCredentialSecret{Type: "api_key", In: "query", Name: "x", Header: "y", Value: value}, "", "in"},
		{"name is not the header", &MCPCredentialSecret{Type: "api_key", In: "header", Name: "X-Api-Key", Header: "X-Other", Value: value}, "", "name"},
		{"name with no header is bearer", &MCPCredentialSecret{Type: "api_key", In: "header", Name: "X-Api-Key", Value: value}, "", "name"},
		{"bearer in a named header", &MCPCredentialSecret{Type: "bearer", In: "header", Name: "X-Api-Key", Header: "X-Api-Key", Value: value}, "", "name"},
		{"name without in", &MCPCredentialSecret{Type: "api_key", Name: "X-Api-Key", Header: "X-Other", Value: value}, "", "name"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			field, reason := UnappliedMCPSecret(tc.sec, tc.reference)
			if field != tc.field {
				t.Fatalf("field = %q (%s), want %q", field, reason, tc.field)
			}
			if (field == "") != (reason == "") || strings.Contains(reason, value) {
				t.Fatalf("reason %q for field %q: want one exactly when refused, never the value", reason, field)
			}
		})
	}
}

func TestUnappliedMCPCredentialPlacement(t *testing.T) {
	secret := func(in string) *MCPCredentialSecret {
		return &MCPCredentialSecret{Type: "api_key", In: in, Name: "k", Value: "test-value-not-real"}
	}
	server := func(url string, sec *MCPCredentialSecret) *MCPServerOrgConfig {
		return &MCPServerOrgConfig{URL: url, Credential: &MCPServerCredential{Secret: sec}}
	}
	cfg := &OrgConfig{MCP: &MCPOrgConfig{
		Servers: map[string]*MCPServerOrgConfig{
			// Query and url_variable placements and parts are applied
			// by credential templates, so the PUT accepts them when the
			// server's URL can carry them.
			"q":  server("https://q.example.com/mcp", secret("query")),
			"v":  server("https://v.example.com/mcp/{k}", secret("url_variable")),
			"p":  server("https://p.example.com/mcp", &MCPCredentialSecret{Type: "api_key", In: "header", Name: "A", Value: "test-value-not-real", Parts: []MCPCredentialSecretPart{{In: "query", Name: "b", Value: "test-value-not-real"}}}),
			"c":  nil,
			"d":  {},
			"cr": {URL: "https://cr.example.com/mcp", CatalogRef: "close/close-mcp@1.0.0", Credential: &MCPServerCredential{Secret: &MCPCredentialSecret{Type: "bearer", Value: "test-value-not-real"}}},
		},
		ManagedTools: &MCPManagedToolsConfig{Credentials: map[string]*MCPManagedToolCredential{
			"fagi.web": {Secret: secret("query")},
			"fagi.nil": nil,
		}},
	}}
	// "cr" names a catalog provider that declares no parts: its pushed
	// secret is placed as it states. With no catalog loaded it is refused,
	// naming its catalog_ref, as the MCP pool refuses it.
	cat := placementCatalog{"close/close-mcp": {"1.2.0", nil}}
	managed := cfg.MCP.ManagedTools
	cfg.MCP.ManagedTools = nil
	if err := cfg.UnappliedMCPCredentialPlacement(nil); err == nil || err.Field != "mcp.servers.cr.catalog_ref" {
		t.Fatalf("no catalog: refusal = %v, want mcp.servers.cr.catalog_ref", err)
	}
	cfg.MCP.ManagedTools = managed
	// A managed tool server still sends a secret only in a header.
	if err := cfg.UnappliedMCPCredentialPlacement(cat); err == nil || err.Field != "mcp.managed_tools.credentials.fagi.web.secret.in" ||
		!strings.HasPrefix(err.Error(), err.Field+": ") {
		t.Fatalf("managed refusal = %v", err)
	}
	cfg.MCP.ManagedTools = nil
	if err := cfg.UnappliedMCPCredentialPlacement(cat); err != nil {
		t.Fatalf("an applied placement was refused: %v", err)
	}
	// Servers in id order; a template refusal names the secret's field, or
	// the server's url.
	cfg.MCP.Servers["b"] = server("https://b.example.com/mcp", secret("url_variable"))
	cfg.MCP.Servers["a"] = server("https://a.example.com/mcp", secret("cookie"))
	if err := cfg.UnappliedMCPCredentialPlacement(cat); err == nil || err.Field != "mcp.servers.a.credential.secret.in" {
		t.Fatalf("first refusal = %v, want mcp.servers.a.credential.secret.in", err)
	}
	delete(cfg.MCP.Servers, "a")
	if err := cfg.UnappliedMCPCredentialPlacement(cat); err == nil || err.Field != "mcp.servers.b.credential.secret.name" {
		t.Fatalf("refusal = %v, want mcp.servers.b.credential.secret.name (the URL has no {k})", err)
	}
	cfg.MCP.Servers["b"] = server("https://b.example.com/mcp/{other}", secret("query"))
	if err := cfg.UnappliedMCPCredentialPlacement(cat); err == nil || err.Field != "mcp.servers.b.url" {
		t.Fatalf("refusal = %v, want mcp.servers.b.url (a placeholder nothing fills)", err)
	}
	for _, c := range []*OrgConfig{{}, nil} {
		if err := c.UnappliedMCPCredentialPlacement(nil); err != nil {
			t.Fatalf("refused %v", err)
		}
	}
}
