package server

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Authored, not run. This dev-compatible HTTP characterization is expected to
// fail at 1a1f for lowercase/Token and query credentials on middleware-only routes.
// It deliberately leaves pipeline, stored, speech and native handler auth alone.
func TestRuntimeR5MiddlewareAuthHTTP(t *testing.T) {
	t.Run("legacy", func(t *testing.T) {
		f := newRuntimeR5Fixture(t, &tenant.GuardrailConfig{}, `{"input_tokens":1}`, nil)
		for _, route := range []struct {
			method, path, body string
			status             int
		}{
			{"POST", "/v1/count_tokens", `{"model":"gpt-5.5","messages":[{"role":"user","content":"hello"}]}`, 200},
			{"GET", "/v1/decisions/models", "", 200},
			{"GET", "/v1/models", "", 200},
			{"GET", "/v1/models/gpt-5.5", "", 200},
			{"POST", "/v1/scheduled", `{}`, 501},
			{"GET", "/v1/scheduled", "", 404},
			{"GET", "/v1/scheduled/synthetic", "", 404},
			{"DELETE", "/v1/scheduled/synthetic", "", 404},
			{"GET", "/mcp", "", 400},
			{"GET", "/v1/.well-known/agent.json", "", 200},
			{"GET", "/v1/agents", "", 200},
			{"POST", "/a2a", `{"jsonrpc":"2.0","id":1,"method":"tasks/get","params":{"id":"synthetic"}}`, 200},
			{"POST", "/v1/a2a", `{"jsonrpc":"2.0","id":1,"method":"tasks/get","params":{"id":"synthetic"}}`, 200},
		} {
			for _, scheme := range []string{"Bearer", "bearer", "BEARER", "Token", "tOkEn", "Basic"} {
				t.Run(route.method+route.path+"/"+scheme, func(t *testing.T) {
					status, body, calls := f.send(t, route.method, route.path, route.body, map[string]string{"Authorization": scheme + " sk-agentcc-r5-a"})
					want := route.status
					if scheme == "Basic" {
						want = 401
					}
					if status != want || len(calls) != 0 {
						t.Fatalf("status=%d want=%d calls=%d body=%s", status, want, len(calls), body)
					}
				})
			}
			for _, tc := range []struct {
				name, query string
				headers     map[string]string
			}{
				{"api-header", "", map[string]string{"x-api-key": "sk-agentcc-r5-a"}},
				{"google-header", "", map[string]string{"x-goog-api-key": "sk-agentcc-r5-a"}},
				{"query-key", "?key=sk-agentcc-r5-a", nil},
				{"query-api-key", "?api_key=sk-agentcc-r5-a", nil},
				{"invalid-authorization-falls-through", "", map[string]string{"Authorization": "Bearer invalid", "x-api-key": "sk-agentcc-r5-a"}},
				{"invalid-header-falls-through-to-query", "?key=sk-agentcc-r5-a", map[string]string{"Authorization": "Basic invalid", "x-api-key": "invalid"}},
			} {
				t.Run(route.method+route.path+"/"+tc.name, func(t *testing.T) {
					status, body, calls := f.send(t, route.method, route.path+tc.query, route.body, tc.headers)
					if status != route.status || len(calls) != 0 {
						t.Fatalf("status=%d want=%d calls=%d body=%s", status, route.status, len(calls), body)
					}
				})
			}
		}
	})
}

func TestRuntimeR5SelectedMiddlewareAuthHTTP(t *testing.T) {
	gr := &tenant.GuardrailConfig{}
	runtimeR5Selected(t, gr, map[string]*tenant.GuardrailCheck{"input-validation": {Enabled: true, Action: "block", ConfidenceThreshold: .5, Config: map[string]interface{}{"min_message_length": 1}}})
	f := newRuntimeR5Fixture(t, gr, `unused`, nil)
	for _, scheme := range []string{"Bearer", "bearer", "BEARER", "Token"} {
		status, body, calls := f.send(t, "POST", "/v1/count_tokens", `{"model":"gpt-5.5","messages":[{"role":"user","content":"hello"}]}`, map[string]string{"Authorization": scheme + " sk-agentcc-r5-a"})
		if status != 403 || !strings.Contains(string(body), "input_guardrail_unsupported") || len(calls) != 0 {
			t.Fatalf("valid selected identity not admitted consistently: status=%d body=%s", status, body)
		}
	}
	// Invalid candidates are skipped only in this middleware-only family.
	status, body, _ := f.send(t, "POST", "/v1/count_tokens", `{}`, map[string]string{"Authorization": "Bearer invalid", "x-api-key": "sk-agentcc-r5-a"})
	if status != 403 || !strings.Contains(string(body), "input_guardrail_unsupported") {
		t.Fatalf("fallback selected identity lost: %d %s", status, body)
	}
	status, body, _ = f.send(t, "POST", "/v1/count_tokens", `{"model":"gpt-5.5","messages":[{"role":"user","content":"hello"}]}`, map[string]string{"Authorization": "Token sk-agentcc-r5-b", "x-api-key": "sk-agentcc-r5-a"})
	if status != 200 {
		t.Fatalf("later selected candidate displaced first valid unselected key: %d %s", status, body)
	}
}

