package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Pushed per-org managed upstream keys are redacted from every
// admin GET; key_hash stays so the control plane can match versions.
func TestAdminOrgConfigGetRedactsManagedUpstreamKeys(t *testing.T) {
	const secret = "sk-or-v1-0123456789abcdef0123456789abcdef"
	store := tenant.NewStore()
	h := NewOrgConfigHandlers(store, "admin-test-token", nil)

	body := `{"managed_models":{"enabled":true,"upstream_keys":{"openrouter":{"api_key":"` + secret + `","key_hash":"kh_1"}}}}`
	put := httptest.NewRequest("PUT", "/-/orgs/org-k/config?org_id=org-k", strings.NewReader(body))
	put.Header.Set("Authorization", "Bearer admin-test-token")
	pw := httptest.NewRecorder()
	h.SetOrgConfig(pw, put)
	if pw.Code != http.StatusOK {
		t.Fatalf("PUT status = %d, body %s", pw.Code, pw.Body.String())
	}

	for _, tc := range []struct {
		name string
		call func(w http.ResponseWriter, r *http.Request)
		url  string
	}{
		{"get", h.GetOrgConfig, "/-/orgs/org-k/config?org_id=org-k"},
		{"list", h.ListOrgConfigs, "/-/orgs/configs"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest("GET", tc.url, nil)
			req.Header.Set("Authorization", "Bearer admin-test-token")
			w := httptest.NewRecorder()
			tc.call(w, req)
			if w.Code != http.StatusOK {
				t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
			}
			got := w.Body.String()
			if strings.Contains(got, secret) {
				t.Fatalf("upstream key returned in clear: %s", got)
			}
			if !strings.Contains(got, `"key_hash":"kh_1"`) || !strings.Contains(got, `"api_key":"sk-or-v1****cdef"`) {
				t.Fatalf("expected a masked key and the key_hash, got %s", got)
			}
		})
	}

	live := store.Get("org-k").ManagedModels.UpstreamKeys["openrouter"].APIKey
	if live != secret {
		t.Fatalf("redaction mutated the live upstream key: %q", live)
	}
}

func TestRedactOrgConfigMasksRolloutUpstreamKeys(t *testing.T) {
	const secret = "sk-or-v1-rollout-0123456789abcdef"
	input := &tenant.OrgConfig{Routing: &tenant.RoutingConfig{Rollout: &tenant.RolloutConfig{
		ManagedModels: &tenant.ManagedModelsConfig{UpstreamKeys: map[string]*tenant.ManagedUpstreamKey{
			"openrouter": {APIKey: secret, KeyHash: "kh_2"},
		}},
	}}}
	redacted := redactOrgConfig(input)
	k := redacted.Routing.Rollout.ManagedModels.UpstreamKeys["openrouter"]
	if k.APIKey == secret || k.KeyHash != "kh_2" {
		t.Fatalf("rollout upstream key not masked: %#v", k)
	}
	if input.Routing.Rollout.ManagedModels.UpstreamKeys["openrouter"].APIKey != secret {
		t.Fatal("redaction mutated the live rollout config")
	}
}
