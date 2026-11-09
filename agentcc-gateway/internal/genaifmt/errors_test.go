package genaifmt

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// An error leaving in Google's format carries no credential from a URL in its message.
func TestWriteError_RedactsCredentialsInURLs(t *testing.T) {
	w := httptest.NewRecorder()
	WriteError(w, http.StatusBadGateway, "UNAVAILABLE",
		`Post "https://user:pw-0001@upstream.example/v1beta/models/m:generateContent?key=fixture-value-0001&alt=sse": dial tcp: refused`)
	body := w.Body.String()
	if strings.Contains(body, "fixture-value-0001") || strings.Contains(body, "pw-0001") {
		t.Fatalf("body carries a credential: %s", body)
	}
	if !strings.Contains(body, "REDACTED") || !strings.Contains(body, "alt=sse") || !strings.Contains(body, "dial tcp: refused") {
		t.Errorf("body %s, want the credentials REDACTED and the rest of the message kept", body)
	}
}
