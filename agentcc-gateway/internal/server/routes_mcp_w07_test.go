package server

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestW07NamedRoutesOffOn(t *testing.T) {
	for _, on := range []bool{false, true} {
		s := newOAuthTestServer(t, true, func(c *config.Config) { c.MCP.VirtualServersEnabled = on })
		for _, path := range []string{"/mcp/v/fixture", "/mcp/v/fixture/approvals/fixture", "/.well-known/oauth-protected-resource/mcp/v/fixture"} {
			method, body := "GET", ""
			if path == "/mcp/v/fixture" {
				method, body = "POST", oauthTestInit
			}
			w := serve(s, method, path, body, "")
			if !on && w.Code != http.StatusNotFound {
				t.Fatalf("OFF route %s returned %d", path, w.Code)
			}
			if on && path == "/mcp/v/fixture" && w.Code != http.StatusOK {
				t.Fatalf("ON initialize returned %d", w.Code)
			}
			if on && path == "/.well-known/oauth-protected-resource/mcp/v/fixture" && w.Code != http.StatusOK {
				t.Fatalf("ON metadata returned %d", w.Code)
			}
		}
		if w := serve(s, "POST", "/mcp", oauthTestInit, ""); w.Code != http.StatusOK {
			t.Fatalf("legacy default init changed: %d", w.Code)
		}
		if w := serve(s, "GET", "/.well-known/oauth-protected-resource/mcp", "", ""); w.Code != http.StatusOK {
			t.Fatalf("default metadata changed: %d", w.Code)
		}
	}
}
