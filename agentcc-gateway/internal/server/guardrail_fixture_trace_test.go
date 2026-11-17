package server

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// These guardrail fixtures do not test trace generation. Supply a short trace
// label rather than printing a second random ULID: a request ULID can contain
// KEY, making the following random trace_id look like a credential in stdout.
// Real request IDs, explicit caller traces, logging and all assertions stay on.
func guardrailFixtureTrace(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("x-agentcc-trace-id") == "" {
			r.Header.Set("x-agentcc-trace-id", "test-trace")
		}
		next.ServeHTTP(w, r)
	})
}

func TestGuardrailFixtureTraceKeepsRequestIdentity(t *testing.T) {
	g := newPostBlockGateway(t, postBlockSetup{})
	seen := map[string]bool{}
	for _, trace := range []string{"", "", "caller-trace"} {
		r := httptest.NewRequest(http.MethodGet, "/healthz", nil)
		if trace != "" {
			r.Header.Set("x-agentcc-trace-id", trace)
		}
		w := httptest.NewRecorder()
		g.srv.httpServer.Handler.ServeHTTP(w, r)
		want := trace
		if want == "" {
			want = "test-trace"
		}
		if w.Header().Get("x-agentcc-trace-id") != want {
			t.Error("fixture trace was not preserved")
		}
		id := w.Header().Get("x-agentcc-request-id")
		if id == "" || seen[id] {
			t.Error("fixture must retain distinct gateway request IDs")
		}
		seen[id] = true
	}
}
