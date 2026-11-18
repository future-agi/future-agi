package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestAdminOrgConfigurationErrorRoundTrip(t *testing.T) {
	const config = `{"guardrails":{"key_policies":[{"id":"selected","key_ids":["key-a"],"checks":{
		"secret-detection":{"enabled":true,"action":"block","configuration_error":"invalid_config"},
		"keyword-blocklist":{"enabled":true,"action":"warn","config":{"keywords":["synthetic"]}}
	}}]}}`
	for _, bulk := range []bool{false, true} {
		name := "single"
		if bulk {
			name = "bulk"
		}
		t.Run(name, func(t *testing.T) {
			store := tenant.NewStore()
			h := NewOrgConfigHandlers(store, "synthetic-admin", nil)
			changed := 0
			h.SetOnConfigChange(func(orgID string) {
				if orgID != "org" {
					t.Errorf("unexpected org invalidation: %q", orgID)
				}
				changed++
			})
			payload, method, path := config, http.MethodPut, "/-/orgs/org/config?org_id=org"
			if bulk {
				payload, method, path = `{"org":`+config+`}`, http.MethodPost, "/-/orgs/configs/bulk"
			}
			r := httptest.NewRequest(method, path, strings.NewReader(payload))
			r.Header.Set("Authorization", "Bearer synthetic-admin")
			w := httptest.NewRecorder()
			if bulk {
				h.BulkLoadOrgConfigs(w, r)
			} else {
				h.SetOrgConfig(w, r)
			}
			if w.Code != http.StatusOK {
				t.Fatalf("admin decode status=%d body=%s", w.Code, w.Body.String())
			}
			cfg := store.Get("org")
			if cfg == nil || cfg.Guardrails == nil || len(cfg.Guardrails.KeyPolicies) != 1 {
				t.Fatalf("selected policy lost: %+v", cfg)
			}
			checks := cfg.Guardrails.KeyPolicies[0].Checks
			if len(checks) != 2 || checks["secret-detection"] == nil || !checks["secret-detection"].Enabled || checks["secret-detection"].Action != "block" {
				t.Fatalf("invalid check identity or siblings lost: %+v", checks)
			}
			encoded, err := json.Marshal(checks)
			if err != nil {
				t.Fatal(err)
			}
			if !strings.Contains(string(encoded), `"configuration_error":"invalid_config"`) || !strings.Contains(string(encoded), `"keywords":["synthetic"]`) {
				t.Fatalf("tenant bridge lost invalid marker or sibling config: %s", encoded)
			}
			if changed != 1 {
				t.Fatalf("config cache invalidations=%d want=1", changed)
			}
			get := httptest.NewRequest(http.MethodGet, "/-/orgs/org/config?org_id=org", nil)
			get.Header.Set("Authorization", "Bearer synthetic-admin")
			response := httptest.NewRecorder()
			h.GetOrgConfig(response, get)
			if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), `"configuration_error":"invalid_config"`) {
				t.Fatalf("admin readback lost invalid marker: status=%d body=%s", response.Code, response.Body.String())
			}
		})
	}
}
