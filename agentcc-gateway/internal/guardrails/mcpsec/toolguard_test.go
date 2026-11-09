package mcpsec

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
)

func TestToolGuardBlockedTool(t *testing.T) {
	g := NewToolGuard(map[string]interface{}{
		"blocked_tools": []string{"exec_command", "delete_all"},
	})

	if msg := g.CheckPre("exec_command", "server1", nil); msg == "" {
		t.Error("expected blocked for exec_command")
	}
	if msg := g.CheckPre("read_file", "server1", nil); msg != "" {
		t.Errorf("expected allowed for read_file, got: %s", msg)
	}
}

func TestToolGuardBlockedToolNamespaced(t *testing.T) {
	g := NewToolGuard(map[string]interface{}{
		"blocked_tools": []string{"exec_command"},
	})

	// Namespaced tool: "server1_exec_command" should match "exec_command".
	if msg := g.CheckPre("server1_exec_command", "server1", nil); msg == "" {
		t.Error("expected blocked for namespaced exec_command")
	}
}

func TestToolGuardAllowedServers(t *testing.T) {
	g := NewToolGuard(map[string]interface{}{
		"allowed_servers": []string{"trusted-server"},
	})

	if msg := g.CheckPre("any_tool", "trusted-server", nil); msg != "" {
		t.Errorf("expected allowed for trusted-server, got: %s", msg)
	}
	if msg := g.CheckPre("any_tool", "untrusted-server", nil); msg == "" {
		t.Error("expected blocked for untrusted-server")
	}
}

func TestToolGuardAllowedServersEmpty(t *testing.T) {
	// No allowed_servers means all servers are allowed.
	g := NewToolGuard(map[string]interface{}{})

	if msg := g.CheckPre("any_tool", "any-server", nil); msg != "" {
		t.Errorf("expected allowed when no server restriction, got: %s", msg)
	}
}

func TestToolGuardInjectionInArgs(t *testing.T) {
	g := NewToolGuard(map[string]interface{}{
		"validate_inputs": true,
	})

	args := map[string]interface{}{
		"sql": "DROP TABLE users",
	}
	if msg := g.CheckPre("query_db", "server1", args); msg == "" {
		t.Error("expected injection detection in args")
	}

	cleanArgs := map[string]interface{}{
		"query": "SELECT * FROM users WHERE id = 1",
	}
	if msg := g.CheckPre("query_db", "server1", cleanArgs); msg != "" {
		t.Errorf("expected clean args to pass, got: %s", msg)
	}
}

func TestToolGuardInjectionDisabled(t *testing.T) {
	g := NewToolGuard(map[string]interface{}{
		"validate_inputs": false,
	})

	args := map[string]interface{}{
		"cmd": "exec('rm -rf /')",
	}
	if msg := g.CheckPre("run", "server1", args); msg != "" {
		t.Errorf("expected pass when validation disabled, got: %s", msg)
	}
}

func TestToolGuardPostCheck(t *testing.T) {
	g := NewToolGuard(map[string]interface{}{
		"validate_outputs": true,
	})

	// Clean result.
	cleanResult := &mcp.ToolCallResult{
		Content: []mcp.ContentPart{
			{Type: "text", Text: "The answer is 42"},
		},
	}
	if msg := g.CheckPost("tool", "server1", cleanResult); msg != "" {
		t.Errorf("expected clean result to pass, got: %s", msg)
	}

	// Result with injection.
	badResult := &mcp.ToolCallResult{
		Content: []mcp.ContentPart{
			{Type: "text", Text: "Result: <script>alert(1)</script>"},
		},
	}
	if msg := g.CheckPost("tool", "server1", badResult); msg == "" {
		t.Error("expected injection detection in output")
	}
}

func TestToolGuardPostCheckDisabled(t *testing.T) {
	g := NewToolGuard(map[string]interface{}{
		"validate_outputs": false,
	})

	badResult := &mcp.ToolCallResult{
		Content: []mcp.ContentPart{
			{Type: "text", Text: "<script>alert(1)</script>"},
		},
	}
	if msg := g.CheckPost("tool", "server1", badResult); msg != "" {
		t.Errorf("expected pass when output validation disabled, got: %s", msg)
	}
}

func TestToolGuardPostCheckNilResult(t *testing.T) {
	g := NewToolGuard(map[string]interface{}{
		"validate_outputs": true,
	})

	if msg := g.CheckPost("tool", "server1", nil); msg != "" {
		t.Errorf("expected pass for nil result, got: %s", msg)
	}
}

func TestToolGuardCommaBlockedTools(t *testing.T) {
	// Test comma-separated string input (from YAML config).
	g := NewToolGuard(map[string]interface{}{
		"blocked_tools": "exec_command,delete_all",
	})

	if msg := g.CheckPre("exec_command", "s1", nil); msg == "" {
		t.Error("expected blocked for exec_command from comma-separated")
	}
	if msg := g.CheckPre("delete_all", "s1", nil); msg == "" {
		t.Error("expected blocked for delete_all from comma-separated")
	}
}

func TestExtractStringSlice(t *testing.T) {
	// []string
	cfg := map[string]interface{}{"key": []string{"a", "b"}}
	result := extractStringSlice(cfg, "key")
	if len(result) != 2 || result[0] != "a" || result[1] != "b" {
		t.Errorf("expected [a b], got %v", result)
	}

	// []interface{}
	cfg = map[string]interface{}{"key": []interface{}{"x", "y"}}
	result = extractStringSlice(cfg, "key")
	if len(result) != 2 {
		t.Errorf("expected 2, got %d", len(result))
	}

	// string
	cfg = map[string]interface{}{"key": "one, two, three"}
	result = extractStringSlice(cfg, "key")
	if len(result) != 3 {
		t.Errorf("expected 3, got %d", len(result))
	}

	// missing key
	cfg = map[string]interface{}{}
	result = extractStringSlice(cfg, "key")
	if result != nil {
		t.Errorf("expected nil, got %v", result)
	}
}

// TestBlockedToolSeparatorAware is bug 22's matching half: the old suffix
// match over-blocked ("exec" blocked "github_my_exec") and under-blocked
// (another separator, or a virtual server's "__", slipped past).
func TestBlockedToolSeparatorAware(t *testing.T) {
	for _, tc := range []struct {
		name      string
		separator string
		blocked   []string
		tool      string
		server    string
		want      bool
	}{
		{"exact exposed name", "", []string{"github__exec"}, "github__exec", "github", true},
		{"bare tool on the server, legacy name", "", []string{"exec"}, "github_exec", "github", true},
		{"bare tool on the server, virtual-server name", "", []string{"exec"}, "github__exec", "github", true},
		{"bare tool, configured separator", ".", []string{"exec"}, "github.exec", "github", true},
		{"legacy qualified entry, virtual-server name", "", []string{"github_exec"}, "github__exec", "github", true},
		{"legacy qualified entry, configured separator", ".", []string{"github.exec"}, "github__exec", "github", true},
		{"never a bare suffix", "", []string{"exec"}, "github_my_exec", "github", false},
		{"never a suffix across the server boundary", "", []string{"hub_exec"}, "github_exec", "github", false},
		{"another server's qualified entry", "", []string{"gitlab_exec"}, "github__exec", "github", false},
		{"no server: exact only", "", []string{"exec"}, "x_exec", "", false},
		{"a flat name is exact", "", []string{"exec"}, "exec", "github", true},
		// An account member github@work exposes github_work__<tool>.
		{"bare tool, account member", "", []string{"delete_repo"}, "github_work__delete_repo", "github", true},
		{"legacy qualified entry, account member", "", []string{"github_delete_repo"}, "github_work__delete_repo", "github", true},
		{"configured separator, account member", ".", []string{"github.delete_repo"}, "github_work__delete_repo", "github", true},
		{"never a bare suffix, account member", "", []string{"repo"}, "github_work__delete_repo", "github", false},
		{"another server's account member", "", []string{"delete_repo"}, "gitlab_work__delete_repo", "github", false},
		{"the server's own prefixed name is not an account", "", []string{"x"}, "github__a__x", "github", false},
		// Flat naming exposes the upstream name itself.
		{"legacy qualified entry, flat name", "", []string{"github_delete_repo"}, "delete_repo", "github", true},
		{"configured separator, flat name", ".", []string{"github.delete_repo"}, "delete_repo", "github", true},
		{"another server's qualified entry, flat name", "", []string{"gitlab_delete_repo"}, "delete_repo", "github", false},
	} {
		cfg := map[string]interface{}{"blocked_tools": tc.blocked}
		if tc.separator != "" {
			cfg["separator"] = tc.separator
		}
		g := NewToolGuard(cfg)
		if got := g.CheckPre(tc.tool, tc.server, nil) != ""; got != tc.want {
			t.Errorf("%s: %v blocks %s on %q = %v, want %v", tc.name, tc.blocked, tc.tool, tc.server, got, tc.want)
		}
	}

	// The resolved upstream tool, whatever name the call exposed it under.
	for _, tc := range []struct {
		name      string
		separator string
		blocked   []string
		server    string
		tool      string
		want      bool
	}{
		{"bare entry", "", []string{"delete_repo"}, "github", "delete_repo", true},
		{"legacy qualified entry", "", []string{"github_delete_repo"}, "github", "delete_repo", true},
		{"virtual-server qualified entry", "", []string{"github__delete_repo"}, "github", "delete_repo", true},
		{"configured separator", ".", []string{"github.delete_repo"}, "github", "delete_repo", true},
		{"another separator's entry", ".", []string{"github_delete_repo"}, "github", "delete_repo", false},
		{"never a bare suffix", "", []string{"repo"}, "github", "delete_repo", false},
		{"never a prefix", "", []string{"delete"}, "github", "delete_repo", false},
		{"another server's qualified entry", "", []string{"gitlab_delete_repo"}, "github", "delete_repo", false},
		{"an exposed name is not an upstream tool", "", []string{"remove_repo"}, "github", "delete_repo", false},
		{"nothing resolved", "", []string{"delete_repo"}, "", "", false},
	} {
		cfg := map[string]interface{}{"blocked_tools": tc.blocked}
		if tc.separator != "" {
			cfg["separator"] = tc.separator
		}
		if got := NewToolGuard(cfg).BlocksUpstreamTool(tc.server, tc.tool); got != tc.want {
			t.Errorf("%s: %v blocks upstream %s/%s = %v, want %v", tc.name, tc.blocked, tc.server, tc.tool, got, tc.want)
		}
	}

	// Exposed names that cannot be read back to their upstream tool: the
	// exposed name alone never blocks them, the resolved upstream tool always
	// does, which is why the scanner must judge both (lane M10's IR-9). Lane M10
	// rejects the three ambiguous account labels at composition, so no member
	// exposes those names; overrides, hash caps and normalization stay.
	long := "delete_repository_and_every_associated_resource_permanently_now"
	for _, tc := range []struct {
		form     string
		exposed  string
		upstream string
	}{
		{`account label "my__acct"`, vserver.EncodePrefixed(vserver.ParseMember("github@my__acct").Key(), "delete_repo"), "delete_repo"},
		{`account label "_x"`, vserver.EncodePrefixed(vserver.ParseMember("github@_x").Key(), "delete_repo"), "delete_repo"},
		{`account label "x_"`, vserver.EncodePrefixed(vserver.ParseMember("github@x_").Key(), "delete_repo"), "delete_repo"},
		{`account label "a  b"`, vserver.EncodePrefixed(vserver.ParseMember("github@a  b").Key(), "delete_repo"), "delete_repo"},
		{"an override's name", "remove_repo", "delete_repo"},
		{"a hash-capped name", vserver.EncodePrefixed("github", long), long},
		{"a normalized flat name", vserver.Normalize("delete repo"), "delete repo"},
	} {
		for _, blocked := range []string{tc.upstream, "github_" + tc.upstream} {
			g := NewToolGuard(map[string]interface{}{"blocked_tools": []string{blocked}})
			if g.CheckPre(tc.exposed, "github", nil) != "" {
				t.Errorf("%s: [%s] blocks the exposed name %s: the parse guessed an upstream tool", tc.form, blocked, tc.exposed)
			}
			if !g.BlocksUpstreamTool("github", tc.upstream) {
				t.Errorf("%s: [%s] does not block the resolved upstream tool github/%s (exposed %s)", tc.form, blocked, tc.upstream, tc.exposed)
			}
		}
	}
}
