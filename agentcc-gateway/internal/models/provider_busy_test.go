package models

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// PROVIDER-CONCURRENCY-1: the provider_busy refusal's wire shape.
func TestProviderBusyWire(t *testing.T) {
	w := httptest.NewRecorder()
	WriteError(w, ErrProviderBusy("openrouter"))
	var body ErrorResponse
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if w.Code != http.StatusServiceUnavailable || w.Header().Get("Retry-After") != "1" ||
		body.Error.Code != "provider_busy" || body.Error.Type != "server_error" {
		t.Fatalf("status %d Retry-After %q body %s", w.Code, w.Header().Get("Retry-After"), w.Body)
	}
	if want := `provider_busy: provider entry "openrouter" has no free upstream slot; the request was not sent upstream, retry after the Retry-After interval`; body.Error.Message != want {
		t.Fatalf("message %q", body.Error.Message)
	}
	if !IsProviderBusy(ErrProviderBusy("x")) {
		t.Fatal("IsProviderBusy does not know its own refusal")
	}
}
