package tenancy

import (
	"regexp"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// An org config cannot add a stdio server. The refusal is what the operator
// reads back from the config push, so it says where stdio servers are defined,
// in plain words, and cites no internal work item.
func TestOrgStdioServerRefusalIsPlain(t *testing.T) {
	_, err := orgSpec("local", tenant.MCPServerSettings{Transport: "stdio"}, nil)
	if err == nil {
		t.Fatal("an org config stdio server was accepted")
	}
	msg := err.Error()
	if !strings.Contains(msg, "stdio servers are defined only in the operator's YAML") {
		t.Errorf("refusal %q does not say where stdio servers are defined", msg)
	}
	if regexp.MustCompile(`\((?:decision|plan|section) [0-9]|\b[A-Z]{1,3}-?[0-9]+\b`).MatchString(msg) {
		t.Errorf("refusal %q cites an internal work item", msg)
	}
}
