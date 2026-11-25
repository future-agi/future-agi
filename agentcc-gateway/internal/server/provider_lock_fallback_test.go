package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type lockedFallbackWitness struct {
	mu    sync.Mutex
	calls []string
}

func (w *lockedFallbackWitness) snapshot() []string {
	w.mu.Lock()
	defer w.mu.Unlock()
	return append([]string(nil), w.calls...)
}

// All witnesses are real HTTP adapter calls, including the credential identity.
func lockedFallbackUpstream(t *testing.T, id string, witness *lockedFallbackWitness) *httptest.Server {
	t.Helper()
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" || r.Method != http.MethodPost {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		var req struct {
			Model  string `json:"model"`
			Stream bool   `json:"stream"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Error(err)
			w.WriteHeader(400)
			return
		}
		witness.mu.Lock()
		witness.calls = append(witness.calls, fmt.Sprintf("%s:%s:%s", id, req.Model, r.Header.Get("Authorization")))
		witness.mu.Unlock()
		// Neither upstream headers nor caller metadata may rewrite the accepted lock.
		w.Header().Set("x-agentcc-provider-locked", "b")
		w.Header().Set("x-agentcc-provider", "b")
		w.Header().Set("Content-Type", "application/json")
		if req.Model == "primary-model" {
			w.WriteHeader(http.StatusServiceUnavailable)
			io.WriteString(w, `{"error":{"message":"synthetic primary unavailable","type":"server_error"}}`)
			return
		}
		if req.Stream {
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprintf(w, "data: {\"id\":\"synthetic\",\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{\"content\":\"synthetic locked backup\"}}]}\n\ndata: [DONE]\n\n", req.Model)
			return
		}
		fmt.Fprintf(w, `{"id":"synthetic","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"synthetic locked backup"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`, req.Model)
	}))
	t.Cleanup(s.Close)
	return s
}

func lockedFallbackConfig(a, b string, breaker bool) *config.Config {
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{
		"a": {BaseURL: a, APIFormat: "openai", APIKey: "synthetic-operator-a", Models: []string{"primary-model"}},
		"b": {BaseURL: b, APIFormat: "openai", APIKey: "synthetic-operator-b", Models: []string{"backup-model"}},
	}
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "synthetic-org", Key: "synthetic-org-probe", KeyType: "byok", Metadata: map[string]string{"org_id": "synthetic-org"}}}
	cfg.Routing = config.RoutingConfig{
		DefaultStrategy: "least-latency",
		Targets:         map[string][]config.RoutingTargetConfig{"primary-model": {{Provider: "a"}}},
		ModelFallbacks:  map[string][]string{"primary-model": {"backup-model"}},
		ProviderLock:    config.ProviderLockConfig{Enabled: true, AllowedProviders: []string{"a"}},
		Failover:        config.FailoverConfig{Enabled: true, MaxAttempts: 1},
		CircuitBreaker:  config.CircuitBreakerConfig{Enabled: breaker, FailureThreshold: 3, SuccessThreshold: 1, HalfOpenMaxProbes: 1, GateDirectCalls: true, Cooldown: time.Hour},
	}
	return cfg
}

// Unlike the adaptive fixture, this exercises authentication on the public route.
func lockedFallbackServer(t *testing.T, cfg *config.Config, orgCfg *tenant.OrgConfig, orgBases map[string]config.ProviderConfig) (*Server, *httptest.Server) {
	t.Helper()
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	store := tenant.NewStore()
	store.Set("synthetic-org", orgCfg)
	keys := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true)), keys, nil, nil, nil, testModelDBPtr(), store, nil)
	if orgBases != nil {
		cache := providers.NewOrgProviderCache(orgBases, false)
		t.Cleanup(cache.EvictAll)
		srv.handlers.orgProviderCache = cache
	}
	srv.ready.Store(true)
	t.Cleanup(func() { srv.Shutdown(context.Background()) })
	gateway := httptest.NewServer(srv.httpServer.Handler)
	t.Cleanup(gateway.Close)
	return srv, gateway
}

func lockedFallbackRequest(t *testing.T, gateway *httptest.Server, stream bool, lock, spoof string, keys ...string) (int, string, http.Header) {
	t.Helper()
	body := map[string]any{"model": "primary-model", "stream": stream, "messages": []map[string]string{{"role": "user", "content": "synthetic probe"}}}
	metadata := map[string]string{"provider_locked": "true", "provider_lock_target": "b"}
	if spoof == "body" {
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
	key := "synthetic-org-probe"
	if len(keys) > 0 {
		key = keys[0]
	}
	req.Header.Set("Authorization", "Bearer "+key)
	if lock != "" {
		req.Header.Set("x-agentcc-provider-lock", lock)
	}
	if spoof == "header" {
		req.Header.Set("x-agentcc-metadata", `{"provider_locked":"true","provider_lock_target":"b"}`)
	}
	client := gateway.Client()
	client.Timeout = 5 * time.Second
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	return resp.StatusCode, string(data), resp.Header
}

func TestAcceptedProviderLockFallbackHTTP(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, breaker := range []bool{false, true} {
			for _, scenario := range []string{
				"b-only", "both-model-lists", "both-routing", "same-a", "no-backup",
				"model-map-b", "model-map-a", "alias-b", "alias-both", "prefix-b", "prefix-a", "only-provider-a",
				"org-b-only", "org-b-with-operator-a", "org-a-and-b", "primary-failover-b-first",
				"spoof-header", "spoof-body", "unlocked", "unlocked-spoof", "disallowed-lock",
			} {
				t.Run(fmt.Sprintf("stream=%t/breaker=%t/%s", stream, breaker, scenario), func(t *testing.T) {
					var witness lockedFallbackWitness
					a := lockedFallbackUpstream(t, "operator-a", &witness)
					b := lockedFallbackUpstream(t, "operator-b", &witness)
					orgA := lockedFallbackUpstream(t, "org-a", &witness)
					orgB := lockedFallbackUpstream(t, "org-b", &witness)
					cfg := lockedFallbackConfig(a.URL, b.URL, breaker)
					orgCfg := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{}}
					var orgBases map[string]config.ProviderConfig
					wantSuccess, wantOrg := false, false
					lock, spoof, wantModel := "a", "", "backup-model"
					addBackupA := func() {
						p := cfg.Providers["a"]
						p.Models = append(p.Models, "backup-model")
						cfg.Providers["a"] = p
						wantSuccess = true
					}
					switch scenario {
					case "both-model-lists", "spoof-header", "spoof-body":
						addBackupA()
						spoof = strings.TrimPrefix(scenario, "spoof-")
					case "both-routing":
						cfg.Routing.Targets["backup-model"] = []config.RoutingTargetConfig{{Provider: "b"}, {Provider: "a"}}
						wantSuccess = true
					case "same-a":
						addBackupA()
						p := cfg.Providers["b"]
						p.Models = []string{"unrelated"}
						cfg.Providers["b"] = p
					case "no-backup":
						cfg.Routing.ModelFallbacks["primary-model"] = []string{"unknown-backup"}
					case "model-map-b":
						cfg.ModelMap = map[string]string{"backup-model": "b"}
					case "model-map-a":
						cfg.ModelMap = map[string]string{"backup-model": "a"}
						wantSuccess = true
					case "alias-b", "alias-both":
						cfg.Routing.ModelFallbacks["primary-model"] = []string{"backup-alias"}
						cfg.Routing.Targets["backup-alias"] = []config.RoutingTargetConfig{{Provider: "b", ModelOverride: "backup-upstream"}}
						if scenario == "alias-both" {
							cfg.Routing.Targets["backup-alias"] = append(cfg.Routing.Targets["backup-alias"], config.RoutingTargetConfig{Provider: "a", ModelOverride: "backup-upstream"})
							wantSuccess = true
						}
						wantModel = "backup-upstream"
					case "prefix-a", "prefix-b":
						cfg.Routing.ModelFallbacks["primary-model"] = []string{strings.TrimPrefix(scenario, "prefix-") + "/backup-model"}
						wantSuccess = scenario == "prefix-a"
						// Preserve the existing operator prefix forwarding contract.
						wantModel = "a/backup-model"
					case "only-provider-a":
						addBackupA()
						delete(cfg.Providers, "b")
					case "org-b-only", "org-b-with-operator-a", "org-a-and-b":
						orgCfg.Providers["b"] = &tenant.ProviderConfig{Enabled: true, APIKey: "synthetic-org-b", Models: []string{"backup-model"}}
						orgBases = map[string]config.ProviderConfig{"a": {BaseURL: orgA.URL, APIFormat: "openai", Models: []string{"primary-model", "backup-model"}}, "b": {BaseURL: orgB.URL, APIFormat: "openai", Models: []string{"backup-model"}}}
						if scenario == "org-b-with-operator-a" {
							addBackupA()
						}
						if scenario == "org-a-and-b" {
							orgCfg.Providers["a"] = &tenant.ProviderConfig{Enabled: true, APIKey: "synthetic-org-a", Models: []string{"primary-model", "backup-model"}}
							wantSuccess, wantOrg = true, true
						}
					case "primary-failover-b-first":
						addBackupA()
						cfg.Routing.Targets["primary-model"] = []config.RoutingTargetConfig{{Provider: "b"}, {Provider: "a"}}
					case "unlocked", "unlocked-spoof":
						lock = ""
						if scenario == "unlocked-spoof" {
							spoof = "body"
						}
					case "disallowed-lock":
						lock = "b"
					}
					srv, gateway := lockedFallbackServer(t, cfg, orgCfg, orgBases)
					// B is the deterministic preferred candidate whenever routing competes.
					srv.handlers.registry.Router().RecordLatency("a", 2*time.Millisecond)
					srv.handlers.registry.Router().RecordLatency("b", time.Millisecond)
					status, body, headers := lockedFallbackRequest(t, gateway, stream, lock, spoof)
					calls := witness.snapshot()
					t.Logf("witness calls=%q status=%d provider=%q lock=%q fallback=%q stream=%t terminal_done=%t body=%s", calls, status, headers.Get("x-agentcc-provider"), headers.Get("x-agentcc-provider-locked"), headers.Get("x-agentcc-fallback-used"), stream, strings.Contains(body, "[DONE]"), body)
					for _, call := range calls {
						if strings.HasPrefix(call, "operator-b:") || strings.HasPrefix(call, "org-b:") {
							t.Errorf("accepted lock to A dispatched B: %s", call)
						}
					}
					if lock != "a" {
						if status != http.StatusForbidden || len(calls) != 0 {
							t.Fatalf("unaccepted lock response: status=%d calls=%q", status, calls)
						}
						return
					}
					if !wantSuccess {
						if status != http.StatusBadGateway || !strings.Contains(body, "synthetic primary unavailable") || strings.Contains(body, "[DONE]") || len(calls) != 1 {
							t.Fatalf("must retain primary failure without fallback: status=%d calls=%q body=%s", status, calls, body)
						}
						return
					}
					if status != http.StatusOK || !strings.Contains(body, "synthetic locked backup") || !strings.Contains(body, `"model":"`+wantModel+`"`) || (stream && !strings.Contains(body, "[DONE]")) {
						t.Errorf("same-A fallback not served: status=%d body=%s", status, body)
					}
					if headers.Get("x-agentcc-provider") != "a" || headers.Get("x-agentcc-provider-locked") != "a" || headers.Get("x-agentcc-fallback-used") != "true" {
						t.Errorf("lost canonical lock/fallback metadata: %v", headers)
					}
					prefix, credential := "operator-a:", "Bearer synthetic-operator-a"
					if wantOrg {
						prefix, credential = "org-a:", "Bearer synthetic-org-a"
					}
					want := []string{prefix + "primary-model:" + credential, prefix + wantModel + ":" + credential}
					if fmt.Sprint(calls) != fmt.Sprint(want) {
						t.Errorf("calls=%q want=%q", calls, want)
					}
				})
			}
		}
	}
}

func TestAcceptedProviderLockAdminSetupHTTP(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, breaker := range []bool{false, true} {
			t.Run(fmt.Sprintf("stream=%t/breaker=%t", stream, breaker), func(t *testing.T) {
				var witness lockedFallbackWitness
				a := lockedFallbackUpstream(t, "operator-a", &witness)
				b := lockedFallbackUpstream(t, "operator-b", &witness)
				cfg := lockedFallbackConfig(a.URL, b.URL, breaker)
				cfg.Admin.Token = "synthetic-admin-token"
				cfg.Auth.Keys = nil
				_, gateway := lockedFallbackServer(t, cfg, &tenant.OrgConfig{}, nil)
				admin := func(method, path, body string, want int) []byte {
					t.Helper()
					req, err := http.NewRequest(method, gateway.URL+path, strings.NewReader(body))
					if err != nil {
						t.Fatal(err)
					}
					req.Header.Set("Content-Type", "application/json")
					req.Header.Set("Authorization", "Bearer synthetic-admin-token")
					resp, err := gateway.Client().Do(req)
					if err != nil {
						t.Fatal(err)
					}
					defer resp.Body.Close()
					data, err := io.ReadAll(resp.Body)
					if err != nil {
						t.Fatal(err)
					}
					if resp.StatusCode != want {
						t.Fatalf("synthetic admin %s %s status=%d want=%d", method, path, resp.StatusCode, want)
					}
					return data
				}
				admin(http.MethodPut, "/-/orgs/synthetic-org/config", `{"providers":{}}`, http.StatusOK)
				data := admin(http.MethodPost, "/-/keys", `{"name":"synthetic-org-client","owner":"synthetic-org","providers":["a"],"metadata":{"org_id":"synthetic-org"}}`, http.StatusCreated)
				var key struct {
					Key string `json:"key"`
				}
				if err := json.Unmarshal(data, &key); err != nil || key.Key == "" {
					t.Fatal("synthetic admin failed to create client key")
				}
				status, body, headers := lockedFallbackRequest(t, gateway, stream, "a", "body", key.Key)
				calls := witness.snapshot()
				t.Logf("admin/public-client witness calls=%q status=%d provider=%q lock=%q stream=%t terminal_done=%t body=%s", calls, status, headers.Get("x-agentcc-provider"), headers.Get("x-agentcc-provider-locked"), stream, strings.Contains(body, "[DONE]"), body)
				if status != http.StatusBadGateway || len(calls) != 1 || !strings.HasPrefix(calls[0], "operator-a:primary-model:") || !strings.Contains(body, "synthetic primary unavailable") || strings.Contains(body, "[DONE]") {
					t.Errorf("admin-created A-only client escaped lock: status=%d calls=%q body=%s", status, calls, body)
				}
			})
		}
	}
}

func TestAcceptedProviderLockBreakerHTTP(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, state := range []string{"open", "half-open-busy", "half-open-failure"} {
			for _, org := range []bool{false, true} {
				t.Run(fmt.Sprintf("stream=%t/%s/org=%t", stream, state, org), func(t *testing.T) {
					var witness lockedFallbackWitness
					a := lockedFallbackUpstream(t, "operator-a", &witness)
					b := lockedFallbackUpstream(t, "operator-b", &witness)
					orgA := lockedFallbackUpstream(t, "org-a", &witness)
					cfg := lockedFallbackConfig(a.URL, b.URL, true)
					p := cfg.Providers["a"]
					p.Models = append(p.Models, "backup-model")
					cfg.Providers["a"] = p
					cfg.Routing.CircuitBreaker.FailureThreshold = 1
					if state != "open" {
						cfg.Routing.CircuitBreaker.Cooldown = 10 * time.Millisecond
					}
					orgCfg := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{}}
					var bases map[string]config.ProviderConfig
					if org {
						orgCfg.Providers["a"] = &tenant.ProviderConfig{Enabled: true, APIKey: "synthetic-org-a", Models: []string{"primary-model", "backup-model"}}
						bases = map[string]config.ProviderConfig{"a": {BaseURL: orgA.URL, APIFormat: "openai", Models: []string{"primary-model", "backup-model"}}}
					}
					srv, gateway := lockedFallbackServer(t, cfg, orgCfg, bases)
					reg := srv.handlers.healthMonitor.CircuitBreakerRegistry()
					cb := reg.Get("a")
					if org {
						cb = reg.GetScoped("synthetic-org", "a")
					}
					generation, allowed := cb.Allow()
					if !allowed {
						t.Fatal("initial breaker admission denied")
					}
					cb.RecordFailure(generation, models.ErrUpstreamProvider(503, "synthetic prime"))
					if state != "open" {
						time.Sleep(30 * time.Millisecond)
					}
					if state == "half-open-busy" {
						lease, allowed := cb.Acquire()
						if !allowed || cb.State() != routing.StateHalfOpen {
							t.Fatal("half-open probe not acquired")
						}
						defer lease.Abandon()
					}
					status, body, headers := lockedFallbackRequest(t, gateway, stream, "a", "header")
					calls := witness.snapshot()
					t.Logf("breaker witness state=%s org=%t calls=%q status=%d provider=%q lock=%q terminal_done=%t body=%s", cb.State(), org, calls, status, headers.Get("x-agentcc-provider"), headers.Get("x-agentcc-provider-locked"), strings.Contains(body, "[DONE]"), body)
					wantStatus, wantCalls, wantError := http.StatusServiceUnavailable, 0, "circuit_open"
					if state == "half-open-failure" {
						wantStatus, wantCalls, wantError = http.StatusBadGateway, 1, "synthetic primary unavailable"
					}
					if status != wantStatus || len(calls) != wantCalls || !strings.Contains(body, wantError) || strings.Contains(body, "[DONE]") {
						t.Errorf("breaker refusal changed: status=%d calls=%q body=%s", status, calls, body)
					}
					for _, call := range calls {
						if strings.Contains(call, "-b:") || strings.Contains(call, ":backup-model:") {
							t.Errorf("refused fallback reached upstream: %s", call)
						}
					}
					if org && reg.Get("a").State() != routing.StateClosed {
						t.Error("org breaker contaminated operator breaker")
					}
					if reg.Get("b").State() != routing.StateClosed {
						t.Error("B breaker changed without eligible B call")
					}
				})
			}
		}
	}
}

func TestAcceptedProviderLockCancellationHTTP(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, breaker := range []bool{false, true} {
			t.Run(fmt.Sprintf("stream=%t/breaker=%t", stream, breaker), func(t *testing.T) {
				var witness lockedFallbackWitness
				entered := make(chan struct{})
				a := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.Method != http.MethodPost {
						w.WriteHeader(404)
						return
					}
					io.Copy(io.Discard, r.Body)
					witness.mu.Lock()
					witness.calls = append(witness.calls, "operator-a:primary-model")
					witness.mu.Unlock()
					close(entered)
					<-r.Context().Done()
				}))
				t.Cleanup(a.Close)
				b := lockedFallbackUpstream(t, "operator-b", &witness)
				srv, _ := lockedFallbackServer(t, lockedFallbackConfig(a.URL, b.URL, breaker), &tenant.OrgConfig{}, nil)
				finished := make(chan struct{})
				gateway := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					defer close(finished)
					srv.httpServer.Handler.ServeHTTP(w, r)
				}))
				t.Cleanup(gateway.Close)
				ctx, cancel := context.WithCancel(context.Background())
				defer cancel()
				req, err := http.NewRequestWithContext(ctx, http.MethodPost, gateway.URL+"/v1/chat/completions", strings.NewReader(fmt.Sprintf(`{"model":"primary-model","stream":%t,"messages":[{"role":"user","content":"synthetic"}]}`, stream)))
				if err != nil {
					t.Fatal(err)
				}
				req.Header.Set("Content-Type", "application/json")
				req.Header.Set("Authorization", "Bearer synthetic-org-probe")
				req.Header.Set("x-agentcc-provider-lock", "a")
				done := make(chan error, 1)
				go func() {
					resp, err := gateway.Client().Do(req)
					if resp != nil {
						resp.Body.Close()
					}
					done <- err
				}()
				select {
				case <-entered:
				case <-time.After(3 * time.Second):
					t.Fatal("primary not entered")
				}
				cancel()
				select {
				case err := <-done:
					if err == nil {
						t.Error("canceled client unexpectedly succeeded")
					}
				case <-time.After(3 * time.Second):
					t.Fatal("client did not cancel")
				}
				select {
				case <-finished:
				case <-time.After(3 * time.Second):
					t.Fatal("gateway did not finish canceled request")
				}
				calls := witness.snapshot()
				t.Logf("cancellation witness calls=%q stream=%t terminal=client-canceled", calls, stream)
				if fmt.Sprint(calls) != "[operator-a:primary-model]" {
					t.Errorf("cancellation dispatched fallback: %q", calls)
				}
				if breaker && srv.handlers.healthMonitor.CircuitBreakerRegistry().Get("a").State() != routing.StateClosed {
					t.Error("cancellation tripped breaker")
				}
			})
		}
	}
}

func TestAcceptedProviderLockAdmission(t *testing.T) {
	var witness lockedFallbackWitness
	a := lockedFallbackUpstream(t, "operator-a", &witness)
	b := lockedFallbackUpstream(t, "operator-b", &witness)
	srv, _ := lockedFallbackServer(t, lockedFallbackConfig(a.URL, b.URL, true), &tenant.OrgConfig{}, nil)
	p, _ := srv.handlers.registry.GetProvider("b")
	rc := &models.RequestContext{Provider: "b", Metadata: map[string]string{"provider_locked": "true", "provider_lock_target": "a"}}
	lease, err := srv.handlers.admitProvider(context.Background(), srv.handlers.healthMonitor.CircuitBreakerRegistry(), rc, "b", p)
	defer lease.Abandon()
	if err == nil {
		t.Fatal("final admission accepted B despite server lock A")
	}
	if !routing.IsAdmissionRefusal(err) {
		t.Fatalf("lock mismatch must be a local admission refusal: %v", err)
	}
}

func TestLockedOrgBackupAliasDuringOperatorOutage(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			var witness lockedFallbackWitness
			a := lockedFallbackUpstream(t, "operator-a", &witness)
			b := lockedFallbackUpstream(t, "operator-b", &witness)
			orgA := lockedFallbackUpstream(t, "org-a", &witness)
			cfg := lockedFallbackConfig(a.URL, b.URL, true)
			cfg.Routing.ModelFallbacks["primary-model"] = []string{"route-only-alias"}
			cfg.Routing.Targets["route-only-alias"] = []config.RoutingTargetConfig{{Provider: "b", ModelOverride: "backup-model"}, {Provider: "a", ModelOverride: "backup-model"}}
			org := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
				"a": {Enabled: true, APIKey: "synthetic-org-a", Models: []string{"primary-model"}},
			}}
			srv, gateway := lockedFallbackServer(t, cfg, org, map[string]config.ProviderConfig{
				"a": {APIFormat: "openai", BaseURL: orgA.URL, Models: []string{"primary-model"}},
			})
			cb := srv.handlers.healthMonitor.CircuitBreakerRegistry().Get("a")
			for i := 0; i < cfg.Routing.CircuitBreaker.FailureThreshold; i++ {
				openReconciliationBreaker(t, cb)
			}
			status, body, headers := lockedFallbackRequest(t, gateway, stream, "a", "body")
			calls := witness.snapshot()
			if status != http.StatusOK || !strings.Contains(body, "synthetic locked backup") || (stream && !strings.Contains(body, "[DONE]")) || headers.Get("x-agentcc-provider-locked") != "a" || headers.Get("x-agentcc-provider") != "a" || headers.Get("x-agentcc-fallback-used") != "true" {
				t.Fatalf("locked alias: status=%d headers=%v body=%s", status, headers, body)
			}
			if fmt.Sprint(calls) != "[org-a:primary-model:Bearer synthetic-org-a org-a:backup-model:Bearer synthetic-org-a]" || cb.State() != routing.StateOpen {
				t.Fatalf("locked fallback escaped org A or changed operator health: calls=%q state=%s", calls, cb.State())
			}
		})
	}
}

func TestUngatedDirectCallStillHonorsAcceptedLock(t *testing.T) {
	h, p := reconciliationHandler(t)
	rc := reconciliationRC(t)
	rc.Metadata["provider_locked"], rc.Metadata["provider_lock_target"] = "true", "other"
	reg := routing.NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true}, nil)
	err := h.callProvider(context.Background(), reg, rc, p, func() error {
		t.Fatal("legacy direct dispatch ignored the accepted provider")
		return nil
	})
	if !routing.IsAdmissionRefusal(err) {
		t.Fatalf("expected accepted-lock refusal, got %v", err)
	}
}
