package server

import (
	"net/http"
	"strings"
	"testing"
)

// TestCustomerCapabilitiesOmitResaleTerms: managed_resale_terms is internal
// legal status. The admin
// route shows it on every cell; /v1/capabilities, for any key, omits it. A
// ?model= response from either route validates against capabilities-model-v1.
func TestCustomerCapabilitiesOmitResaleTerms(t *testing.T) {
	srv := newCapabilitiesTestGateway(t, orgWithBYOK(true))

	admin := capGet(t, srv, "/-/capabilities", capAdminToken)
	if admin.Code != http.StatusOK || !strings.Contains(admin.Body.String(), `"managed_resale_terms":"unconfirmed"`) {
		t.Fatalf("admin view: %d, want managed_resale_terms on its cells; body %.300s", admin.Code, admin.Body)
	}
	for name, key := range map[string]string{"org key": capOrgKey, "internal key": capInternalKey, "acl'd org key": capACLKey} {
		w := capGet(t, srv, "/v1/capabilities", key)
		if w.Code != http.StatusOK {
			t.Fatalf("%s: %d %s", name, w.Code, w.Body)
		}
		if strings.Contains(w.Body.String(), "managed_resale_terms") {
			t.Errorf("%s: /v1/capabilities shows managed_resale_terms", name)
		}
		if errs := validateCapabilities(t, capDecode(t, w)); len(errs) > 0 {
			t.Errorf("%s fails capabilities-v1.schema.json: %v", name, errs)
		}
	}
	for path, bearer := range map[string]string{
		"/v1/capabilities?model=anthropic/claude-sonnet-5.5": capOrgKey,
		"/-/capabilities?model=gpt-4o":                       capAdminToken,
	} {
		w := capGet(t, srv, path, bearer)
		if w.Code != http.StatusOK {
			t.Fatalf("%s: %d %s", path, w.Code, w.Body)
		}
		if errs := validateCapabilitiesModel(t, capDecode(t, w)); len(errs) > 0 {
			t.Errorf("%s fails capabilities-model-v1.schema.json: %v", path, errs)
		}
	}
}
