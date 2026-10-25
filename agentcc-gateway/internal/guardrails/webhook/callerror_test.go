package webhook

import (
	"context"
	"strings"
	"testing"
)

// A failed webhook call is reported to the org whose request the check
// judged. The message says the call failed and never names where the gateway
// connected: not the URL, not the address it dialed, not a proxy it went
// through. The gateway's own log keeps the full error for the operator.
func TestCheckFailureNamesNoAddress(t *testing.T) {
	cases := []struct {
		name      string
		url       string
		forbidden []string
	}{
		{"address", "http://127.0.0.1:9/org-hook",
			[]string{"127.0.0.1", "org-hook", ":9", "http://", "dial tcp"}},
		{"hostname", "http://hook.example-org.invalid:8443/check",
			[]string{"example-org.invalid", "8443", "http://", "dial tcp", "lookup", "proxyconnect", "127.0.0.1"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			g := newGuard("hook-check", map[string]interface{}{"url": tc.url, "timeout": "3s"})
			r := g.Check(context.Background(), makeInput("hello"))
			if r.Pass {
				t.Fatal("a check whose call fails must not pass")
			}
			for _, s := range tc.forbidden {
				if strings.Contains(r.Message, s) {
					t.Errorf("the failure message names where the gateway connected (%q): %q", s, r.Message)
				}
			}
			if !strings.Contains(r.Message, "failed after") {
				t.Errorf("the message should still say the call failed: %q", r.Message)
			}
		})
	}
}
