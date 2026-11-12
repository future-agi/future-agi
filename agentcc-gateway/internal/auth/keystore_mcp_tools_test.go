package auth

import (
	"bytes"
	"log/slog"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"gopkg.in/yaml.v3"
)

// TestYAMLMCPFieldsSplitFromAllowedTools is bug 23: one allowed_tools list
// governed MCP tool names and the LLM path's function tools. The MCP path
// now reads mcp_allowed_tools and mcp_denied_tools, falling back to the old
// lists only when both are absent, with a deprecation logged once.
func TestYAMLMCPFieldsSplitFromAllowedTools(t *testing.T) {
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })

	var cfg config.AuthConfig
	if err := yaml.Unmarshal([]byte(`
keys:
  - {name: split, key: k-split, allowed_tools: [web_search], denied_tools: [shell], mcp_allowed_tools: [github_list_repos]}
  - {name: legacy, key: k-legacy, allowed_tools: [github_list_repos], denied_tools: [github_delete_repo]}
  - {name: deny-only, key: k-deny, allowed_tools: [web_search], mcp_denied_tools: [github_delete_repo]}
  - {name: opt-out, key: k-optout, allowed_tools: [web_search], mcp_allowed_tools: []}
  - {name: plain, key: k-plain}
`), &cfg); err != nil {
		t.Fatal(err)
	}
	ks := NewKeyStore(cfg)
	key := func(raw string) *APIKey {
		k := ks.Lookup(raw)
		if k == nil {
			t.Fatalf("no key %s", raw)
		}
		return k
	}
	for _, tc := range []struct {
		raw             string
		allowed, denied string
	}{
		{"k-split", "github_list_repos", ""},
		{"k-legacy", "github_list_repos", "github_delete_repo"},
		{"k-deny", "", "github_delete_repo"},
		{"k-optout", "", ""},
		{"k-plain", "", ""},
	} {
		a, d := key(tc.raw).MCPToolLists()
		if strings.Join(a, ",") != tc.allowed || strings.Join(d, ",") != tc.denied {
			t.Errorf("%s: MCP lists (%v, %v), want (%s, %s)", tc.raw, a, d, tc.allowed, tc.denied)
		}
	}
	// The /v1 lists are untouched.
	if k := key("k-split"); strings.Join(k.AllowedTools, ",") != "web_search" || strings.Join(k.DeniedTools, ",") != "shell" {
		t.Fatalf("/v1 lists changed: %v %v", k.AllowedTools, k.DeniedTools)
	}
	logs := buf.String()
	if n := strings.Count(logs, "deprecated fallback"); n != 1 || !strings.Contains(logs, "name=legacy") {
		t.Fatalf("deprecation logged %d times, want once for key legacy:\n%s", n, logs)
	}
}
