package models

import (
	"net/http"
	"net/http/httptest"
	"runtime"
	"strings"
	"testing"
	"time"
)

const liveKey = "AIzaSyD-ThisIsTheShapeOfARealGoogleKey_123456"

// The reported leak: a network failure on a Gemini call surfaced as a 502
// whose message carried the whole URL, API key included, into the caller's
// response body and into the request log.
func TestRedactCredentials_TheReportedGeminiError(t *testing.T) {
	message := `gemini: request failed: Post https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent?key=` +
		liveKey + `: http2: server sent GOAWAY and closed the connection; LastStreamID=77, ErrCode=NO_ERROR, debug="server_shutting_down"`

	got := RedactCredentials(message)

	if strings.Contains(got, liveKey) {
		t.Fatalf("the key survived redaction:\n%s", got)
	}
	// The rest of the message is what the message is for.
	for _, keep := range []string{"generativelanguage.googleapis.com", "GOAWAY", "server_shutting_down", "gemini-3-flash-preview"} {
		if !strings.Contains(got, keep) {
			t.Errorf("redaction removed %q, which is diagnostic, not secret:\n%s", keep, got)
		}
	}
}

func TestRedactCredentials(t *testing.T) {
	tests := []struct {
		name     string
		in       string
		mustDrop string
		mustKeep string
	}{
		{"key in a query", `Post https://host/v1?key=` + liveKey + `: boom`, liveKey, "host/v1"},
		{"api_key in a query", `GET https://host/v1?api_key=` + liveKey, liveKey, "host/v1"},
		{"access_token", `GET https://host/v1?access_token=` + liveKey, liveKey, "host/v1"},
		{"a password in the host", `Post https://user:` + liveKey + `@host/v1: boom`, liveKey, "user"},
		{"a quoted URL", `Post "https://host/v1?key=` + liveKey + `": boom`, liveKey, "host/v1"},
		{"a key beside a harmless param", `Post https://host/v1?alt=sse&key=` + liveKey, liveKey, "alt=sse"},
		{"two URLs in one message", `tried https://a/v1?key=` + liveKey + ` then https://b/v1?key=` + liveKey, liveKey, "https://b/v1"},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := RedactCredentials(tc.in)
			if strings.Contains(got, tc.mustDrop) {
				t.Errorf("the credential survived:\n%s", got)
			}
			if !strings.Contains(got, tc.mustKeep) {
				t.Errorf("%q was lost:\n%s", tc.mustKeep, got)
			}
		})
	}
}

// A message with nothing secret in it must come back untouched, or every
// upstream error becomes harder to read.
func TestRedactCredentials_LeavesOrdinaryMessagesAlone(t *testing.T) {
	for _, in := range []string{
		"provider error (HTTP 429): rate limit reached, retry in 30s",
		"gemini: request timed out",
		`Post https://generativelanguage.googleapis.com/v1beta/models/x:generateContent: connection refused`,
		"model gpt-4o is not configured for this provider",
	} {
		if got := RedactCredentials(in); got != in {
			t.Errorf("message was rewritten:\n  in:  %s\n  out: %s", in, got)
		}
	}
}

// Every error constructor redacts, so no call site has to remember to.
func TestErrorConstructors_Redact(t *testing.T) {
	withKey := "failed: Post https://host/v1?key=" + liveKey + ": boom"

	errs := map[string]*APIError{
		"upstream": ErrUpstreamProvider(0, withKey),
		"internal": ErrInternal(withKey),
		"timeout":  ErrGatewayTimeout(withKey),
		"bad":      ErrBadRequest("code", withKey),
	}
	for name, apiErr := range errs {
		if strings.Contains(apiErr.Message, liveKey) {
			t.Errorf("%s: the key reached the error message: %s", name, apiErr.Message)
		}
	}
}

// And the body actually written to the caller carries no credential.
func TestWriteError_Redacts(t *testing.T) {
	w := httptest.NewRecorder()
	WriteError(w, ErrUpstreamProvider(0, "failed: Post https://host/v1?key="+liveKey+": boom"))

	if w.Code != http.StatusBadGateway {
		t.Errorf("status = %d, want %d", w.Code, http.StatusBadGateway)
	}
	if strings.Contains(w.Body.String(), liveKey) {
		t.Errorf("the key reached the response body: %s", w.Body.String())
	}
}

// The text handed to the redactor is a caller's. An unknown model name of
// "https://host/" followed by a hundred thousand brackets reaches here through
// an unknown-model error, and trimming that suffix a byte at a time allocated
// gigabytes for one request.
func TestRedactCredentials_HostileTrailingPunctuation(t *testing.T) {
	hostile := "model https://host/v1?key=" + liveKey + strings.Repeat(")", 131072) + " not found"

	var before, after runtime.MemStats
	runtime.GC()
	runtime.ReadMemStats(&before)

	start := time.Now()
	got := RedactCredentials(hostile)
	elapsed := time.Since(start)

	runtime.ReadMemStats(&after)
	allocated := after.TotalAlloc - before.TotalAlloc

	if strings.Contains(got, liveKey) {
		t.Error("the credential survived redaction")
	}
	// The quadratic version allocated ~9 GB and took ~0.8s for this input.
	// A linear one needs a small multiple of the input, which is 128 KiB.
	if limit := uint64(32 << 20); allocated > limit {
		t.Errorf("allocated %d bytes for a %d byte input, want under %d — the trim is not linear",
			allocated, len(hostile), limit)
	}
	if elapsed > 2*time.Second {
		t.Errorf("took %s, want well under 2s", elapsed)
	}
}

// 78 sites build an APIError directly rather than through the constructors —
// a provider parsing an upstream error body among them. Those never reach
// newAPIError, so redaction also happens where the response is written.
func TestWriteError_RedactsADirectlyConstructedError(t *testing.T) {
	// The shape parseGeminiError produces: the upstream's own message, which
	// can echo back a URL carrying the key.
	upstreamEcho := &APIError{
		Status:  http.StatusBadRequest,
		Type:    ErrTypeInvalidRequest,
		Code:    "provider_400",
		Message: "gemini error (HTTP 400): API key not valid: https://provider.invalid/v1?key=" + liveKey,
	}

	w := httptest.NewRecorder()
	WriteError(w, upstreamEcho)

	if w.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want %d", w.Code, http.StatusBadRequest)
	}
	if strings.Contains(w.Body.String(), liveKey) {
		t.Errorf("the credential reached the response body:\n%s", w.Body.String())
	}
	// The diagnosis still has to survive.
	if !strings.Contains(w.Body.String(), "API key not valid") {
		t.Errorf("the upstream's message was lost:\n%s", w.Body.String())
	}
}
