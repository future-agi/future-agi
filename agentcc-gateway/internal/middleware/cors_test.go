package middleware

import (
	"net/http"
	"net/http/httptest"
	"strings"
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

func TestCORS_BrowserEvidenceExposure(t *testing.T) {
	const origin = "https://app.example.com"
	allowed := config.CORSConfig{Enabled: true, AllowedOrigins: []string{origin}}
	// Keep the expected exposure independent of the middleware's defaults so
	// existing browser-readable evidence cannot accidentally be dropped.
	exposed := strings.Join([]string{
		"X-Agentcc-Request-Id", "X-Agentcc-Trace-Id", "X-Agentcc-Provider",
		"X-Agentcc-Model-Used", "X-Agentcc-Latency-Ms", "X-Agentcc-Cost",
		"X-Agentcc-Cache", "X-Agentcc-Timeout-Ms", "X-RateLimit-Limit",
		"X-RateLimit-Remaining", "X-RateLimit-Reset", "X-Agentcc-Client-Request-Id",
		"X-Agentcc-Config-Ignored", "X-Agentcc-Guardrail-Triggered",
		"X-Agentcc-Guardrail-Action", "X-Agentcc-Guardrails-Triggered",
		"X-Agentcc-Fallback-Used", "X-Agentcc-Routing-Strategy",
		"X-Agentcc-Credits-Remaining", "X-Ratelimit-Limit-Requests",
		"X-Ratelimit-Remaining-Requests", "X-Ratelimit-Reset-Requests",
		"X-Agentcc-Billing-Mode", "Retry-After",
	}, ", ")

	for _, tc := range []struct {
		name            string
		cfg             config.CORSConfig
		origin          string
		method          string
		status          int
		billingMode     string
		retryAfter      string
		wantOrigin      string
		wantCredentials string
		wantExposed     string
		wantMethods     string
		wantHeaders     string
		wantMaxAge      string
	}{
		{
			name: "allowed_success", cfg: allowed, origin: origin, method: http.MethodPost,
			status: http.StatusOK, billingMode: "managed", retryAfter: "5",
			wantOrigin: origin, wantExposed: exposed,
		},
		{
			name: "allowed_rate_limited", cfg: allowed, origin: origin, method: http.MethodPost,
			status: http.StatusTooManyRequests, billingMode: "byok", retryAfter: "30",
			wantOrigin: origin, wantExposed: exposed,
		},
		{
			name: "allowed_retry_http_date", cfg: allowed, origin: origin, method: http.MethodPost,
			status: http.StatusTooManyRequests, billingMode: "managed", retryAfter: "Wed, 11 Nov 2026 16:00:00 GMT",
			wantOrigin: origin, wantExposed: exposed,
		},
		{
			name: "absent_evidence_stays_absent", cfg: allowed, origin: origin, method: http.MethodPost,
			status: http.StatusOK, wantOrigin: origin, wantExposed: exposed,
		},
		{
			name: "custom_exposed_headers_override",
			cfg: config.CORSConfig{
				Enabled: true, AllowedOrigins: []string{origin}, ExposedHeaders: []string{"X-Custom-Evidence"},
			},
			origin: origin, method: http.MethodPost,
			status: http.StatusTooManyRequests, billingMode: "byok", retryAfter: "30",
			wantOrigin: origin, wantExposed: "X-Custom-Evidence",
		},
		{
			name: "disallowed_origin", cfg: allowed, origin: "https://other.example.com", method: http.MethodPost,
			status: http.StatusTooManyRequests, billingMode: "byok", retryAfter: "30",
		},
		{
			name: "no_origin", cfg: allowed, method: http.MethodPost,
			status: http.StatusOK, billingMode: "managed", retryAfter: "5",
		},
		{
			name: "wildcard_without_credentials", cfg: config.CORSConfig{Enabled: true},
			origin: origin, method: http.MethodPost,
			status: http.StatusOK, billingMode: "managed", retryAfter: "5",
			wantOrigin: "*", wantExposed: exposed,
		},
		{
			name:   "wildcard_with_credentials",
			cfg:    config.CORSConfig{Enabled: true, AllowedOrigins: []string{"*"}, AllowCredentials: true},
			origin: origin, method: http.MethodPost,
			status: http.StatusOK, billingMode: "managed", retryAfter: "5",
			wantOrigin: origin, wantCredentials: "true", wantExposed: exposed,
		},
		{
			name: "default_preflight", cfg: allowed, origin: origin, method: http.MethodOptions,
			status: http.StatusNoContent, wantOrigin: origin, wantExposed: exposed,
			wantMethods: "GET, POST, PUT, DELETE, PATCH, OPTIONS",
			wantHeaders: strings.Join(defaultCORSHeaders, ", "), wantMaxAge: "86400",
		},
		{
			name: "custom_preflight",
			cfg: config.CORSConfig{
				Enabled: true, AllowedOrigins: []string{origin}, AllowCredentials: true,
				AllowedMethods: []string{"POST"}, AllowedHeaders: []string{"Content-Type"},
				ExposedHeaders: []string{"X-Custom-Evidence"}, MaxAge: 3600,
			},
			origin: origin, method: http.MethodOptions,
			status: http.StatusNoContent, wantOrigin: origin, wantCredentials: "true",
			wantExposed: "X-Custom-Evidence", wantMethods: "POST", wantHeaders: "Content-Type", wantMaxAge: "3600",
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			calls := 0
			handler := CORS(tc.cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if tc.billingMode != "" {
					w.Header().Set("X-Agentcc-Billing-Mode", tc.billingMode)
				}
				if tc.retryAfter != "" {
					w.Header().Set("Retry-After", tc.retryAfter)
				}
				w.WriteHeader(tc.status)
				_, _ = w.Write([]byte(http.StatusText(tc.status)))
			}))
			req := httptest.NewRequest(tc.method, "/v1/chat/completions", nil)
			if tc.origin != "" {
				req.Header.Set("Origin", tc.origin)
			}
			if tc.method == http.MethodOptions {
				req.Header.Set("Access-Control-Request-Method", "POST")
				req.Header.Set("Access-Control-Request-Headers", "Content-Type")
			}
			rec := httptest.NewRecorder()
			handler.ServeHTTP(rec, req)

			if rec.Code != tc.status {
				t.Errorf("status = %d, want %d", rec.Code, tc.status)
			}
			wantVary := ""
			if tc.wantOrigin != "" && tc.wantOrigin != "*" {
				wantVary = "Origin"
			}
			for name, want := range map[string]string{
				"Access-Control-Allow-Origin":      tc.wantOrigin,
				"Access-Control-Allow-Credentials": tc.wantCredentials,
				"Access-Control-Expose-Headers":    tc.wantExposed,
				"Access-Control-Allow-Methods":     tc.wantMethods,
				"Access-Control-Allow-Headers":     tc.wantHeaders,
				"Access-Control-Max-Age":           tc.wantMaxAge,
				"Vary":                             wantVary,
				"X-Agentcc-Billing-Mode":           tc.billingMode,
				"Retry-After":                      tc.retryAfter,
			} {
				if got := rec.Header().Get(name); got != want {
					t.Errorf("%s = %q, want %q", name, got, want)
				}
			}
			wantCalls, wantBody := 1, http.StatusText(tc.status)
			if tc.method == http.MethodOptions {
				wantCalls, wantBody = 0, ""
				for _, name := range strings.Split(rec.Header().Get("Access-Control-Allow-Headers"), ",") {
					if strings.EqualFold(strings.TrimSpace(name), "X-Agentcc-Billing-Mode") ||
						strings.EqualFold(strings.TrimSpace(name), "Retry-After") {
						t.Errorf("response evidence must not expand allowed request headers: %q", name)
					}
				}
			}
			if calls != wantCalls || rec.Body.String() != wantBody {
				t.Errorf("handler calls/body = %d/%q, want %d/%q", calls, rec.Body.String(), wantCalls, wantBody)
			}
		})
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
