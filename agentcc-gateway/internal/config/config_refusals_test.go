package config

import (
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Two config.go load refusals: a non-finite markup and a colliding MCP endpoint.

func loadRefusalYAML(t *testing.T, body string) error {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	_, err := Load(path)
	return err
}

// A non-finite markup passes a range check (every comparison with NaN is
// false), and managed capabilities are priced with ToolMarkupBps.
func TestNonFiniteMarkupIsConfigError(t *testing.T) {
	for _, tc := range []struct {
		yaml, want string
	}{
		{"managed:\n  markup_percent: .nan\n", "managed.markup_percent"},
		{"managed:\n  tool_markup_percent: .nan\n", "managed.tool_markup_percent"},
		{"managed:\n  markup_percent: .inf\n", "managed.markup_percent"},
		{"managed:\n  tool_markup_percent: -.inf\n", "managed.tool_markup_percent"},
	} {
		err := loadRefusalYAML(t, tc.yaml)
		if err == nil || !strings.Contains(err.Error(), tc.want) {
			t.Errorf("%q: Load = %v, want an error naming %s", tc.yaml, err, tc.want)
		}
	}
	if err := loadRefusalYAML(t, "managed:\n  markup_percent: 5\n  tool_markup_percent: -1\n"); err != nil {
		t.Errorf("tool_markup_percent -1 (inherit) must stay allowed: %v", err)
	}

	c := DefaultConfig()
	c.Managed.ToolMarkupPercent = math.NaN()
	if err := c.Validate(); err == nil {
		t.Error("Validate accepted a NaN tool markup")
	}
}

// The MCP endpoint is never "/" or under one of the gateway's own route
// roots: Validate and Load refuse both.
func TestMCPEndpointCollidingWithAPIRootIsConfigError(t *testing.T) {
	for _, ep := range []string{"/", "//", "/v1", "/v1/mcp", "/v1beta", "/-", "/-/mcp", "/a2a", "/healthz", "/.well-known", "mcp"} {
		c := DefaultConfig()
		c.MCP.Endpoint = ep
		err := c.Validate()
		if err == nil || !strings.Contains(err.Error(), "mcp.endpoint") {
			t.Errorf("mcp.endpoint %q: Validate = %v, want an mcp.endpoint error", ep, err)
		}
	}
	for _, ep := range []string{"", "/mcp", "/tools/mcp", "/v1x"} {
		c := DefaultConfig()
		c.MCP.Endpoint = ep
		if err := c.Validate(); err != nil {
			t.Errorf("mcp.endpoint %q: Validate = %v, want nil", ep, err)
		}
	}
	if err := loadRefusalYAML(t, "mcp:\n  endpoint: /\n"); err == nil || !strings.Contains(err.Error(), "mcp.endpoint") {
		t.Errorf("Load with mcp.endpoint / = %v, want an mcp.endpoint error", err)
	}
}
