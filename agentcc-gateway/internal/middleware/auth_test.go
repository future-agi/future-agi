package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestKeyAuthFailsClosedForNonPublicRoutes(t *testing.T) {
	keyStore := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{
		Name: "test",
		Key:  "valid-key",
	}}})
	handler := KeyAuth(keyStore, true)(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))

	for _, path := range []string{"/v1/models", "/mcp", "/a2a", "/future-route"} {
		t.Run(path, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodGet, path, nil)
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			if response.Code != http.StatusUnauthorized {
				t.Fatalf("expected 401 for %s, got %d", path, response.Code)
			}
		})
	}
}

func TestKeyAuthExplicitBypasses(t *testing.T) {
	keyStore := auth.NewKeyStore(config.AuthConfig{})
	handler := KeyAuth(keyStore, true)(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
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
	handler := KeyAuth(keyStore, true)(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
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
