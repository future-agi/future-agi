package futureagi

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A failed evaluation call is reported to the org whose request the check
// judged. The message says the evaluation failed and never names where the
// gateway connected: not the URL, not the address it dialed, not a proxy it
// went through. The gateway's own log keeps the full error for the operator.
func TestCheckFailureNamesNoAddress(t *testing.T) {
	cases := []struct {
		name      string
		baseURL   string
		forbidden []string
	}{
		{"address", "http://127.0.0.1:9/operator-fi-host",
			[]string{"127.0.0.1", "operator-fi-host", ":9", "http://", "dial tcp"}},
		{"hostname", "http://fi-backend.example-operator.invalid:8443/operator-fi-host",
			[]string{"example-operator.invalid", "fi-backend", "8443", "http://", "dial tcp", "lookup", "proxyconnect", "127.0.0.1"}},
		{"not a URL", "http://[::1:bad/operator-fi-host",
			[]string{"::1", "operator-fi-host", "http://"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			g := newGuard("fi-check", map[string]interface{}{
				"provider":   "futureagi",
				"base_url":   tc.baseURL,
				"api_key":    "literal-org-key",
				"secret_key": "literal-org-secret",
				"eval_id":    "toxicity",
				"timeout":    "3s",
			})
			r := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
			if r.Pass {
				t.Fatal("a check whose evaluation call fails must not pass")
			}
			for _, s := range tc.forbidden {
				if strings.Contains(r.Message, s) {
					t.Errorf("the failure message names where the gateway connected (%q): %q", s, r.Message)
				}
			}
			if !strings.Contains(r.Message, "futureagi evaluation failed") {
				t.Errorf("the message should still say the evaluation failed: %q", r.Message)
			}
		})
	}
}

// A status the evaluation API answers carries no address, so the message
// still names it: the org can tell a refused key from an outage.
func TestCheckFailureStillNamesTheStatus(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
	}))
	defer srv.Close()
	g := newGuard("fi-check", map[string]interface{}{
		"provider":   "futureagi",
		"base_url":   srv.URL,
		"api_key":    "literal-org-key",
		"secret_key": "literal-org-secret",
		"eval_id":    "toxicity",
	})
	r := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
	if r.Pass || !strings.Contains(r.Message, "authentication failed (HTTP 403)") {
		t.Fatalf("a 403 from the evaluation API should read as an authentication failure: pass=%v %q", r.Pass, r.Message)
	}
}
