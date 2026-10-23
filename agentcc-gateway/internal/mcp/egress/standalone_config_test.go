package egress

import (
	"errors"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/netguard"
)

// TestStandaloneConfigListsItsOwnBackend: the
// Standalone install runs the gateway next to Django in one container, with
// config.example.yaml as its config (docker-compose.yml), and Django fills an
// org FutureAGI eval check's base_url with its own BASE_URL, which defaults
// to http://localhost:8000 there. The guard refuses loopback unless listed, so
// that config lists exactly that name and port in mcp.egress.private_allow: a
// Standalone setup with guardrails on reaches its eval API,
// and nothing else on loopback opens (the gateway's own port, another port,
// the address spelled as an IP), and metadata stays refused.
func TestStandaloneConfigListsItsOwnBackend(t *testing.T) {
	// A test token the start-up check accepts, as the install's bootstrap
	// sets one; nothing here reads a real secret.
	t.Setenv("AGENTCC_ADMIN_TOKEN", "5d1f8b3e7a9c2e4f6b8d0a2c4e6f8a1b3d5f7a9c1e3b5d7f9a2c4e6b8d0f2a4c")
	cfg, err := config.Load("../../../config.example.yaml")
	if err != nil {
		t.Fatalf("config.example.yaml does not load: %v", err)
	}
	if cfg.MCP.Enabled {
		t.Error("config.example.yaml turns MCP on; its mcp section must only hold the egress list")
	}
	d, err := New(Options{PrivateAllow: cfg.MCP.Egress.PrivateAllow})
	if err != nil {
		t.Fatalf("New(%v): %v", cfg.MCP.Egress.PrivateAllow, err)
	}

	for _, resolved := range []string{"127.0.0.1:8000", "[::1]:8000"} {
		if err := d.check("localhost", resolved, false); err != nil {
			t.Errorf("Django's default BASE_URL http://localhost:8000 (resolved %s) is refused: %v", resolved, err)
		}
	}
	for _, c := range []struct{ host, resolved string }{
		{"localhost", "127.0.0.1:8080"}, // the gateway itself
		{"localhost", "127.0.0.1:8001"},
		{"127.0.0.1", "127.0.0.1:8000"}, // a name entry matches the name, not the address
		{"backend.internal", "10.0.0.5:8000"},
	} {
		var refused *RefusedError
		if err := d.check(c.host, c.resolved, false); !errors.As(err, &refused) {
			t.Errorf("check(%q, %q) = %v, want a refusal", c.host, c.resolved, err)
		}
	}
	var refused *RefusedError
	if err := d.check("localhost", "169.254.169.254:8000", false); !errors.As(err, &refused) || refused.Class != netguard.Forbidden {
		t.Errorf("metadata is not refused under the Standalone list: %v", err)
	}
}
