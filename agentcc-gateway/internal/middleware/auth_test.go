package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

// testGatedRoots are the roots server.go gates besides /v1 with MCP and A2A
// on: the native Google API, the speech vendors' relays, /mcp and /a2a.
func testGatedRoots() []string {
	return append([]string{GenAIRoot, "/mcp", "/a2a"}, SpeechRelayRoots()...)
}

func TestKeyAuthFailsClosedForNonPublicRoutes(t *testing.T) {
	keyStore := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{
		Name: "test",
		Key:  "valid-key",
	}}})
	handler := KeyAuth(keyStore, true, nil, testGatedRoots()...)(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))

	for _, tc := range []struct {
		path string
		want int
	}{
		{"/v1/models", http.StatusUnauthorized},
		{"/mcp", http.StatusUnauthorized},
		{"/a2a", http.StatusUnauthorized},
		// DEV CHANGE NOT KEPT: dev gates every path that is not public. The
		// line gates its served API roots (/v1, /v1beta, the speech relays,
		// /mcp, /a2a), so a path outside them reaches the next handler; the
		// server answers it 404 (TestUnregisteredPathsKeepTheirNotFound).
		{"/future-route", http.StatusNoContent},
	} {
		path := tc.path
		t.Run(path, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodGet, path, nil)
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			if response.Code != tc.want {
				t.Fatalf("expected %d for %s, got %d", tc.want, path, response.Code)
			}
		})
	}
}

func TestKeyAuthExplicitBypasses(t *testing.T) {
	keyStore := auth.NewKeyStore(config.AuthConfig{})
	handler := KeyAuth(keyStore, true, nil, testGatedRoots()...)(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))

	for _, path := range []string{"/healthz", "/readyz", "/livez", "/.well-known/agent.json", "/-/config"} {
		t.Run(path, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodGet, path, nil)
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			if response.Code != http.StatusNoContent {
				t.Fatalf("expected bypass for %s, got %d", path, response.Code)
			}
		})
	}
}

func TestKeyAuthAcceptsValidKeyOnMCP(t *testing.T) {
	keyStore := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{
		Name: "test",
		Key:  "valid-key",
	}}})
	handler := KeyAuth(keyStore, true, nil, testGatedRoots()...)(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))

	request := httptest.NewRequest(http.MethodPost, "/mcp", nil)
	request.Header.Set("Authorization", "Bearer valid-key")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusNoContent {
		t.Fatalf("expected authenticated MCP request to pass, got %d", response.Code)
	}
}

func TestKeyAuthAcceptsValidGeminiAPIKey(t *testing.T) {
	keyStore := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{
		Name: "test",
		Key:  "valid-key",
	}}})
	handler := KeyAuth(keyStore, true, nil, testGatedRoots()...)(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))

	for _, test := range []struct {
		name   string
		path   string
		header string
		apiKey string
	}{
		{name: "x-goog-api-key header", path: "/v1beta/models/gemini", header: "x-goog-api-key", apiKey: "valid-key"},
		{name: "key query parameter", path: "/v1beta/models/gemini?key=valid-key"},
	} {
		t.Run(test.name, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodGet, test.path, nil)
			if test.header != "" {
				request.Header.Set(test.header, test.apiKey)
			}
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			if response.Code != http.StatusNoContent {
				t.Fatalf("expected authenticated Gemini request to pass, got %d", response.Code)
			}
		})
	}
}
