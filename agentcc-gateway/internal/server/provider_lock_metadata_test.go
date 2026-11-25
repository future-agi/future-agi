package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestCallerMetadataPreservesProviderLockResults(t *testing.T) {
	for _, source := range []string{"header", "body"} {
		for _, accepted := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/accepted=%t", source, accepted), func(t *testing.T) {
				rc := &models.RequestContext{Metadata: make(map[string]string)}
				if accepted {
					rc.Metadata["provider_locked"] = "true"
					rc.Metadata["provider_lock_target"] = "a"
				}
				r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
				raw := json.RawMessage(`{"provider_locked":"false","provider_lock_target":"b","provider_lock":"a","provider_region":"synthetic-region"}`)
				var body json.RawMessage
				if source == "header" {
					r.Header.Set("x-agentcc-metadata", string(raw))
				} else {
					body = raw
				}
				applyCallerMetadata(rc, r, body)
				for key, want := range map[string]string{"provider_locked": "true", "provider_lock_target": "a"} {
					got, exists := rc.Metadata[key]
					if exists != accepted || (accepted && got != want) {
						t.Errorf("%s=%q present=%t, want present=%t with value %q", key, got, exists, accepted, want)
					}
					if slices.Contains(rc.CustomMetadataKeys, key) {
						t.Errorf("server result %s recorded as a caller dimension", key)
					}
				}
				if rc.Metadata["provider_lock"] != "a" || rc.Metadata["provider_region"] != "synthetic-region" {
					t.Fatalf("caller lock request or ordinary dimension changed: %v", rc.Metadata)
				}
			})
		}
	}
}

func TestCallerMetadataDoesNotSelectOperatorModelFallback(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, source := range []string{"none", "header", "body"} {
			t.Run(fmt.Sprintf("stream=%t/%s", stream, source), func(t *testing.T) {
				var operatorACalls, operatorBCalls, orgCalls atomic.Int64
				operatorA := adaptiveUpstream(t, false, 0, &operatorACalls)
				operatorB := adaptiveUpstream(t, false, 0, &operatorBCalls)
				orgPrimary := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					orgCalls.Add(1)
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(http.StatusServiceUnavailable)
					io.WriteString(w, `{"error":{"message":"synthetic primary unavailable","type":"server_error"}}`)
				}))
				t.Cleanup(orgPrimary.Close)
				cfg := config.DefaultConfig()
				cfg.Providers = map[string]config.ProviderConfig{
					"a": {BaseURL: operatorA.URL, APIFormat: "openai", Models: []string{"primary-model"}},
					"b": {BaseURL: operatorB.URL, APIFormat: "openai", Models: []string{"backup-model"}},
				}
				cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "synthetic-org", Key: "synthetic-org-probe", KeyType: "byok", Metadata: map[string]string{"org_id": "synthetic-org"}}}
				cfg.Routing = config.RoutingConfig{
					DefaultStrategy: "round-robin",
					Targets: map[string][]config.RoutingTargetConfig{
						"primary-model": {{Provider: "a"}},
					},
					ModelFallbacks: map[string][]string{"primary-model": {"backup-model"}},
					ProviderLock:   config.ProviderLockConfig{Enabled: true},
					Failover:       config.FailoverConfig{Enabled: true, MaxAttempts: 1},
				}
				srv, gateway := adaptiveServer(t, cfg)
				store := tenant.NewStore()
				store.Set("synthetic-org", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
					"a": {Enabled: true, APIKey: "synthetic-org-key", Models: []string{"primary-model"}},
				}})
				orgCache := providers.NewOrgProviderCache(map[string]config.ProviderConfig{
					"a": {BaseURL: orgPrimary.URL, APIFormat: "openai", Models: []string{"primary-model"}},
				}, false)
				t.Cleanup(orgCache.EvictAll)
				srv.handlers.tenantStore, srv.handlers.orgProviderCache = store, orgCache
				srv.handlers.keyStore = auth.NewKeyStore(cfg.Auth)

				body := map[string]any{
					"model": "primary-model", "stream": stream,
					"messages": []map[string]string{{"role": "user", "content": "synthetic probe"}},
				}
				metadata := map[string]string{"provider_locked": "true", "provider_lock_target": "b"}
				if source == "body" {
					body["metadata"] = metadata
				}
				encoded, err := json.Marshal(body)
				if err != nil {
					t.Fatal(err)
				}
				req, err := http.NewRequest(http.MethodPost, gateway.URL+"/v1/chat/completions", bytes.NewReader(encoded))
				if err != nil {
					t.Fatal(err)
				}
				req.Header.Set("Content-Type", "application/json")
				req.Header.Set("Authorization", "Bearer synthetic-org-probe")
				if source == "header" {
					encoded, err := json.Marshal(metadata)
					if err != nil {
						t.Fatal(err)
					}
					req.Header.Set("x-agentcc-metadata", string(encoded))
				}
				resp, err := gateway.Client().Do(req)
				if err != nil {
					t.Fatal(err)
				}
				defer resp.Body.Close()
				data, err := io.ReadAll(resp.Body)
				if err != nil {
					t.Fatal(err)
				}
				if resp.StatusCode != http.StatusBadGateway || !strings.Contains(string(data), "synthetic primary unavailable") || orgCalls.Load() != 1 || operatorACalls.Load() != 0 || operatorBCalls.Load() != 0 {
					t.Fatalf("fallback eligibility: status=%d org_calls=%d operator_a=%d operator_b=%d body=%s", resp.StatusCode, orgCalls.Load(), operatorACalls.Load(), operatorBCalls.Load(), data)
				}
			})
		}
	}
}
