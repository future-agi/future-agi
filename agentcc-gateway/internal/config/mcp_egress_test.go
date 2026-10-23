package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The operator list mcp.egress.private_allow: config load refuses a cloud
// metadata address or host name, a range inside a metadata range, a range
// that covers any part of the link-local block, and a malformed entry; it
// accepts private ranges and hosts. The egress guard refuses every metadata
// address at dial time, whatever the list holds
// (TestPrivateAllowWideRangeKeepsMetadataRefused).
func TestPrivateAllowRefusesMetadata(t *testing.T) {
	for _, entry := range []string{
		"169.254.169.254", "169.254.169.254:80", "169.254.0.0/16", "169.254.169.254/32", "169.254.170.0/24",
		"169.0.0.0/8", "0.0.0.0/0", "::/0", "::/1", "::ffff:0:0/96", "::ffff:169.254.169.254", "::ffff:169.254.0.0/112",
		"fd00:ec2::254", "[fd00:ec2::254]:80", "fd00:ec2::254/128",
		"fd20:ce::254", "[fd20:ce::254]:80", "fd20:ce::254/128",
		"100.100.100.200", "100.100.100.200/32", "168.63.129.16",
		"metadata.google.internal", "Metadata.Google.Internal.", "metadata.google.internal:80", "metadata.goog",
		"metadata", "instance-data", "instance-data.ec2.internal:8080",
	} {
		c := DefaultConfig()
		c.MCP.Egress.PrivateAllow = []string{"10.0.0.0/8", entry}
		err := c.Validate()
		if err == nil || !strings.Contains(err.Error(), "mcp.egress.private_allow[1]") || !strings.Contains(err.Error(), "metadata") {
			t.Errorf("private_allow %q: Validate = %v, want a metadata refusal", entry, err)
		}
	}
	for _, entry := range []string{"", "10.0.0.0/33", "mock.local:0", "mock.local:http", "[]:80"} {
		c := DefaultConfig()
		c.MCP.Egress.PrivateAllow = []string{entry}
		if err := c.Validate(); err == nil {
			t.Errorf("private_allow %q: malformed entry accepted", entry)
		}
	}

	path := filepath.Join(t.TempDir(), "config.yaml")
	body := `
mcp:
  egress:
    private_allow:
      - 10.0.0.0/8
      - 192.168.1.0/24
      - 100.64.0.0/10
      - fc00::/7
      - 127.0.0.1:9100
      - mcp.internal
      - mcp.internal:8443
      - 10.1.2.3
`
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	c, err := Load(path)
	if err != nil {
		t.Fatalf("private ranges and hosts refused: %v", err)
	}
	if len(c.MCP.Egress.PrivateAllow) != 8 {
		t.Errorf("private_allow = %v", c.MCP.Egress.PrivateAllow)
	}
	if c.MCP.Enabled {
		t.Error("an mcp.egress list turned MCP on")
	}
}
