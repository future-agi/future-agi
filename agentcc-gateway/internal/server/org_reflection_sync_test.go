package server

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Exercise both exported sync entry points as well as a direct pull. Cancellation
// from the change callback stops periodic sync after its first complete merge.
func syncReflectionConfigs(t *testing.T, mode string, store *tenant.Store, configs map[string]json.RawMessage) {
	t.Helper()
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/agentcc/org-configs/bulk/" || r.Header.Get("Authorization") != "Bearer test-admin" {
			http.Error(w, "unexpected sync request", http.StatusBadRequest)
			return
		}
		json.NewEncoder(w).Encode(map[string]interface{}{"status": true, "result": configs})
	}))
	defer backend.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if mode == "periodic" {
		store.SetOnChange(func(string) { cancel() })
	}
	switch mode {
	case "sync":
		if err := tenant.SyncFromControlPlane(ctx, backend.URL, "test-admin", store); err != nil {
			t.Fatal(err)
		}
	case "startup":
		tenant.RunControlPlaneSync(ctx, true, 0, backend.URL, "test-admin", store, nil)
	case "periodic":
		tenant.RunControlPlaneSync(ctx, false, time.Millisecond, backend.URL, "test-admin", store, nil)
	default:
		t.Fatalf("unknown sync mode %q", mode)
	}
	if ctx.Err() == context.DeadlineExceeded {
		t.Fatal("sync did not complete a merge")
	}
}

func TestControlPlaneSyncStoresReflectionConfigsAsReceived(t *testing.T) {
	for _, mode := range []string{"sync", "startup", "periodic"} {
		t.Run(mode, func(t *testing.T) {
			store := tenant.NewStore()
			previous := &tenant.OrgConfig{ModelMap: map[string]string{"logical": "working"}}
			for _, orgID := range []string{"invalid-existing", "unresolved-existing", "valid-existing", "removed"} {
				store.Set(orgID, previous)
			}
			configs := map[string]json.RawMessage{
				"invalid-existing":    json.RawMessage(`{"guardrails":{"checks":{"keyword-blocklist":{"enabled":true,"action":"block","config":{"reflect_debug":"PRIVATE_CONFIG_SENTINEL"}}}}}`),
				"invalid-new":         json.RawMessage(`{"guardrails":{"checks":{"keyword-blocklist":{"enabled":true,"action":"block","config":{"reflect_max_attempts":true}}}}}`),
				"unresolved-existing": json.RawMessage(`{"guardrails":{"checks":{"llama-guard":{"enabled":true,"action":"block","config":{"stage":"post","on_block":"reflect"}}}}}`),
				"unresolved-new":      json.RawMessage(`{"guardrails":{"checks":{"unknown-check":{"enabled":true,"action":"block","config":{"stage":"post","on_block":"reflect"}}}}}`),
				"valid-existing":      json.RawMessage(`{"model_map":{"logical":"updated"}}`),
				"valid-new":           json.RawMessage(`{"guardrails":{"checks":{"keyword-blocklist":{"enabled":true,"action":"block","config":{"stage":"post","on_block":"reflect"}}}}}`),
			}
			syncReflectionConfigs(t, mode, store, configs)
			body, err := json.Marshal(configs)
			if err != nil {
				t.Fatal(err)
			}
			var expected map[string]*tenant.OrgConfig
			if err := json.Unmarshal(body, &expected); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(store.GetAll(), expected) {
				t.Fatalf("sync did not store configs as received: %+v", store.GetAll())
			}
		})
	}
}

func TestLoadedInvalidReflectionFailsClosed(t *testing.T) {
	for _, mode := range []string{"bulk", "sync", "startup", "periodic"} {
		for _, invalid := range []string{
			`{"guardrails":{"checks":{"keyword-blocklist":{"enabled":true,"action":"block","config":{"stage":"post","on_block":"reflect","reflect_max_attempts":true}}}}}`,
			`{"guardrails":{"checks":{"llama-guard":{"enabled":true,"action":"block","config":{"stage":"post","on_block":"reflect","private":"PRIVATE_CONFIG_SENTINEL"}}}}}`,
		} {
			for _, state := range []string{"existing", "new"} {
				t.Run(mode+"/"+state+"/"+invalid, func(t *testing.T) {
					const orgID = "loaded-tenant"
					const key = "sk-agentcc-loaded-internal-fixture"
					store := tenant.NewStore()
					if state == "existing" {
						store.Set(orgID, &tenant.OrgConfig{})
					}
					plugin := orgReflectionPlugin(store)
					u := &reflectionUpstream{}
					upstream := httptest.NewServer(http.HandlerFunc(u.serve))
					defer upstream.Close()
					cfg := config.DefaultConfig()
					cfg.Admin.Token = "test-admin"
					cfg.Auth.Enabled = true
					cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "internal", Key: key, KeyType: "internal", Metadata: map[string]string{"org_id": orgID}}}
					cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "operator-fixture", APIFormat: "openai", Models: []string{"gpt-4o"}}
					registry, err := providers.NewRegistry(cfg)
					if err != nil {
						t.Fatal(err)
					}
					srv := New(cfg, "", registry, pipeline.NewEngine(plugin), nil, nil, nil, nil, testModelDBPtr(), store, plugin.InvalidateDynamicCache)
					// Bulk and sync must not consult even a rejecting PUT validator.
					srv.SetOrgReflectionConfigValidator(func(*tenant.OrgConfig) error {
						t.Fatal("bulk and sync must not perform guardrail admission")
						return nil
					})
					srv.ready.Store(true)
					configs := map[string]json.RawMessage{orgID: json.RawMessage(invalid)}
					if mode == "bulk" {
						body, err := json.Marshal(configs)
						if err != nil {
							t.Fatal(err)
						}
						r := httptest.NewRequest(http.MethodPost, "/-/orgs/configs/bulk", bytes.NewReader(body))
						r.Header.Set("Authorization", "Bearer test-admin")
						w := httptest.NewRecorder()
						srv.httpServer.Handler.ServeHTTP(w, r)
						var result map[string]interface{}
						if w.Code != http.StatusOK || json.Unmarshal(w.Body.Bytes(), &result) != nil || !reflect.DeepEqual(result, map[string]interface{}{"status": "ok", "loaded": float64(1)}) {
							t.Fatalf("bulk did not load config: status=%d body=%s", w.Code, w.Body.String())
						}
					} else {
						syncReflectionConfigs(t, mode, store, configs)
					}
					var expected tenant.OrgConfig
					if err := json.Unmarshal([]byte(invalid), &expected); err != nil {
						t.Fatal(err)
					}
					if !reflect.DeepEqual(store.Get(orgID), &expected) {
						t.Fatal("loaded config was discarded or replaced")
					}
					for _, stream := range []bool{false, true} {
						body, _ := json.Marshal(map[string]interface{}{"model": "gpt-4o", "messages": []map[string]string{{"role": "user", "content": "ordinary request"}}, "stream": stream})
						r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", bytes.NewReader(body))
						r.Header.Set("Authorization", "Bearer "+key)
						w := httptest.NewRecorder()
						srv.httpServer.Handler.ServeHTTP(w, r)
						if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "invalid_guardrail_config") || strings.Contains(w.Body.String(), "PRIVATE_CONFIG_SENTINEL") || len(u.calls()) != 0 {
							t.Fatalf("tenant ran unguarded: stream=%t status=%d upstream_calls=%d body=%s", stream, w.Code, len(u.calls()), w.Body.String())
						}
					}
				})
			}
		}
	}
}

func TestControlPlaneSyncInvalidatesChangedReflectionConfig(t *testing.T) {
	store := tenant.NewStore()
	store.Set("invalid", &tenant.OrgConfig{})
	var invalidated []string
	store.SetOnChange(func(orgID string) { invalidated = append(invalidated, orgID) })
	configs := map[string]json.RawMessage{
		"invalid": json.RawMessage(`{"guardrails":{"checks":{"llama-guard":{"enabled":true,"action":"block","config":{"stage":"post","on_block":"reflect"}}}}}`),
		"valid":   json.RawMessage(`{"model_map":{"logical":"working"}}`),
	}
	syncReflectionConfigs(t, "sync", store, configs)
	slices.Sort(invalidated)
	if !reflect.DeepEqual(invalidated, []string{"invalid", "valid"}) || store.Get("invalid").Guardrails == nil {
		t.Fatalf("changed configs/caches were not updated: invalidated=%v", invalidated)
	}
	invalidated = nil
	syncReflectionConfigs(t, "sync", store, configs)
	if len(invalidated) != 0 {
		t.Fatalf("unchanged configs invalidated caches: %v", invalidated)
	}
}
