package conformance

import (
	"strings"
	"testing"
)

// A skip reason is printed for an operator, so it names the connector runtime
// in words, not by an internal code name.
func TestSkipReasonsNameTheConnectorRuntimeInWords(t *testing.T) {
	if strings.Contains(waitK2Runtime, "K2") {
		t.Fatalf("a skip reason names an internal code: %q", waitK2Runtime)
	}
	if !strings.Contains(waitK2Runtime, "in place of the connector runtime, agentcc-connectors;") {
		t.Fatalf("the skip reason does not name the connector runtime: %q", waitK2Runtime)
	}
}
