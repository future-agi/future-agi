package external

import (
	"context"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A failed call to an external guardrail service is reported to the org whose
// request the check judged (the 403 a block answers, a warning, the request
// log). The message says the call failed and never names where the gateway
// connected: not the URL, not the address it dialed, not a proxy it went
// through. The gateway's own log keeps the full error for the operator.
func TestCheckFailureNamesNoAddress(t *testing.T) {
	cases := []struct {
		name      string
		endpoint  string
		forbidden []string
	}{
		{"address", "http://127.0.0.1:9/org-guard-endpoint/v2/guard",
			[]string{"127.0.0.1", "org-guard-endpoint", ":9", "http://", "dial tcp"}},
		{"hostname", "http://guard.example-org.invalid:8443/v2/guard",
			[]string{"example-org.invalid", "8443", "http://", "dial tcp", "lookup", "proxyconnect", "127.0.0.1"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			g := newGuard("ext-check", map[string]interface{}{
				"provider": "lakera",
				"endpoint": tc.endpoint,
				"api_key":  "literal-org-key",
				"timeout":  "3s",
			})
			r := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
			if r.Pass {
				t.Fatal("a check whose call fails must not pass")
			}
			for _, s := range tc.forbidden {
				if strings.Contains(r.Message, s) {
					t.Errorf("the failure message names where the gateway connected (%q): %q", s, r.Message)
				}
			}
			if !strings.Contains(r.Message, "external guardrail call failed") {
				t.Errorf("the message should still say the call failed: %q", r.Message)
			}
		})
	}
}
