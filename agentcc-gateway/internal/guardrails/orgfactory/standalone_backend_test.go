package orgfactory_test

// The Standalone install runs the gateway beside Django under supervisord,
// and Django fills an org FutureAGI eval check's base_url with its own
// BASE_URL, http://localhost:8000 on a self-hosted install. The gateway dials
// that through the egress guard, which refuses loopback unless
// mcp.egress.private_allow lists it. The install's gateway config is the file
// the operator mounts (config.example.yaml by default, or their own), which it
// does not own, so supervisord sets AGENTCC_MCP_EGRESS_PRIVATE_ALLOW for its
// gateway to the local backend's address, and the gateway adds it to the list
// (futureagi/tests/test_default_install_packaging.py pins the value, the API's
// own port). This test drives that path as cmd/agentcc wires it: config.Load of
// the default mounted file with the variable set, the guard built from the
// loaded list, and an org check built by the org factory.

import (
	"context"
	"net/http"
	"net/http/httptest"
	"net/url"
	"path/filepath"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	guardrails "github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/orgfactory"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
)

func TestStandaloneLocalBackendIsReachedWithGuardrailsOn(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"result":[{"evaluations":[{"name":"toxicity","output":"Passed","evalId":"15"}]}]}`))
	}))
	defer srv.Close()
	port := mustURL(t, srv.URL).Port()
	// What Django puts in the check on a self-hosted install, with the test
	// server's port in place of the API's 8000.
	backend := "http://localhost:" + port

	// The Standalone gateway's default config file (docker-compose.yml mounts
	// AGENTCC_CONFIG_PATH, defaulting to it), loaded as the gateway loads it.
	example := filepath.Join("..", "..", "..", "config.example.yaml")

	cases := []struct {
		name  string
		allow string // AGENTCC_MCP_EGRESS_PRIVATE_ALLOW
		reach bool
	}{
		{"as supervisord sets it for the local backend", "localhost:" + port + ",127.0.0.1:" + port, true},
		{"unset", "", false},
		{"another port listed", "localhost:1,127.0.0.1:1", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("FI_BASE_URL", "")
			t.Setenv("AGENTCC_ADMIN_TOKEN", "standalone-admin-token-0f1e2d3c4b5a6978")
			t.Setenv("AGENTCC_MCP_EGRESS_PRIVATE_ALLOW", tc.allow)
			cfg, err := config.Load(example)
			if err != nil {
				t.Fatalf("config.Load(%s): %v", example, err)
			}
			dialer, err := egress.New(egress.Options{PrivateAllow: cfg.MCP.Egress.PrivateAllow})
			if err != nil {
				t.Fatalf("egress.New: %v", err)
			}
			check := orgfactory.New(dialer)("futureagi-eval", map[string]interface{}{
				"provider":   "futureagi",
				"eval_id":    "15",
				"call_type":  "protect",
				"api_key":    "standalone-org-api-key-test-value",
				"secret_key": "standalone-org-secret-key-test-value",
				"base_url":   backend,
			})
			if check == nil {
				t.Fatal("the org factory built no check")
			}
			before := hits.Load()
			res := check.Check(context.Background(), egressInput())
			reached := hits.Load() > before
			if reached != tc.reach {
				t.Fatalf("local backend reached = %v, want %v (pass=%v message=%q)", reached, tc.reach, res.Pass, res.Message)
			}
			if tc.reach && !res.Pass {
				t.Errorf("the check reached the backend but did not pass: %q", res.Message)
			}
		})
	}
}

func mustURL(t *testing.T, raw string) *url.URL {
	t.Helper()
	u, err := url.Parse(raw)
	if err != nil {
		t.Fatal(err)
	}
	return u
}

func egressInput() *guardrails.CheckInput {
	return &guardrails.CheckInput{
		Request:  orgRequestContext("org-standalone").Request,
		Metadata: map[string]string{"org_id": "org-standalone"},
	}
}
