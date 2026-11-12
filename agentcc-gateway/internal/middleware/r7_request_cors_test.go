package middleware

// A browser may send every documented request
// header and read every documented response header.

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func r7Preflight(t *testing.T, requested string) http.Header {
	t.Helper()
	h := CORS(config.CORSConfig{Enabled: true})(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	req := httptest.NewRequest(http.MethodOptions, "/v1/chat/completions", nil)
	req.Header.Set("Origin", "https://app.example")
	req.Header.Set("Access-Control-Request-Method", "POST")
	if requested != "" {
		req.Header.Set("Access-Control-Request-Headers", requested)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	return w.Header()
}

func r7Lists(list, name string) bool {
	for _, v := range strings.Split(list, ",") {
		if strings.EqualFold(strings.TrimSpace(v), name) {
			return true
		}
	}
	return false
}

func TestR7AlwaysPresentHeadersCORSExposesEveryDocumentedHeader(t *testing.T) {
	hdr := r7Preflight(t, "")
	allow, expose := hdr.Get("Access-Control-Allow-Headers"), hdr.Get("Access-Control-Expose-Headers")
	for _, h := range []string{
		"x-agentcc-trace-id", "x-agentcc-session-id", "x-agentcc-session-name", "x-agentcc-session-path",
		"x-agentcc-request-id", "x-agentcc-user-id", "x-agentcc-metadata", "x-agentcc-cache-ttl",
		"x-agentcc-cache-namespace", "x-agentcc-cache-force-refresh", "cache-control", "x-agentcc-provider-lock",
		"x-agentcc-complexity-override", "x-agentcc-guardrail-policy", "x-guardrail-policy", "x-agentcc-config",
		"x-agentcc-request-timeout", "x-agentcc-timeout", "x-agentcc-max-attempts", "x-agentcc-retry-delay-ms",
		"x-agentcc-backoff", "x-agentcc-auto-preference", "x-agentcc-log-content",
	} {
		if !r7Lists(allow, h) {
			t.Errorf("Access-Control-Allow-Headers lacks the documented request header %s", h)
		}
	}
	for _, h := range []string{
		"x-agentcc-request-id", "x-agentcc-trace-id", "x-agentcc-provider", "x-agentcc-model-used",
		"x-agentcc-latency-ms", "x-agentcc-timeout-ms", "x-agentcc-cost", "x-agentcc-cache",
		"x-agentcc-client-request-id", "x-agentcc-guardrail-triggered", "x-agentcc-guardrail-action",
		"x-agentcc-guardrails-triggered", "x-agentcc-fallback-used", "x-agentcc-routing-strategy",
		"x-agentcc-credits-remaining", "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests",
		"x-ratelimit-reset-requests",
		// Kept: the names exposed before.
		"x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset",
	} {
		if !r7Lists(expose, h) {
			t.Errorf("Access-Control-Expose-Headers lacks the documented response header %s", h)
		}
	}
}

func TestR7PropertyHeadersCORSPreflightAllowsTheRequestedProperties(t *testing.T) {
	allow := r7Preflight(t, "content-type, x-agentcc-property-env, X-Agentcc-Property-Cost_Center, x-other").Get("Access-Control-Allow-Headers")
	for _, h := range []string{"x-agentcc-property-env", "x-agentcc-property-cost_center"} {
		if !r7Lists(allow, h) {
			t.Errorf("a preflight asking for %s is not allowed it: %s", h, allow)
		}
	}
	if r7Lists(allow, "x-other") {
		t.Errorf("a header that is not a property was allowed: %s", allow)
	}
	// An operator's own allowed list is kept as configured.
	h := CORS(config.CORSConfig{Enabled: true, AllowedHeaders: []string{"Authorization"}})(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	req := httptest.NewRequest(http.MethodOptions, "/v1/chat/completions", nil)
	req.Header.Set("Origin", "https://app.example")
	req.Header.Set("Access-Control-Request-Headers", "x-agentcc-property-env")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if got := w.Header().Get("Access-Control-Allow-Headers"); got != "Authorization" {
		t.Errorf("configured allowed headers became %q", got)
	}
}
