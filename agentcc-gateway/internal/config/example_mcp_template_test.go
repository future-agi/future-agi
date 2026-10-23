package config

import (
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"
)

// exampleConfigPath is the repo's config.example.yaml, the default gateway
// config of both compose installs.
var exampleConfigPath = filepath.Join("..", "..", "config.example.yaml")

// commentedLine is a comment line: its indent, "# ", and the rest.
var commentedLine = regexp.MustCompile(`^( *)# (.*)$`)

// uncommentMCPTemplate does what the file tells an operator to do to turn
// MCP on: in the MCP section (from its "# Model Context Protocol" heading to
// the first blank line), delete the "# " of every template line. A template
// line is one whose uncommented form is YAML under the mcp key: "mcp:" itself
// or an indented key ("  enabled: true", from "  # enabled: true", or from
// "#   enabled: true" in a file that keeps the template at column 0). A prose
// comment ("# With auth.enabled, ...") uncomments to unindented text and is
// left alone.
func uncommentMCPTemplate(t *testing.T, text string) string {
	t.Helper()
	lines := strings.Split(text, "\n")
	start := slices.IndexFunc(lines, func(l string) bool {
		return strings.HasPrefix(l, "# Model Context Protocol")
	})
	if start < 0 {
		t.Fatal("config.example.yaml has no \"# Model Context Protocol\" section")
	}
	changed := 0
	for i := start; i < len(lines) && strings.TrimSpace(lines[i]) != ""; i++ {
		m := commentedLine.FindStringSubmatch(lines[i])
		if m == nil {
			continue
		}
		if out := m[1] + m[2]; out == "mcp:" || strings.HasPrefix(out, " ") {
			lines[i] = out
			changed++
		}
	}
	if changed < 10 {
		t.Fatalf("found %d MCP template lines to uncomment, want the whole template", changed)
	}
	return strings.Join(lines, "\n")
}

// TestExampleConfig_MCPTemplateUncommentsIntoOneBlock: config.example.yaml
// holds one mcp block. The
// egress guard's private_allow is live in it (the Standalone install needs
// localhost:8000 listed), and the MCP template is commented beneath the same
// key, so uncommenting the template turns MCP on in that block and the file
// still loads.
func TestExampleConfig_MCPTemplateUncommentsIntoOneBlock(t *testing.T) {
	raw, err := os.ReadFile(exampleConfigPath)
	if err != nil {
		t.Fatal(err)
	}
	text := string(raw)

	var live, commented int
	for _, l := range strings.Split(text, "\n") {
		switch {
		case strings.HasPrefix(l, "mcp:"):
			live++
		case strings.HasPrefix(l, "# mcp:"):
			commented++
		}
	}
	if live != 1 || commented != 0 {
		t.Errorf("config.example.yaml has %d live and %d commented top-level mcp keys, want one live block and no second header", live, commented)
	}

	// A test token the start-up check accepts, as the installers set one;
	// the template's bearer token is a test value too.
	t.Setenv("AGENTCC_ADMIN_TOKEN", "5d1f8b3e7a9c2e4f6b8d0a2c4e6f8a1b3d5f7a9c1e3b5d7f9a2c4e6b8d0f2a4c")
	t.Setenv("MCP_SEARCH_TOKEN", "planted-mcp-search-token")

	shipped, err := Load(exampleConfigPath)
	if err != nil {
		t.Fatalf("config.example.yaml as shipped does not load: %v", err)
	}
	if shipped.MCP.Enabled {
		t.Error("config.example.yaml as shipped turns MCP on")
	}

	path := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(path, []byte(uncommentMCPTemplate(t, text)), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("config.example.yaml with its MCP template uncommented does not load: %v", err)
	}
	if !cfg.MCP.Enabled {
		t.Error("the uncommented template does not turn MCP on")
	}
	for _, name := range []string{"filesystem", "search"} {
		if _, ok := cfg.MCP.Servers[name]; !ok {
			t.Errorf("the uncommented template has no %q server", name)
		}
	}
	if !slices.Contains(cfg.MCP.Egress.PrivateAllow, "localhost:8000") {
		t.Errorf("mcp.egress.private_allow = %v after uncommenting, want localhost:8000 kept", cfg.MCP.Egress.PrivateAllow)
	}
}
