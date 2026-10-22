package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestKeyGateCoversV1AndItsRootsOnTheCanonicalPath(t *testing.T) {
	g := newKeyGate([]string{"/mcp/", "/a2a", ""})
	for _, p := range []string{
		"/v1/models", "//v1/models",
		"/mcp", "//mcp", "/mcp/", "/./mcp", "/zz/../mcp", "/mcp/sse", "/mcp/messages", "/mcp//x",
		"/a2a", "/a2a/", "//a2a", "/a2a/x/y",
	} {
		if !g.covers(p) {
			t.Errorf("covers(%q) = false, want true", p)
		}
	}
	for _, p := range []string{
		"/", "/healthz", "/-/keys", "/v1", "/v1beta/models/x",
		"/mcpx", "/mcp-admin", "/a2ab", "/.well-known/agent.json", "/-/mcp/status",
	} {
		if g.covers(p) {
			t.Errorf("covers(%q) = true, want false", p)
		}
	}
}

func TestKeyAuthGatesItsRootsOnEveryMethodAndSpelling(t *testing.T) {
	ks := auth.NewKeyStore(config.AuthConfig{Enabled: true, Keys: []config.AuthKeyConfig{{
		Name: "k", Key: "sk-root", Owner: "o", KeyType: "internal",
	}}})
	gate := KeyAuth(ks, true, nil, "/mcp", "/a2a")

	for _, target := range []string{"/mcp", "//mcp", "/mcp/", "/./mcp", "/zz/../mcp", "/%2Fmcp", "/mcp/sse", "/a2a", "//a2a/x"} {
		for _, method := range []string{http.MethodGet, http.MethodPost, http.MethodDelete, http.MethodPut} {
			rr := httptest.NewRecorder()
			gate(mustNotRun(t)).ServeHTTP(rr, httptest.NewRequest(method, target, nil))
			if rr.Code != http.StatusUnauthorized {
				t.Fatalf("%s %s with no key: status %d, want 401", method, target, rr.Code)
			}
			if got := rr.Header().Get("WWW-Authenticate"); got != models.BearerChallenge {
				t.Fatalf("%s %s with no key: WWW-Authenticate %q, want %q", method, target, got, models.BearerChallenge)
			}
		}
	}

	for _, header := range []string{"Authorization", "x-api-key"} {
		called := false
		ok := gate(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { called = true }))
		req := httptest.NewRequest(http.MethodPost, "/mcp", nil)
		value := "sk-root"
		if header == "Authorization" {
			value = "Bearer sk-root"
		}
		req.Header.Set(header, value)
		ok.ServeHTTP(httptest.NewRecorder(), req)
		if !called {
			t.Fatalf("a valid key in %s on /mcp did not reach the handler", header)
		}
	}

	// With auth off the roots pass through, as /v1 does.
	called := false
	off := KeyAuth(ks, false, nil, "/mcp")(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { called = true }))
	off.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodPost, "/mcp", nil))
	if !called {
		t.Fatal("with auth off, /mcp was gated")
	}
}
