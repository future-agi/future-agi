package mcpsec

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
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
