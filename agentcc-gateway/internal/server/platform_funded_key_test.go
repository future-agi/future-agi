package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"slices"
	"testing"
)

// GET /-/health names the key billing values this build honours, so the
// platform mints a platform-funded key only for a gateway that applies it.
func TestHealthNamesTheKeyBillingThisBuildHonours(t *testing.T) {
	srv, _, _ := newOrgBindingServer(t)
	w := bindingCall(t, srv, http.MethodGet, "/-/health", "")
	if w.Code != http.StatusOK {
		t.Fatalf("GET /-/health: %d %s", w.Code, w.Body.String())
	}
	var body struct {
		PlatformKeyBilling []string `json:"platform_key_billing"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(body.PlatformKeyBilling, []string{"platform_funded"}) {
		t.Fatalf("platform_key_billing = %v, want [platform_funded]", body.PlatformKeyBilling)
	}

	req := httptest.NewRequest(http.MethodGet, "/-/health", nil)
	rec := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(rec, req)
	if rec.Code == http.StatusOK {
		t.Fatalf("GET /-/health without the admin token answered 200: %s", rec.Body.String())
	}
}

// Request metadata never sets the request's key billing: every key_ name is
// dropped from it.
func TestCallerMetadataCannotSetKeyBilling(t *testing.T) {
	if !isBlockedMetadataKey("key_billing") {
		t.Fatal("a caller's x-agentcc-metadata can set key_billing")
	}
}
