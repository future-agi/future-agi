package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The release boundary is inside middleware but outside the handler. Retain
// middleware identity/CORS/security metadata, not identically named values a
// handler (or upstream) subsequently overwrites.
func TestInspectionR3RefusalPreservesMiddlewareHeadersHTTP(t *testing.T) {
	for _, safeError := range []bool{false, true} {
		name := "missing-receipt"
		if safeError {
			name = "gateway-error"
		}
		t.Run(name, func(t *testing.T) {
			requestIDs := make(chan string, 1)
			handler := middleware.RequestID(http.HandlerFunc(func(dst http.ResponseWriter, r *http.Request) {
				requestIDs <- dst.Header().Get("x-agentcc-request-id")
				dst.Header().Set("Access-Control-Allow-Origin", "https://client.example")
				dst.Header().Add("Vary", "Origin")
				dst.Header().Set("X-Content-Type-Options", "nosniff")
				state := models.NewInspectionState(textInspection("test.headers"))
				state.BindPrincipal("org", "key")
				state.Require(models.InspectionOutput)
				guard, w := newInspectionWriter(dst, state)
				for _, header := range []string{"x-agentcc-request-id", "x-agentcc-trace-id", "Access-Control-Allow-Origin", "Vary", "X-Content-Type-Options", "Location", "X-Upstream-Content", "Content-Encoding"} {
					w.Header().Set(header, "uninspected-content")
				}
				w.Header().Set("Content-Length", "999")
				if safeError {
					models.WriteError(w, &models.APIError{Status: 403, Code: "content_blocked", Message: "uninspected-content"})
				} else {
					_, _ = w.Write([]byte("uninspected-content"))
				}
				guard.finish()
			}))
			gateway := httptest.NewServer(handler)
			defer gateway.Close()
			resp, err := http.Get(gateway.URL)
			if err != nil {
				t.Fatal(err)
			}
			defer resp.Body.Close()
			var body models.ErrorResponse
			if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
				t.Fatal(err)
			}
			requestID := <-requestIDs
			if resp.StatusCode != 403 || requestID == "" || resp.Header.Get("x-agentcc-request-id") != requestID || resp.Header.Get("x-agentcc-trace-id") == "" {
				t.Fatalf("lost correlation: status=%d headers=%v", resp.StatusCode, resp.Header)
			}
			if resp.Header.Get("Access-Control-Allow-Origin") != "https://client.example" || resp.Header.Get("Vary") != "Origin" || resp.Header.Get("X-Content-Type-Options") != "nosniff" {
				t.Fatalf("lost middleware headers: %v", resp.Header)
			}
			for key, values := range resp.Header {
				if strings.Contains(strings.Join(values, ","), "uninspected-content") {
					t.Fatalf("released uninspected header %s", key)
				}
			}
			wantCode := "output_guardrail_unsupported"
			if safeError {
				wantCode = "content_blocked"
			}
			if body.Error.Code != wantCode || strings.Contains(body.Error.Message, "uninspected-content") || resp.Header.Get("Content-Length") == "999" {
				t.Fatalf("invalid refusal: body=%+v headers=%v", body, resp.Header)
			}
		})
	}
}

func TestInspectionR3LegacyStreamErrorSanitized(t *testing.T) {
	for _, tc := range []struct{ code, want string }{
		{"streaming_not_supported", "streaming_not_supported"},
		{"raw-upstream-code", "request_failed"},
	} {
		t.Run(tc.code, func(t *testing.T) {
			state := models.NewInspectionState(textInspection("test.legacy-completion"))
			state.BindPrincipal("org", "key")
			state.Require(models.InspectionInput)
			dst := httptest.NewRecorder()
			guard, w := newInspectionWriter(dst, state)
			secret := "uninspected-upstream-content"
			w.Header().Set("Location", secret)
			models.WriteError(w, &models.APIError{Status: 501, Code: tc.code, Type: secret, Message: secret, Param: &secret, RetryAfter: secret})
			_, _ = w.Write([]byte(secret))
			guard.finish()
			var body models.ErrorResponse
			if err := json.Unmarshal(dst.Body.Bytes(), &body); err != nil {
				t.Fatal(err)
			}
			if dst.Code != 501 || body.Error.Code != tc.want || body.Error.Type != models.ErrTypeServer || body.Error.Param != nil {
				t.Fatalf("status=%d body=%s", dst.Code, dst.Body.String())
			}
			if strings.Contains(dst.Body.String(), secret) || dst.Header().Get("Location") != "" || dst.Header().Get("Retry-After") != "" {
				t.Fatalf("unsanitized body/headers: %s %v", dst.Body.String(), dst.Header())
			}
		})
	}
}
