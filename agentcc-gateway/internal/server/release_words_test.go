package server

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// What this release does not serve says so in words that promise no later
// date: "in this release", never "yet". The codes and statuses are unchanged.

func TestAnEndpointNotBuiltSaysItIsNotAvailableInThisRelease(t *testing.T) {
	w := httptest.NewRecorder()
	NotImplemented(w, httptest.NewRequest(http.MethodGet, "/v1/fixture", nil))
	if w.Code != http.StatusNotImplemented {
		t.Fatalf("status %d, want 501", w.Code)
	}
	e := errorObject(t, w)
	if e["code"] != "not_implemented" {
		t.Fatalf("code %v, want not_implemented", e["code"])
	}
	if want := "This endpoint is not available in this release"; e["message"] != want {
		t.Fatalf("message %q, want %q", e["message"], want)
	}
}

func TestADeferredRunSaysOrganizationKeysCannotSendItInThisRelease(t *testing.T) {
	e := errDeferredRunUnavailable("A scheduled run")
	if e.Status != http.StatusBadRequest || e.Code != "deferred_run_unavailable" {
		t.Fatalf("status %d code %q, want 400 deferred_run_unavailable", e.Status, e.Code)
	}
	want := "A scheduled run is not available for organization API keys in this release; send the request synchronously"
	if e.Message != want {
		t.Fatalf("message %q, want %q", e.Message, want)
	}
}
