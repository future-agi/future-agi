package middleware

// The documented request headers the RequestID
// middleware reads before any other reader: x-agentcc-config, the
// x-agentcc-guardrail-policy alias, x-agentcc-property-<name>, the session
// name and path and the client's x-agentcc-request-id; and the
// x-agentcc-latency-ms response header on every response.

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/oklog/ulid/v2"
)

// headerSeen is what the handler behind RequestID received.
type headerSeen struct {
	called bool
	header http.Header
	reqID  string
}

// serveHeaders sends one request with hdr through RequestID to a handler that
// records what it got and answers 200.
func serveHeaders(t *testing.T, hdr map[string]string) (*httptest.ResponseRecorder, *headerSeen) {
	t.Helper()
	seen := &headerSeen{}
	h := RequestID(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen.called = true
		seen.header = r.Header.Clone()
		seen.reqID = models.GetRequestID(r.Context())
		w.WriteHeader(http.StatusOK)
	}))
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(`{}`))
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	return w, seen
}

// errorOf is the error object of a refusal body.
func errorOf(t *testing.T, w *httptest.ResponseRecorder) models.ErrorDetail {
	t.Helper()
	var e models.ErrorResponse
	if err := json.Unmarshal(w.Body.Bytes(), &e); err != nil {
		t.Fatalf("body %q is not an error response: %v", w.Body.String(), err)
	}
	return e.Error
}

// metadataOf is the x-agentcc-metadata object the handler received.
func metadataOf(t *testing.T, seen *headerSeen) map[string]string {
	t.Helper()
	raw := seen.header.Get("x-agentcc-metadata")
	if raw == "" {
		return nil
	}
	m := map[string]string{}
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		t.Fatalf("x-agentcc-metadata %q is not a JSON object of strings: %v", raw, err)
	}
	return m
}

func TestAgentccConfigHeaderExpandsIntoTheDocumentedHeaders(t *testing.T) {
	w, seen := serveHeaders(t, map[string]string{"x-agentcc-config": `{
		"request-timeout": 1234, "timeout": "2s", "cache-ttl": 300, "cache-namespace": "prod",
		"cache-force-refresh": true, "session-id": "s-1", "max-attempts": 2, "retry-delay-ms": "10",
		"backoff": "constant", "trace-id": "trace-from-config", "user-id": "user-42",
		"provider-lock": "openai", "complexity-override": "simple", "auto-preference": "quality",
		"log-content": "none", "max-cost-usd": 0.5, "route-hint": "latency", "run-id": "run-1",
		"metadata": {"team": "ml"}}`})
	if w.Code != http.StatusOK || !seen.called {
		t.Fatalf("status %d (handler called %v), want 200; body %s", w.Code, seen.called, w.Body.String())
	}
	want := map[string]string{
		"x-agentcc-request-timeout":     "1234",
		"x-agentcc-timeout":             "2s",
		"x-agentcc-cache-ttl":           "300",
		"x-agentcc-cache-namespace":     "prod",
		"x-agentcc-cache-force-refresh": "true",
		"x-agentcc-session-id":          "s-1",
		"x-agentcc-max-attempts":        "2",
		"x-agentcc-retry-delay-ms":      "10",
		"x-agentcc-backoff":             "constant",
		"x-agentcc-trace-id":            "trace-from-config",
		"x-agentcc-user-id":             "user-42",
		"x-agentcc-provider-lock":       "openai",
		"x-agentcc-complexity-override": "simple",
		"x-agentcc-auto-preference":     "quality",
		"x-agentcc-log-content":         "none",
		"x-agentcc-max-cost-usd":        "0.5",
		"x-agentcc-route-hint":          "latency",
		"x-agentcc-run-id":              "run-1",
	}
	for h, v := range want {
		if got := seen.header.Get(h); got != v {
			t.Errorf("%s = %q, want %q from x-agentcc-config", h, got, v)
		}
	}
	if got := metadataOf(t, seen)["team"]; got != "ml" {
		t.Errorf("metadata team = %q, want ml from x-agentcc-config", got)
	}
	// The trace id is read after the expansion: the response carries the
	// config's.
	if got := w.Header().Get("x-agentcc-trace-id"); got != "trace-from-config" {
		t.Errorf("response x-agentcc-trace-id = %q, want the config's trace-from-config", got)
	}
}

func TestAgentccConfigHeaderExplicitHeaderWins(t *testing.T) {
	_, seen := serveHeaders(t, map[string]string{
		"x-agentcc-config":          `{"cache-namespace":"from-config","request-timeout":5000,"cache-force-refresh":false}`,
		"x-agentcc-cache-namespace": "explicit",
	})
	if got := seen.header.Get("x-agentcc-cache-namespace"); got != "explicit" {
		t.Errorf("x-agentcc-cache-namespace = %q, want the explicit header's value", got)
	}
	if got := seen.header.Get("x-agentcc-request-timeout"); got != "5000" {
		t.Errorf("x-agentcc-request-timeout = %q, want 5000 from the config", got)
	}
	if v, ok := seen.header["X-Agentcc-Cache-Force-Refresh"]; ok {
		t.Errorf("cache-force-refresh false set the header %q; false asks for nothing", v)
	}
}

func TestAgentccConfigHeaderRefusesWhatItCannotApply(t *testing.T) {
	big := `{"metadata":{"k":"` + strings.Repeat("x", 16*1024) + `"}}`
	cases := []struct {
		name, config, wantInMsg string
	}{
		{"not JSON", `this is not json`, "x-agentcc-config"},
		{"an array", `[1,2]`, "JSON object"},
		{"trailing data", `{} {}`, "x-agentcc-config"},
		// The SDK's nested GatewayConfig sections (and a numeric timeout)
		// are answered 200: agentcc_config_sections_test.go.
		{"unknown key", `{"timeouts":{"total":1234}}`, `"timeouts"`},
		{"timeout not a duration", `{"timeout":"soon"}`, `"timeout"`},
		{"request-timeout not a number", `{"request-timeout":"abc"}`, `"request-timeout"`},
		{"request-timeout negative", `{"request-timeout":-5}`, `"request-timeout"`},
		{"request-timeout fractional", `{"request-timeout":1.5}`, `"request-timeout"`},
		{"cache-ttl negative", `{"cache-ttl":-1}`, `"cache-ttl"`},
		{"cache-force-refresh not a bool", `{"cache-force-refresh":"yes"}`, `"cache-force-refresh"`},
		{"null value", `{"session-id":null}`, `"session-id"`},
		{"object for a string", `{"session-id":{"a":1}}`, `"session-id"`},
		{"control character", `{"session-id":"a\r\nX-Injected: 1"}`, `"session-id"`},
		{"duplicate key", `{"cache-ttl":1,"cache-ttl":2}`, `"cache-ttl"`},
		{"metadata not an object", `{"metadata":"[1]"}`, `"metadata"`},
		{"upper-case key", `{"Cache-TTL":30}`, `"Cache-TTL"`},
		{"the config itself", `{"config":"{}"}`, `"config"`},
		{"async mode", `{"async":true}`, `"async"`},
		{"async ttl", `{"async-job-result-ttl":"1h"}`, `"async-job-result-ttl"`},
		{"runtime org id", `{"org-id":"org-other"}`, `"org-id"`},
		{"runtime deployment id", `{"deployment-id":"d-1"}`, `"deployment-id"`},
		{"authorization", `{"authorization":"Bearer sk-x"}`, `"authorization"`},
		{"standard header", `{"cache-control":"no-store"}`, `"cache-control"`},
		{"forwarded for", `{"x-forwarded-for":"10.0.0.1"}`, `"x-forwarded-for"`},
		{"full header name", `{"x-agentcc-cache-ttl":30}`, `"x-agentcc-cache-ttl"`},
		{"larger than 16 KiB", big, "16 KiB"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			w, seen := serveHeaders(t, map[string]string{"x-agentcc-config": tc.config})
			if seen.called {
				t.Fatalf("the handler ran: x-agentcc-config %s was not refused", tc.config)
			}
			if w.Code != http.StatusBadRequest {
				t.Fatalf("status %d, want 400; body %s", w.Code, w.Body.String())
			}
			e := errorOf(t, w)
			if e.Code != "invalid_agentcc_config" || e.Param == nil || *e.Param != "x-agentcc-config" {
				t.Errorf("error code %q param %v, want invalid_agentcc_config naming x-agentcc-config", e.Code, e.Param)
			}
			if !strings.Contains(e.Message, tc.wantInMsg) {
				t.Errorf("message %q does not name %s", e.Message, tc.wantInMsg)
			}
			if w.Header().Get("x-agentcc-request-id") == "" || w.Header().Get("x-agentcc-latency-ms") == "" {
				t.Errorf("the refusal lacks the request id or latency header: %v", w.Header())
			}
		})
	}
}

func TestAgentccConfigHeaderAcceptsUpTo16KiB(t *testing.T) {
	pad := 16*1024 - len(`{"metadata":{"k":""}}`)
	config := `{"metadata":{"k":"` + strings.Repeat("x", pad) + `"}}`
	if len(config) != 16*1024 {
		t.Fatalf("test config is %d bytes, want 16384", len(config))
	}
	w, seen := serveHeaders(t, map[string]string{"x-agentcc-config": config})
	if w.Code != http.StatusOK || !seen.called {
		t.Fatalf("a 16 KiB config: status %d, want 200; body %s", w.Code, w.Body.String())
	}
}

func TestGuardrailPolicyAliasIsReadAsXGuardrailPolicy(t *testing.T) {
	_, seen := serveHeaders(t, map[string]string{"x-agentcc-guardrail-policy": "log-only"})
	if got := seen.header.Get("X-Guardrail-Policy"); got != "log-only" {
		t.Errorf("X-Guardrail-Policy = %q, want the alias's log-only", got)
	}
	_, seen = serveHeaders(t, map[string]string{"x-agentcc-guardrail-policy": "log-only", "X-Guardrail-Policy": "strict"})
	if got := seen.header.Get("X-Guardrail-Policy"); got != "strict" {
		t.Errorf("X-Guardrail-Policy = %q, want the explicit strict (the alias never overrides it)", got)
	}
	_, seen = serveHeaders(t, map[string]string{"x-agentcc-config": `{"guardrail-policy":"disabled"}`})
	if got := seen.header.Get("X-Guardrail-Policy"); got != "disabled" {
		t.Errorf("X-Guardrail-Policy = %q, want disabled from x-agentcc-config's guardrail-policy", got)
	}
}

func TestPropertyHeadersEnterTheMetadataHeader(t *testing.T) {
	_, seen := serveHeaders(t, map[string]string{
		"x-agentcc-property-env":  "prod",
		"X-Agentcc-Property-Team": "ml",
		"x-agentcc-metadata":      `{"feature":"search","env":"staging"}`,
	})
	m := metadataOf(t, seen)
	for k, v := range map[string]string{"env": "prod", "team": "ml", "feature": "search"} {
		if m[k] != v {
			t.Errorf("metadata %s = %q, want %q (metadata %v)", k, m[k], v, m)
		}
	}
	_, seen = serveHeaders(t, map[string]string{"x-agentcc-config": `{"property-tier":"gold"}`})
	if got := metadataOf(t, seen)["tier"]; got != "gold" {
		t.Errorf("metadata tier = %q, want gold from x-agentcc-config's property-tier", got)
	}
	// Invalid metadata JSON is ignored; the properties are kept.
	_, seen = serveHeaders(t, map[string]string{"x-agentcc-property-env": "prod", "x-agentcc-metadata": `not json`})
	if got := metadataOf(t, seen)["env"]; got != "prod" {
		t.Errorf("metadata env = %q, want prod", got)
	}
	w, seen := serveHeaders(t, map[string]string{"x-agentcc-property-env": strings.Repeat("v", 1025)})
	if seen.called || w.Code != http.StatusBadRequest {
		t.Errorf("a 1025-character property value: status %d (handler ran %v), want 400", w.Code, seen.called)
	} else if e := errorOf(t, w); e.Param == nil || *e.Param != "x-agentcc-property-env" {
		t.Errorf("refusal param %v, want x-agentcc-property-env", e.Param)
	}
	w, seen = serveHeaders(t, map[string]string{"x-agentcc-property-": "x"})
	if seen.called || w.Code != http.StatusBadRequest {
		t.Errorf("a property header with no name: status %d (handler ran %v), want 400", w.Code, seen.called)
	}
}

func TestSessionNamePathEnterTheMetadataHeader(t *testing.T) {
	_, seen := serveHeaders(t, map[string]string{
		"x-agentcc-session-id":   "s1",
		"x-agentcc-session-name": "My session",
		"x-agentcc-session-path": "/search/rerank",
	})
	m := metadataOf(t, seen)
	if m["session_name"] != "My session" || m["session_path"] != "/search/rerank" {
		t.Errorf("metadata %v, want session_name and session_path", m)
	}
	for _, tc := range []struct{ header, value string }{
		{"x-agentcc-session-name", strings.Repeat("n", 256)},
		{"x-agentcc-session-path", strings.Repeat("p", 1025)},
	} {
		w, seen := serveHeaders(t, map[string]string{tc.header: tc.value})
		if seen.called || w.Code != http.StatusBadRequest {
			t.Errorf("%s of %d characters: status %d (handler ran %v), want 400", tc.header, len(tc.value), w.Code, seen.called)
		}
	}
}

func TestClientRequestIDIsKeptBesideTheGatewayID(t *testing.T) {
	w, seen := serveHeaders(t, map[string]string{"x-agentcc-request-id": "client-req-123"})
	gw := w.Header().Get("x-agentcc-request-id")
	if gw == "client-req-123" {
		t.Fatalf("the gateway's request id was replaced by the client's")
	}
	if _, err := ulid.ParseStrict(gw); err != nil {
		t.Errorf("x-agentcc-request-id %q is not the gateway's ULID: %v", gw, err)
	}
	if seen.reqID != gw {
		t.Errorf("the context's request id %q differs from the response's %q", seen.reqID, gw)
	}
	if got := w.Header().Get("x-agentcc-client-request-id"); got != "client-req-123" {
		t.Errorf("x-agentcc-client-request-id = %q, want the client's id echoed", got)
	}
	if got := metadataOf(t, seen)["client_request_id"]; got != "client-req-123" {
		t.Errorf("metadata client_request_id = %q, want the client's id", got)
	}
	w, seen = serveHeaders(t, map[string]string{"x-agentcc-request-id": strings.Repeat("r", 256)})
	if seen.called || w.Code != http.StatusBadRequest {
		t.Errorf("a 256-character client request id: status %d (handler ran %v), want 400", w.Code, seen.called)
	}
	w, _ = serveHeaders(t, nil)
	if v, ok := w.Header()["X-Agentcc-Client-Request-Id"]; ok {
		t.Errorf("x-agentcc-client-request-id %q on a request that sent none", v)
	}
}

func TestAlwaysPresentHeadersLatencyOnEveryResponse(t *testing.T) {
	cases := []struct {
		name  string
		write func(w http.ResponseWriter)
		want  string // "" = any number
	}{
		{"an error the handler writes", func(w http.ResponseWriter) {
			models.WriteError(w, models.ErrNotFound("model_not_found", "no such model"))
		}, ""},
		{"a body with no WriteHeader", func(w http.ResponseWriter) { _, _ = w.Write([]byte("ok")) }, ""},
		{"a handler's own latency", func(w http.ResponseWriter) {
			w.Header().Set("x-agentcc-latency-ms", "7")
			w.WriteHeader(http.StatusOK)
		}, "7"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := RequestID(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { tc.write(w) }))
			w := httptest.NewRecorder()
			h.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/v1/embeddings", nil))
			got := w.Header().Get("x-agentcc-latency-ms")
			if got == "" {
				t.Fatalf("no x-agentcc-latency-ms; headers %v", w.Header())
			}
			if _, err := strconv.ParseInt(got, 10, 64); err != nil {
				t.Errorf("x-agentcc-latency-ms %q is not a whole number of milliseconds", got)
			}
			if tc.want != "" && got != tc.want {
				t.Errorf("x-agentcc-latency-ms = %q, want the handler's %q", got, tc.want)
			}
			if len(w.Header().Values("x-agentcc-latency-ms")) != 1 {
				t.Errorf("x-agentcc-latency-ms sent %d times", len(w.Header().Values("x-agentcc-latency-ms")))
			}
		})
	}
	// A streaming handler still flushes through the wrapper.
	h := RequestID(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		f, ok := w.(http.Flusher)
		if !ok {
			t.Fatal("the writer RequestID passes on is not an http.Flusher")
		}
		_, _ = w.Write([]byte("data: x\n\n"))
		f.Flush()
		if http.NewResponseController(w).Flush() != nil {
			t.Error("http.ResponseController cannot reach the writer's Flush")
		}
	}))
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil))
	if !w.Flushed || w.Header().Get("x-agentcc-latency-ms") == "" {
		t.Errorf("stream: flushed %v, latency %q", w.Flushed, w.Header().Get("x-agentcc-latency-ms"))
	}
}

// requestIDAllocBudget is the most RequestID allocates per request with no
// agentcc header whose handler set its own latency header: the two ids, their
// response headers, the context value and the request copy. The header reader
// and the latency writer add none. A guard, not a red test.
const requestIDAllocBudget = 13

func TestAlwaysPresentHeadersAddNoAllocation(t *testing.T) {
	h := RequestID(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header()["X-Agentcc-Latency-Ms"] = []string{"1"}
		w.WriteHeader(http.StatusOK)
	}))
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
	req.Header.Set("Authorization", "Bearer sk-x")
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	allocs := testing.AllocsPerRun(200, func() {
		delete(w.Header(), "X-Agentcc-Request-Id")
		delete(w.Header(), "X-Agentcc-Trace-Id")
		h.ServeHTTP(w, req)
	})
	t.Logf("RequestID: %.0f allocs per request (budget %d)", allocs, requestIDAllocBudget)
	if allocs > requestIDAllocBudget {
		t.Errorf("RequestID allocates %.0f times per request with no agentcc header, want at most %d", allocs, requestIDAllocBudget)
	}
}
