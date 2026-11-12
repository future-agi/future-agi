package genaifmt

import (
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// TestWriteAPIErrorSendsRetryAfter: a refusal with a Retry-After keeps it on
// the GenAI dialect, and a provider_busy names its code in the message.
func TestWriteAPIErrorSendsRetryAfter(t *testing.T) {
	w := httptest.NewRecorder()
	WriteAPIError(w, models.ErrProviderBusy("entry"))
	if w.Code != 503 || w.Header().Get("Retry-After") != "1" || !strings.Contains(w.Body.String(), `"UNAVAILABLE"`) ||
		!strings.Contains(w.Body.String(), "provider_busy") {
		t.Fatalf("status %d Retry-After %q body %s", w.Code, w.Header().Get("Retry-After"), w.Body)
	}
	w = httptest.NewRecorder()
	WriteAPIError(w, models.ErrInternal("x"))
	if w.Header().Get("Retry-After") != "" {
		t.Fatal("Retry-After on an error that has none")
	}
}
