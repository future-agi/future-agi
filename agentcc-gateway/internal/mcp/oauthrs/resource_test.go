package oauthrs

import (
	"encoding/json"
	"strings"
	"testing"
)

// The test deployment's addresses. Every value is a placeholder.
const (
	testPublicURL = "https://gw.example.test"
	testIssuer    = "https://app.example.test/agentcc/mcp-oauth"
)

func newTestResource(t *testing.T) *Resource {
	t.Helper()
	res, err := NewResource(Config{PublicURL: testPublicURL, Issuer: testIssuer})
	if err != nil {
		t.Fatalf("NewResource: %v", err)
	}
	return res
}

// Every virtual server is its own protected resource: its metadata names its
// own canonical address and the configured authorization server, and its
// metadata address is the RFC 9728 well-known form of that address.
func TestPRMPerVirtualServer(t *testing.T) {
	res := newTestResource(t)
	cases := []struct {
		vs, resource, metadata string
	}{
		{"", "https://gw.example.test/mcp", "https://gw.example.test/.well-known/oauth-protected-resource/mcp"},
		{"vs_A", "https://gw.example.test/mcp/v/vs_A", "https://gw.example.test/.well-known/oauth-protected-resource/mcp/v/vs_A"},
		{"vs_B", "https://gw.example.test/mcp/v/vs_B", "https://gw.example.test/.well-known/oauth-protected-resource/mcp/v/vs_B"},
	}
	for _, tc := range cases {
		if got := res.ResourceURI(tc.vs); got != tc.resource {
			t.Fatalf("ResourceURI(%q) = %q, want %q", tc.vs, got, tc.resource)
		}
		if got := res.MetadataURL(tc.vs); got != tc.metadata {
			t.Fatalf("MetadataURL(%q) = %q, want %q", tc.vs, got, tc.metadata)
		}
		raw, err := json.Marshal(res.Metadata(tc.vs))
		if err != nil {
			t.Fatal(err)
		}
		var doc map[string]any
		if err := json.Unmarshal(raw, &doc); err != nil {
			t.Fatal(err)
		}
		if doc["resource"] != tc.resource {
			t.Fatalf("metadata resource %v, want %q", doc["resource"], tc.resource)
		}
		if as, _ := doc["authorization_servers"].([]any); len(as) != 1 || as[0] != testIssuer {
			t.Fatalf("authorization_servers %v, want [%q]", doc["authorization_servers"], testIssuer)
		}
		if s, _ := doc["scopes_supported"].([]any); len(s) != 3 || s[0] != ScopeTools || s[1] != ScopeResources || s[2] != ScopePrompts {
			t.Fatalf("scopes_supported %v", doc["scopes_supported"])
		}
		if b, _ := doc["bearer_methods_supported"].([]any); len(b) != 1 || b[0] != "header" {
			t.Fatalf("bearer_methods_supported %v, want [header]", doc["bearer_methods_supported"])
		}
		if doc["resource_name"] == "" {
			t.Fatal("resource_name is empty")
		}
	}
}

// The addresses come from configuration only: a custom endpoint, a public
// base with a path and a trailing slash all shape the resource, and the
// metadata of an id that names no virtual server has the same shape as any
// other (nothing is looked up).
func TestResourceAddressesComeFromConfig(t *testing.T) {
	res, err := NewResource(Config{PublicURL: "https://edge.example.test/gw/", Endpoint: "/tools/mcp/", Issuer: testIssuer})
	if err != nil {
		t.Fatal(err)
	}
	if got, want := res.ResourceURI("vs_1"), "https://edge.example.test/gw/tools/mcp/v/vs_1"; got != want {
		t.Fatalf("ResourceURI = %q, want %q", got, want)
	}
	if got, want := res.MetadataURL(""), "https://edge.example.test/.well-known/oauth-protected-resource/gw/tools/mcp"; got != want {
		t.Fatalf("MetadataURL = %q, want %q", got, want)
	}
	if res.Endpoint() != "/tools/mcp" {
		t.Fatalf("Endpoint = %q, want the canonical /tools/mcp", res.Endpoint())
	}
	a, _ := json.Marshal(res.Metadata("vs_does_not_exist"))
	b, _ := json.Marshal(res.Metadata("vs_other"))
	if strings.ReplaceAll(string(a), "vs_does_not_exist", "X") != strings.ReplaceAll(string(b), "vs_other", "X") {
		t.Fatalf("metadata differs beyond the id:\n%s\n%s", a, b)
	}
}

// A request path maps to a resource only when it is the endpoint or one
// virtual server directly under it.
func TestResourceForPath(t *testing.T) {
	res := newTestResource(t)
	for path, want := range map[string]string{
		"/mcp":        "https://gw.example.test/mcp",
		"/mcp/v/vs_A": "https://gw.example.test/mcp/v/vs_A",
	} {
		if got, ok := res.ResourceForPath(path); !ok || got != want {
			t.Fatalf("ResourceForPath(%q) = %q, %v, want %q", path, got, ok, want)
		}
	}
	for _, path := range []string{"", "/", "/mcp/", "/mcp/v", "/mcp/v/", "/mcp/v/a/b", "/mcpx", "/other/mcp", "/v1/chat/completions"} {
		if got, ok := res.ResourceForPath(path); ok {
			t.Fatalf("ResourceForPath(%q) = %q, want no resource", path, got)
		}
	}
}

// A configuration that would advertise a relative, ambiguous or missing
// address is refused, never served.
func TestNewResourceRefusesUnusableConfig(t *testing.T) {
	for name, c := range map[string]Config{
		"no public url":       {Issuer: testIssuer},
		"relative public url": {PublicURL: "/gw", Issuer: testIssuer},
		"public url scheme":   {PublicURL: "ftp://gw.example.test", Issuer: testIssuer},
		"public url query":    {PublicURL: "https://gw.example.test?x=1", Issuer: testIssuer},
		"public url fragment": {PublicURL: "https://gw.example.test#x", Issuer: testIssuer},
		"public url user":     {PublicURL: "https://someone@gw.example.test", Issuer: testIssuer},
		"public url path":     {PublicURL: "https://gw.example.test/a/../b", Issuer: testIssuer},
		"no issuer":           {PublicURL: testPublicURL},
		"relative issuer":     {PublicURL: testPublicURL, Issuer: "agentcc/mcp-oauth"},
		"issuer query":        {PublicURL: testPublicURL, Issuer: testIssuer + "?a=b"},
	} {
		if _, err := NewResource(c); err == nil {
			t.Fatalf("%s: NewResource accepted %+v", name, c)
		}
	}
}
