package tenancy

// Integration tests; each names the
// integrator request it proves.

import (
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// TestOrgClientConfigCarriesEraAndCallSettings (the org half): an
// org server's era, timeout_ms and forward_headers reach the ClientConfig
// the pool builds it with, as a YAML server's do through
// mcp.StaticClientConfig, so a call through it is bounded by the server's own
// timeout and forwards its headers, and a pinned era is honoured. Before the
// merge the pool kept them in its spec only (fingerprint and status).
func TestOrgClientConfigCarriesEraAndCallSettings(t *testing.T) {
	r := newRig(t, Options{})
	r.factory.upstream(urlA, "search")
	r.push("org-a", 1, `{"enabled":true,"servers":{"github":{"url":"`+urlA+`","era":"2025-06-18",`+
		`"timeout_ms":120000,"forward_headers":["X-Tenant"]},"plain":{"url":"`+urlA+`"}}}`)
	_, pool := r.resolve("org-a")
	cfgOf := func(id string) mcp.ClientConfig {
		t.Helper()
		c, err := pool.Client(id, "", mcp.AuthOverride{})
		if err != nil {
			t.Fatalf("Client(%s): %v", id, err)
		}
		return unwrap(c).(*fakeClient).cfg
	}
	got := cfgOf("github")
	if got.Era != "2025-06-18" || got.Call.Timeout != 120*time.Second ||
		len(got.Call.ForwardHeaders) != 1 || got.Call.ForwardHeaders[0] != "X-Tenant" {
		t.Fatalf("github's ClientConfig era %q, call settings %+v; want 2025-06-18, 120 s, [X-Tenant]", got.Era, got.Call)
	}
	// A server that sets neither gets the org config defaults: era auto, timeout_ms
	// 30000 (the tenant accessor's), no forwarded header.
	if plain := cfgOf("plain"); plain.Era != "auto" || plain.Call.Timeout != 30*time.Second || len(plain.Call.ForwardHeaders) != 0 {
		t.Fatalf("plain's ClientConfig era %q, call settings %+v; want auto, 30 s and no header", plain.Era, plain.Call)
	}
}
