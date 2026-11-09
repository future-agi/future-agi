package main

import (
	"errors"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
)

// TestMCPUpstreamWiringInstallsFactory: the "upstream" step installs M13's
// factory as Deps.ClientFactory, and the factory dials through the dialer
// the server holds after SetDeps: M17's guard, set by the later "probe"
// step, which refuses a loopback upstream that private_allow does not list.
func TestMCPUpstreamWiringInstallsFactory(t *testing.T) {
	t.Setenv("AGENTCC_MCP_STATE_KEYS", mcpTestStateKey)
	srv := newWiringServer(t, true)
	d := &wiringDeps{}
	if err := runMCPWiring(d, mcpWiringOrder); err != nil {
		t.Fatalf("runMCPWiring: %v", err)
	}
	d.server = srv
	if err := d.startServerWiring(); err != nil {
		t.Fatalf("startServerWiring: %v", err)
	}
	deps := srv.MCP().Deps()
	if reflect.ValueOf(deps.ClientFactory).Pointer() == reflect.ValueOf(mcp.DefaultClientFactory).Pointer() {
		t.Fatal("Deps.ClientFactory is still M1's default")
	}
	if _, ok := deps.Dialer.(*egress.Dialer); !ok {
		t.Fatalf("Deps.Dialer = %T, want M17's guard", deps.Dialer)
	}
	_, err := deps.ClientFactory(mcp.ClientConfig{ServerID: "loop", URL: "http://127.0.0.1:9/mcp"})
	var refused *egress.RefusedError
	if !errors.As(err, &refused) {
		t.Fatalf("a loopback upstream through the wired factory: %v, want the guard's refusal", err)
	}
}
