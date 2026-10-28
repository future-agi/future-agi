package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/blocklist"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/external"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/futureagi"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/hallucination"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/webhook"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func orgReflectionPlugin(store *tenant.Store) *guardrails.GuardrailPlugin {
	registry := map[string]guardrails.Guardrail{
		"keyword-blocklist":       blocklist.New(nil),
		"hallucination-detection": hallucination.New(nil),
	}
	factory := func(name string, cfg map[string]interface{}) guardrails.Guardrail {
		if futureagi.IsFutureAGIConfig(cfg) {
			return futureagi.New(name, cfg)
		}
		if webhook.IsWebhookConfig(cfg) {
			return webhook.New(name, cfg)
		}
		if external.IsExternalProviderConfig(cfg) {
			return external.New(name, cfg)
		}
		return nil
	}
	return guardrails.NewPlugin(nil, registry, factory, nil, store)
}

func TestSetOrgConfigRejectsInvalidReflectionWithoutWriting(t *testing.T) {
	for _, tc := range []struct {
		name, check, action, config string
	}{
		{"boolean attempts", "keyword-blocklist", "block", `{"stage":"post","on_block":"reflect","reflect_max_attempts":true}`},
		{"fractional attempts", "keyword-blocklist", "block", `{"stage":"post","on_block":"reflect","reflect_max_attempts":1.5}`},
		{"excess attempts", "keyword-blocklist", "block", `{"stage":"post","on_block":"reflect","reflect_max_attempts":6}`},
		{"invalid on block", "keyword-blocklist", "block", `{"on_block":"PRIVATE_CONFIG_SENTINEL"}`},
		{"invalid debug", "keyword-blocklist", "block", `{"reflect_debug":"PRIVATE_CONFIG_SENTINEL"}`},
		{"invalid stage", "keyword-blocklist", "block", `{"stage":"PRIVATE_CONFIG_SENTINEL"}`},
		{"pre reflection", "keyword-blocklist", "block", `{"stage":"pre","on_block":"reflect"}`},
		{"native pre reflection", "keyword-blocklist", "block", `{"on_block":"reflect"}`},
		{"warn reflection", "keyword-blocklist", "warn", `{"stage":"post","on_block":"reflect"}`},
		{"request only check", "prompt-injection", "block", `{"stage":"post","on_block":"reflect"}`},
		{"response only check", "hallucination-detection", "block", `{"stage":"pre"}`},
		{"unresolved external check", "llama-guard", "block", `{"stage":"post","on_block":"reflect"}`},
		{"unresolved custom check", "unknown-check", "block", `{"stage":"post","on_block":"reflect"}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			for _, state := range []string{"existing", "new", "fallback-existing", "fallback-new"} {
				t.Run(state, func(t *testing.T) {
					fallback := strings.HasPrefix(state, "fallback-")
					if fallback && tc.name == "response only check" {
						// Without an implementation the fallback can only validate
						// retry controls and reject unresolved reflecting checks.
						return
					}
					store := tenant.NewStore()
					var previous *tenant.OrgConfig
					if strings.HasSuffix(state, "existing") {
						previous = &tenant.OrgConfig{ModelMap: map[string]string{"logical": "working"}}
						store.Set("org-a", previous)
					}
					cache := providers.NewOrgProviderCache(map[string]config.ProviderConfig{"openai": {APIFormat: "openai", BaseURL: "http://127.0.0.1:1"}}, true)
					cached, err := cache.GetOrCreate("org-a", "openai", "test-key")
					if err != nil {
						t.Fatal(err)
					}
					h := NewOrgConfigHandlers(store, "test-admin", cache)
					if !fallback {
						h.validateReflectionConfig = orgReflectionPlugin(store).ValidateOrgReflectionConfig
					}
					invalidations := 0
					h.SetOnConfigChange(func(string) { invalidations++ })
					body := `{"guardrails":{"checks":{"` + tc.check + `":{"enabled":true,"action":"` + tc.action + `","config":` + tc.config + `}}}}`
					r := httptest.NewRequest(http.MethodPut, "/-/orgs/org-a/config?org_id=org-a", strings.NewReader(body))
					r.Header.Set("Authorization", "Bearer test-admin")
					w := httptest.NewRecorder()
					h.SetOrgConfig(w, r)
					if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "invalid_guardrail_config") || strings.Contains(w.Body.String(), "PRIVATE_CONFIG_SENTINEL") {
						t.Fatalf("status=%d body=%s", w.Code, w.Body.String())
					}
					if store.Get("org-a") != previous || invalidations != 0 || cache.Get("org-a", "openai") != cached {
						t.Fatalf("invalid config changed stored state or caches: config=%+v invalidations=%d", store.Get("org-a"), invalidations)
					}
				})
			}
		})
	}
}

func TestSetOrgConfigAcceptsValidReflectionAndDisabledChecks(t *testing.T) {
	for _, checks := range []string{
		`{"keyword-blocklist":{"enabled":true,"action":"block","config":{"stage":"post","on_block":"reflect","reflect_max_attempts":5,"reflect_debug":true,"words":["unsafe"]}}}`,
		`{"hallucination-detection":{"enabled":true,"action":"block","config":{"on_block":"reflect"}}}`,
		`{"keyword-blocklist":{"enabled":true,"action":"block","config":{"words":["unsafe"]}}}`,
		`{"keyword-blocklist":{"enabled":false,"config":{"reflect_max_attempts":true}},"unused":null}`,
		`{"futureagi-eval":{"enabled":true,"action":"block","config":{"provider":"futureagi","stage":"post","on_block":"reflect","mode":"provider-specific"}}}`,
		`{"llama-guard":{"enabled":true,"action":"block","config":{"provider":"llama_guard","stage":"post","on_block":"reflect"}}}`,
		`{"custom-webhook":{"enabled":true,"action":"block","config":{"provider":"webhook","url":"https://example.invalid/check","stage":"post","on_block":"reflect"}}}`,
		`{"llama-guard":{"enabled":false,"action":"block","config":{"stage":"post","on_block":"reflect"}}}`,
		`{"llama-guard":{"enabled":true,"action":"block","config":{"stage":"post","on_block":"reject"}}}`,
	} {
		t.Run(checks, func(t *testing.T) {
			store := tenant.NewStore()
			h := NewOrgConfigHandlers(store, "test-admin", nil)
			h.validateReflectionConfig = orgReflectionPlugin(store).ValidateOrgReflectionConfig
			invalidations := 0
			h.SetOnConfigChange(func(string) { invalidations++ })
			r := httptest.NewRequest(http.MethodPut, "/-/orgs/org-a/config?org_id=org-a", strings.NewReader(`{"guardrails":{"checks":`+checks+`}}`))
			r.Header.Set("Authorization", "Bearer test-admin")
			w := httptest.NewRecorder()
			h.SetOrgConfig(w, r)
			if w.Code != http.StatusOK || store.Get("org-a") == nil || invalidations != 1 {
				t.Fatalf("valid config rejected: status=%d invalidations=%d body=%s", w.Code, invalidations, w.Body.String())
			}
		})
	}
}

func TestBulkLoadOrgConfigsStoresReflectionConfigsAsReceived(t *testing.T) {
	store := tenant.NewStore()
	previous := &tenant.OrgConfig{ModelMap: map[string]string{"logical": "working"}}
	store.Set("invalid-existing", previous)
	store.Set("unresolved-existing", previous)
	store.Set("valid-existing", previous)
	store.Set("removed", previous)
	cache := providers.NewOrgProviderCache(map[string]config.ProviderConfig{"openai": {APIFormat: "openai", BaseURL: "http://127.0.0.1:1"}}, true)
	if _, err := cache.GetOrCreate("invalid-existing", "openai", "test-key"); err != nil {
		t.Fatal(err)
	}
	h := NewOrgConfigHandlers(store, "test-admin", cache)
	h.validateReflectionConfig = func(*tenant.OrgConfig) error {
		t.Fatal("bulk load must not perform guardrail admission")
		return nil
	}
	var invalidations []string
	h.SetOnConfigChange(func(orgID string) { invalidations = append(invalidations, orgID) })
	body := `{
		"invalid-existing":{"guardrails":{"checks":{"keyword-blocklist":{"enabled":true,"action":"block","config":{"stage":"post","on_block":"reflect","reflect_max_attempts":true}}}}},
		"invalid-new":{"guardrails":{"checks":{"keyword-blocklist":{"enabled":true,"action":"block","config":{"reflect_debug":"PRIVATE_CONFIG_SENTINEL"}}}}},
		"unresolved-existing":{"guardrails":{"checks":{"llama-guard":{"enabled":true,"action":"block","config":{"stage":"post","on_block":"reflect"}}}}},
		"unresolved-new":{"guardrails":{"checks":{"unknown-check":{"enabled":true,"action":"block","config":{"stage":"post","on_block":"reflect"}}}}},
		"valid-existing":{"model_map":{"logical":"updated"}},
		"valid-new":{"guardrails":{"checks":{"keyword-blocklist":{"enabled":true,"action":"block","config":{"stage":"post","on_block":"reflect","reflect_debug":false}}}}}
	}`
	r := httptest.NewRequest(http.MethodPost, "/-/orgs/configs/bulk", strings.NewReader(body))
	r.Header.Set("Authorization", "Bearer test-admin")
	w := httptest.NewRecorder()
	h.BulkLoadOrgConfigs(w, r)
	var response map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	if w.Code != http.StatusOK || !reflect.DeepEqual(response, map[string]interface{}{"status": "ok", "loaded": float64(6)}) {
		t.Fatalf("incompatible bulk response: status=%d body=%s", w.Code, w.Body.String())
	}
	var expected map[string]*tenant.OrgConfig
	if err := json.Unmarshal([]byte(body), &expected); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(store.GetAll(), expected) {
		t.Fatalf("bulk did not store configs as received: %+v", store.GetAll())
	}
	slices.Sort(invalidations)
	if !reflect.DeepEqual(invalidations, []string{"invalid-existing", "invalid-new", "unresolved-existing", "unresolved-new", "valid-existing", "valid-new"}) || cache.Get("invalid-existing", "openai") != nil {
		t.Fatalf("bulk did not invalidate caches: invalidations=%v", invalidations)
	}
}
