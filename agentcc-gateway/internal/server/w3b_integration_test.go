package server

// Integrator-owned tests for the W3b merge.

import (
	"strings"
	"testing"

	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/mcpsec"
)

// M10 IR-1: the routes' MCP key authenticator (used until the wiring installs
// M8's identity, and by any server built without it) grants a key its MCP
// tool lists, never its /v1 function-tool lists when an MCP list is set.
func TestMCPKeyAuthGrantsTheKeysMCPToolLists(t *testing.T) {
	ks := authpkg.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{
		{Name: "split", Key: "w3b-test-key-split", Metadata: map[string]string{"org_id": "org-w3b"},
			AllowedTools: []string{"web_search"}, DeniedTools: []string{"shell"}, MCPAllowedTools: []string{"gh_t1"}},
		{Name: "legacy", Key: "w3b-test-key-legacy", DeniedTools: []string{"gh_t1"}},
	}})
	a := &mcpKeyAuth{keyStore: ks}
	grant, ok := a.AuthenticateKey("w3b-test-key-split")
	if !ok || grant.OrgID != "org-w3b" || strings.Join(grant.AllowedTools, ",") != "gh_t1" || len(grant.DeniedTools) != 0 {
		t.Fatalf("split key grant %+v %v, want org-w3b with the MCP lists ([gh_t1], [])", grant, ok)
	}
	grant, ok = a.AuthenticateKey("w3b-test-key-legacy")
	if !ok || len(grant.AllowedTools) != 0 || strings.Join(grant.DeniedTools, ",") != "gh_t1" {
		t.Fatalf("legacy key grant %+v %v, want the deprecated fallback ([], [gh_t1])", grant, ok)
	}
}

// M10 IR-2 (merge-blocking with IR-9, orchestrator ruling 23:00): the MCP
// guard the routes build learns the configured mcp.separator. With a custom
// separator, an operator's blocked_tools entry <server><sep><tool> must block
// the tool under every name a call can use: the legacy exposed name, a virtual
// server's <server>__<tool>, and the resolved upstream tool (IR-9's check).
// Without the separator the guard assumes "_", so the last two pass.
func TestMCPGuardConfigCarriesTheSeparator(t *testing.T) {
	m := config.MCPConfig{Separator: "."}
	m.Guardrails.Enabled = true
	m.Guardrails.BlockedTools = []string{"github.delete_repo"}
	g := mcpsec.NewToolGuard(mcpGuardConfig(m))
	if !g.BlocksUpstreamTool("github", "delete_repo") {
		t.Fatal("the resolved upstream tool github/delete_repo is not blocked by github.delete_repo with separator \".\"")
	}
	for _, exposed := range []string{"github.delete_repo", "github__delete_repo"} {
		if msg := g.CheckPre(exposed, "github", nil); msg == "" {
			t.Fatalf("exposed name %s is not blocked by github.delete_repo with separator \".\"", exposed)
		}
	}
	if g.BlocksUpstreamTool("github", "list_repos") || g.CheckPre("github__list_repos", "github", nil) != "" {
		t.Fatal("a tool that is not blocked was refused")
	}
}
