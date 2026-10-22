package config

import (
	"reflect"
	"strings"
	"testing"
)

// The Standalone install (the gateway beside Django under supervisord) lists
// its own loopback backend for the egress guard through the environment: its
// gateway reads the config file the operator mounts (config.example.yaml by
// default), which the install does not own.
const egressAllowEnv = "AGENTCC_MCP_EGRESS_PRIVATE_ALLOW"

func TestLoad_EgressPrivateAllowFromEnvFollowsTheFile(t *testing.T) {
	t.Setenv(egressAllowEnv, " localhost:8000, 127.0.0.1:8000 ,,")
	c, err := loadYAML(t, "mcp:\n  egress:\n    private_allow: [\"10.0.0.0/8\"]\n")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	want := []string{"10.0.0.0/8", "localhost:8000", "127.0.0.1:8000"}
	if !reflect.DeepEqual(c.MCP.Egress.PrivateAllow, want) {
		t.Errorf("mcp.egress.private_allow = %q, want %q", c.MCP.Egress.PrivateAllow, want)
	}

	// With no file entry, the environment's are the list.
	c, err = loadYAML(t, "")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if want := want[1:]; !reflect.DeepEqual(c.MCP.Egress.PrivateAllow, want) {
		t.Errorf("mcp.egress.private_allow = %q, want %q", c.MCP.Egress.PrivateAllow, want)
	}
}

func TestLoad_EgressPrivateAllowFromEnvIsCheckedAsTheFileIs(t *testing.T) {
	// A cloud-metadata entry is refused from the environment too, and the
	// error names the variable, so the operator knows where to look.
	t.Setenv(egressAllowEnv, "localhost:8000,169.254.169.254")
	_, err := loadYAML(t, "")
	if err == nil {
		t.Fatal("Load accepted a cloud-metadata entry from the environment")
	}
	if !strings.Contains(err.Error(), "169.254.169.254") || !strings.Contains(err.Error(), egressAllowEnv) {
		t.Errorf("Load error %q does not name the entry and %s", err, egressAllowEnv)
	}
}

func TestLoad_EgressPrivateAllowUnsetLeavesTheFileAlone(t *testing.T) {
	unsetEnv(t, egressAllowEnv)
	c, err := loadYAML(t, "mcp:\n  egress:\n    private_allow: [\"presidio.internal:5001\"]\n")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if want := []string{"presidio.internal:5001"}; !reflect.DeepEqual(c.MCP.Egress.PrivateAllow, want) {
		t.Errorf("mcp.egress.private_allow = %q, want %q", c.MCP.Egress.PrivateAllow, want)
	}
}
