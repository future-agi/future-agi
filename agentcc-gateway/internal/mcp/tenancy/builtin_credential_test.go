package tenancy

import (
	"context"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// A per_user customer credential on a built-in server is never served by the
// shared built-in: there is no per-person client to bind that person's grant
// lifetime to, so the call is refused and the built-in is never called. (A
// shared customer credential is guarded through the per-entry adapter; the
// credentials package's supplied built-in replica tests pin that.)
func TestBuiltinPersonalCustomerCredentialRefused(t *testing.T) {
	operator := map[string]config.ManagedMCPServerConfig{"web": {Kind: config.ManagedServerKindBuiltin}}
	r := newRig(t, Options{MCP: config.MCPConfig{ManagedServers: operator}, InitialWait: -1})
	up := &fakeUpstream{tools: []string{"search"}}
	builtin := &fakeBuiltin{fakeClient{cfg: mcp.ClientConfig{ServerID: "fagi.web"}, up: up}}
	reg := mcp.NewBuiltinRegistry()
	if err := reg.Register("web", builtin); err != nil {
		t.Fatal(err)
	}
	d := *r.deps.Load()
	d.Builtins = reg
	d.ManagedServers = fixedManaged{"org-a": {{ServerID: "fagi.web"}}}
	r.deps.Store(&d)
	r.push("org-a", 1, `{"enabled":true,"managed_tools":{"enabled":true,"credentials":{"fagi.web":{"mode":"per_user","credential_id":"synthetic-personal"}}}}`)
	_, pool := r.resolve("org-a")
	auth := mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer synthetic-person-grant"}}}
	if c, err := pool.Client("fagi.web", "u1", auth); err == nil {
		if _, err := c.CallTool(context.Background(), "search", nil, mcp.CallOptions{}); err == nil {
			t.Fatal("a person's customer credential on a built-in was served by the shared built-in")
		}
	}
	if calls := up.called(); len(calls) != 0 {
		t.Fatalf("the built-in was called %d times for a person's customer credential", len(calls))
	}
	if builtin.closeCall.Load() != 0 {
		t.Fatal("refusing a person's call closed the registry-owned built-in")
	}
}
