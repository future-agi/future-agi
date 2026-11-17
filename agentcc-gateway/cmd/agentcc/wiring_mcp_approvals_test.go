package main

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestW5ApprovalProviderWired(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.MCP.Enabled = true
	cfg.MCP.StateKeys = []string{mcpTestStateKey}
	srv := newWiringServer(t, true)
	d := &wiringDeps{cfg: cfg, server: srv}
	// Use the executable's normal ordered MCP initialization, not just a
	// standalone utility. No Redis is deliberately configured.
	if err := initMCP(d); err != nil {
		t.Fatal(err)
	}
	if err := d.startServerWiring(); err != nil {
		t.Fatal(err)
	}
	if _, ok := srv.MCP().Deps().Approvals.(*mcp.ApprovalManager); !ok {
		t.Fatalf("provider=%T", srv.MCP().Deps().Approvals)
	}
	var org tenant.OrgConfig
	_ = json.Unmarshal([]byte(`{"mcp":{"virtual_servers":{"review":{"id":"vs-review","approval":{"require":"listed","tools":["mock/*"]}}}}}`), &org)
	cc := &mcp.CallContext{OrgConfig: &org, VirtualServer: "vs-review", Principal: mcp.Principal{Kind: mcp.PrincipalKey, OrgID: "synthetic", KeyID: "owner"}, Upstream: mcp.UpstreamRef{ServerID: "mock"}, Tool: mcp.ToolRef{UpstreamName: "delete"}, Args: json.RawMessage(`{}`)}
	if denial := srv.MCP().Deps().Approvals.Check(cc); denial == nil || denial.Code != mcp.CodePolicyUnavailable {
		t.Fatalf("unavailable Redis allowed call: %v", denial)
	}
	cfg.MCP.Enabled = false
	deps := &mcp.Deps{}
	if err := initMCPApprovals(d, deps); err != nil || deps.Approvals != nil {
		t.Fatal("disabled MCP installed approvals")
	}
}
