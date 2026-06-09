package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func okHandler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
}

func TestCORS_Disabled(t *testing.T) {
	// No middleware applied when disabled; just test a normal request.
	handler := okHandler()
	req := httptest.NewRequest("GET", "/v1/chat/completions", nil)
	req.Header.Set("Origin", "https://app.example.com")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Header().Get("Access-Control-Allow-Origin") != "" {
		t.Error("expected no CORS headers without middleware")
	}
}

func TestCORS_PreflightWildcard(t *testing.T) {
	cfg := config.CORSConfig{Enabled: true}
	handler := CORS(cfg)(okHandler())

	req := httptest.NewRequest("OPTIONS", "/v1/chat/completions", nil)
	req.Header.Set("Origin", "https://app.example.com")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusNoContent {
		t.Fatalf("expected 204, got %d", rec.Code)
	}
	if got := rec.Header().Get("Access-Control-Allow-Origin"); got != "*" {
		t.Errorf("expected *, got %q", got)
	}
	if got := rec.Header().Get("Access-Control-Allow-Methods"); got == "" {
		t.Error("expected non-empty Allow-Methods")
	}
	if got := rec.Header().Get("Access-Control-Allow-Headers"); got == "" {
		t.Error("expected non-empty Allow-Headers")
	}
	if got := rec.Header().Get("Access-Control-Max-Age"); got != "86400" {
		t.Errorf("expected max-age 86400, got %q", got)
	}
}

func TestCORS_SimpleRequest(t *testing.T) {
	cfg := config.CORSConfig{Enabled: true}
	handler := CORS(cfg)(okHandler())

	req := httptest.NewRequest("POST", "/v1/chat/completions", nil)
	req.Header.Set("Origin", "https://app.example.com")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
	if got := rec.Header().Get("Access-Control-Allow-Origin"); got != "*" {
		t.Errorf("expected *, got %q", got)
	}
	if got := rec.Header().Get("Access-Control-Expose-Headers"); got == "" {
		t.Error("expected non-empty Expose-Headers")
	}
}

func TestCORS_SpecificOrigins(t *testing.T) {
	cfg := config.CORSConfig{
		Enabled:        true,
		AllowedOrigins: []string{"https://allowed.com", "https://also-allowed.com"},
	}
	handler := CORS(cfg)(okHandler())

	// Allowed origin.
	req := httptest.NewRequest("POST", "/v1/chat/completions", nil)
	req.Header.Set("Origin", "https://allowed.com")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if got := rec.Header().Get("Access-Control-Allow-Origin"); got != "https://allowed.com" {
		t.Errorf("expected https://allowed.com, got %q", got)
	}
	if rec.Header().Get("Vary") != "Origin" {
		t.Error("expected Vary: Origin for specific origin")
	}

	// Disallowed origin — no CORS headers.
	req2 := httptest.NewRequest("POST", "/v1/chat/completions", nil)
	req2.Header.Set("Origin", "https://evil.com")
	rec2 := httptest.NewRecorder()
	handler.ServeHTTP(rec2, req2)
	if got := rec2.Header().Get("Access-Control-Allow-Origin"); got != "" {
		t.Errorf("expected no CORS header for disallowed origin, got %q", got)
	}
}

func TestCORS_NoOriginHeader(t *testing.T) {
	cfg := config.CORSConfig{Enabled: true}
	handler := CORS(cfg)(okHandler())

	req := httptest.NewRequest("GET", "/healthz", nil)
	// No Origin header.
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if got := rec.Header().Get("Access-Control-Allow-Origin"); got != "" {
		t.Errorf("expected no CORS header without Origin, got %q", got)
	}
}

func TestCORS_Credentials(t *testing.T) {
	cfg := config.CORSConfig{
		Enabled:          true,
		AllowedOrigins:   []string{"https://app.example.com"},
		AllowCredentials: true,
	}
	handler := CORS(cfg)(okHandler())

	req := httptest.NewRequest("POST", "/v1/chat/completions", nil)
	req.Header.Set("Origin", "https://app.example.com")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if got := rec.Header().Get("Access-Control-Allow-Credentials"); got != "true" {
		t.Errorf("expected Allow-Credentials: true, got %q", got)
	}
	// Must use specific origin, not "*", when credentials are enabled.
	if got := rec.Header().Get("Access-Control-Allow-Origin"); got != "https://app.example.com" {
		t.Errorf("expected specific origin with credentials, got %q", got)
	}
}

func TestCORS_CustomMaxAge(t *testing.T) {
	cfg := config.CORSConfig{
		Enabled: true,
		MaxAge:  3600,
	}
	handler := CORS(cfg)(okHandler())

	req := httptest.NewRequest("OPTIONS", "/v1/chat/completions", nil)
	req.Header.Set("Origin", "https://app.example.com")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if got := rec.Header().Get("Access-Control-Max-Age"); got != "3600" {
		t.Errorf("expected max-age 3600, got %q", got)
	}
}

func TestCORS_WildcardWithCredentials(t *testing.T) {
	cfg := config.CORSConfig{
		Enabled:          true,
		AllowedOrigins:   []string{"*"},
		AllowCredentials: true,
	}
	handler := CORS(cfg)(okHandler())

	req := httptest.NewRequest("POST", "/v1/chat/completions", nil)
	req.Header.Set("Origin", "https://app.example.com")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	// With credentials, must reflect the specific origin, not "*".
	if got := rec.Header().Get("Access-Control-Allow-Origin"); got != "https://app.example.com" {
		t.Errorf("expected specific origin with credentials, got %q", got)
	}
}
