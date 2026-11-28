package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/expression"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/webhook"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type cacheGateBackend struct {
	cache.Backend
	gets atomic.Int64
	sets atomic.Int64
}

func (b *cacheGateBackend) Get(key string) (*models.ChatCompletionResponse, bool) {
	b.gets.Add(1)
	return b.Backend.Get(key)
}
func (b *cacheGateBackend) Set(key string, response *models.ChatCompletionResponse, ttl time.Duration) {
	b.sets.Add(1)
	b.Backend.Set(key, response, ttl)
}

type cacheGateFixture struct {
	send     func(key, user, metadata string) *httptest.ResponseRecorder
	tenants  *tenant.Store
	backend  *cacheGateBackend
	upstream *dialectUpstream
}

// Exercise actual HTTP decoding/auth/metadata, production plugin order, and a
// real exact cache. Only the provider and webhook are synthetic loopback servers.
func newCacheGateFixture(t *testing.T, settings *tenant.GuardrailConfig) cacheGateFixture {
	t.Helper()
	up := &dialectUpstream{reply: "safe reply"}
	upstream := httptest.NewServer(up)
	t.Cleanup(upstream.Close)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "selected", Key: "cache-gate-selected", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "cache-gate-org"}},
		{Name: "unselected", Key: "cache-gate-unselected", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "cache-gate-org"}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "synthetic", APIFormat: "openai", Models: []string{"gpt-5.5"}}
	cfg.Guardrails = config.GuardrailsConfig{Enabled: true, DefaultTimeout: time.Second}
	tenants := tenant.NewStore()
	tenants.Set("cache-gate-org", &tenant.OrgConfig{Cache: &tenant.CacheConfig{Enabled: true}, Guardrails: settings})
	registry := map[string]guardrails.Guardrail{}
	engine := guardrails.NewEngine(cfg.Guardrails, registry)
	factory := func(name string, cfg map[string]interface{}) guardrails.Guardrail {
		if expression.IsExpressionConfig(cfg) {
			return expression.New(name, cfg)
		}
		return webhook.New(name, cfg)
	}
	providersRegistry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keys := auth.NewKeyStore(cfg.Auth)
	backend := &cacheGateBackend{Backend: cache.NewMemoryBackend(cache.NewStore(100))}
	cached := cacheplugin.New(backend, time.Minute)
	cached.SetTenantStore(tenants)
	plugins := pipeline.NewEngine(authplugin.New(keys, true), cached, guardrails.NewPlugin(engine, registry, factory, nil, tenants))
	srv := New(cfg, "", providersRegistry, plugins, keys, engine, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	send := func(key, user, metadata string) *httptest.ResponseRecorder {
		body, _ := json.Marshal(map[string]interface{}{"model": "gpt-5.5", "messages": []map[string]string{{"role": "user", "content": "identical safe prompt"}}, "user": user})
		req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(string(body)))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer cache-gate-"+key)
		if metadata != "" {
			req.Header.Set("X-Agentcc-Metadata", metadata)
		}
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		return w
	}
	return cacheGateFixture{send: send, tenants: tenants, backend: backend, upstream: up}
}

func cacheGateSettings(check map[string]interface{}) *tenant.GuardrailConfig {
	return &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{{ID: "selected-policy", Name: "selected policy", Mode: "enforce", KeyIDs: []string{"key_1"}, Checks: map[string]*tenant.GuardrailCheck{"request-gate": {Enabled: true, Action: "block", Config: check}}}}}
}

func TestCacheGateRequestDependentAdmission(t *testing.T) {
	for _, tc := range []struct{ name, expr, allowedUser, restrictedUser, allowedMeta, restrictedMeta string }{
		{name: "user", expr: "request.user == 'restricted'", allowedUser: "allowed", restrictedUser: "restricted"},
		{name: "metadata", expr: "metadata.customer == 'restricted'", allowedUser: "same", restrictedUser: "same", allowedMeta: `{"customer":"allowed"}`, restrictedMeta: `{"customer":"restricted"}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := newCacheGateFixture(t, cacheGateSettings(map[string]interface{}{"expression": tc.expr}))
			allowed := f.send("selected", tc.allowedUser, tc.allowedMeta)
			if allowed.Code != http.StatusOK {
				t.Fatalf("allowed status=%d body=%s", allowed.Code, allowed.Body.String())
			}
			denied := f.send("selected", tc.restrictedUser, tc.restrictedMeta)
			t.Logf("allowed=%d denied=%d cache=%q upstream=%d cache_get=%d cache_set=%d", allowed.Code, denied.Code, denied.Header().Get("X-Agentcc-Cache"), f.upstream.served(), f.backend.gets.Load(), f.backend.sets.Load())
			if denied.Code != http.StatusForbidden || f.upstream.served() != 1 || f.backend.gets.Load() != 0 || f.backend.sets.Load() != 0 {
				t.Fatalf("request-dependent precheck bypassed: status=%d upstream=%d body=%s", denied.Code, f.upstream.served(), denied.Body.String())
			}
		})
	}
}

// An external decision may change without any request or policy change. A
// replay-safe request hash alone cannot authorize the second response.
func TestCacheGateWebhookRechecksIdenticalRequest(t *testing.T) {
	for _, mode := range []string{"enforce", "monitor"} {
		t.Run(mode, func(t *testing.T) {
			var calls atomic.Int64
			hook := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				_ = json.NewEncoder(w).Encode(map[string]bool{"pass": calls.Add(1) == 1})
			}))
			defer hook.Close()
			settings := cacheGateSettings(map[string]interface{}{"url": hook.URL})
			settings.KeyPolicies[0].Mode = mode
			f := newCacheGateFixture(t, settings)
			first := f.send("selected", "identical", "")
			second := f.send("selected", "identical", "")
			want, wantUpstream := http.StatusForbidden, 1
			if mode == "monitor" {
				want, wantUpstream = http.StatusOK, 2
			}
			t.Logf("mode=%s first=%d second=%d hook_calls=%d upstream=%d cache_get=%d cache_set=%d", mode, first.Code, second.Code, calls.Load(), f.upstream.served(), f.backend.gets.Load(), f.backend.sets.Load())
			if first.Code != http.StatusOK || second.Code != want || calls.Load() != 2 || f.upstream.served() != wantUpstream || f.backend.gets.Load() != 0 || f.backend.sets.Load() != 0 {
				t.Fatalf("decision not refreshed: first=%d second=%d calls=%d upstream=%d body=%s", first.Code, second.Code, calls.Load(), f.upstream.served(), second.Body.String())
			}
			if mode == "monitor" && second.Header().Get("X-Agentcc-Cache") != "skip_key_policy" {
				t.Fatalf("monitored response must bypass caching: %q", second.Header().Get("X-Agentcc-Cache"))
			}
		})
	}
}

func TestCacheGateUnselectedAndPolicyRevocation(t *testing.T) {
	settings := cacheGateSettings(map[string]interface{}{"expression": "request.user == 'restricted'"})
	f := newCacheGateFixture(t, settings)
	for i := 0; i < 2; i++ {
		w := f.send("unselected", "restricted", "")
		if w.Code != http.StatusOK {
			t.Fatalf("unselected status=%d", w.Code)
		}
		if i == 1 && w.Header().Get("X-Agentcc-Cache") != "hit_exact" {
			t.Fatalf("unselected repeat cache=%q", w.Header().Get("X-Agentcc-Cache"))
		}
	}
	if f.upstream.served() != 1 || f.backend.gets.Load() != 2 || f.backend.sets.Load() != 1 {
		t.Fatalf("unselected calls upstream=%d get=%d set=%d", f.upstream.served(), f.backend.gets.Load(), f.backend.sets.Load())
	}
	if w := f.send("selected", "restricted", ""); w.Code != http.StatusForbidden {
		t.Fatalf("selected used unselected entry: status=%d", w.Code)
	}
	// Immutable config replacement revokes the policy. Unscoped cache behavior
	// is intentionally restored (and can reuse the unscoped key's entry).
	f.tenants.Set("cache-gate-org", &tenant.OrgConfig{Cache: &tenant.CacheConfig{Enabled: true}})
	if w := f.send("selected", "restricted", ""); w.Code != http.StatusOK || w.Header().Get("X-Agentcc-Cache") != "hit_exact" {
		t.Fatalf("revoked policy status=%d cache=%q", w.Code, w.Header().Get("X-Agentcc-Cache"))
	}
	f.tenants.Set("cache-gate-org", &tenant.OrgConfig{Cache: &tenant.CacheConfig{Enabled: true}, Guardrails: settings})
	if w := f.send("selected", "restricted", ""); w.Code != http.StatusForbidden {
		t.Fatalf("reassigned policy status=%d", w.Code)
	}
	if f.upstream.served() != 1 || f.backend.gets.Load() != 3 || f.backend.sets.Load() != 1 {
		t.Fatalf("policy changes touched cache incorrectly: upstream=%d get=%d set=%d", f.upstream.served(), f.backend.gets.Load(), f.backend.sets.Load())
	}
}

func TestCacheGateInFlightFailOpenNeverStores(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	var calls atomic.Int64
	hook := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) == 1 {
			close(entered)
			select {
			case <-release:
			case <-r.Context().Done():
				return
			}
		}
		http.Error(w, "synthetic unavailable", http.StatusServiceUnavailable)
	}))
	defer hook.Close()
	settings := cacheGateSettings(map[string]interface{}{"url": hook.URL})
	settings.FailOpen = true
	f := newCacheGateFixture(t, settings)
	done := make(chan *httptest.ResponseRecorder, 1)
	go func() { done <- f.send("selected", "same", "") }()
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		close(release)
		t.Fatal("webhook did not start")
	}
	strict := *settings
	strict.FailOpen = false
	f.tenants.Set("cache-gate-org", &tenant.OrgConfig{Cache: &tenant.CacheConfig{Enabled: true}, Guardrails: &strict})
	close(release)
	first := <-done
	second := f.send("selected", "same", "")
	t.Logf("old=%d strict=%d upstream=%d hooks=%d gets=%d sets=%d", first.Code, second.Code, f.upstream.served(), calls.Load(), f.backend.gets.Load(), f.backend.sets.Load())
	if first.Code != http.StatusOK || second.Code != http.StatusServiceUnavailable || f.upstream.served() != 1 || calls.Load() != 2 || f.backend.gets.Load() != 0 || f.backend.sets.Load() != 0 {
		t.Fatalf("old admission contaminated strict config: old=%d strict=%d upstream=%d hooks=%d gets=%d sets=%d", first.Code, second.Code, f.upstream.served(), calls.Load(), f.backend.gets.Load(), f.backend.sets.Load())
	}
}

func TestCacheGateEffectiveTimeoutChange(t *testing.T) {
	var calls atomic.Int64
	hook := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		select {
		case <-time.After(25 * time.Millisecond):
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"pass":true}`))
		case <-r.Context().Done():
		}
	}))
	defer hook.Close()
	settings := cacheGateSettings(map[string]interface{}{"url": hook.URL})
	settings.TimeoutMs = 1000
	f := newCacheGateFixture(t, settings)
	first := f.send("selected", "same", "")
	fast := *settings
	fast.TimeoutMs = 1
	f.tenants.Set("cache-gate-org", &tenant.OrgConfig{Cache: &tenant.CacheConfig{Enabled: true}, Guardrails: &fast})
	second := f.send("selected", "same", "")
	t.Logf("slow=%d fast=%d upstream=%d hook_calls=%d gets=%d sets=%d", first.Code, second.Code, f.upstream.served(), calls.Load(), f.backend.gets.Load(), f.backend.sets.Load())
	if first.Code != http.StatusOK || second.Code != http.StatusServiceUnavailable || f.upstream.served() != 1 || f.backend.gets.Load() != 0 || f.backend.sets.Load() != 0 {
		t.Fatalf("effective timeout bypassed: first=%d second=%d upstream=%d gets=%d sets=%d", first.Code, second.Code, f.upstream.served(), f.backend.gets.Load(), f.backend.sets.Load())
	}
}

func TestCacheGateEffectiveFailOpenChange(t *testing.T) {
	var webhookCalls atomic.Int64
	hook := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		webhookCalls.Add(1)
		http.Error(w, "synthetic unavailable", http.StatusServiceUnavailable)
	}))
	defer hook.Close()
	settings := cacheGateSettings(map[string]interface{}{"url": hook.URL})
	settings.FailOpen = true
	f := newCacheGateFixture(t, settings)
	first := f.send("selected", "same", "")
	if first.Code != http.StatusOK {
		t.Fatalf("fail-open status=%d body=%s", first.Code, first.Body.String())
	}
	strict := *settings
	strict.FailOpen = false
	f.tenants.Set("cache-gate-org", &tenant.OrgConfig{Cache: &tenant.CacheConfig{Enabled: true}, Guardrails: &strict})
	second := f.send("selected", "same", "")
	t.Logf("fail_open=%d fail_closed=%d cache=%q upstream=%d webhook=%d cache_get=%d cache_set=%d", first.Code, second.Code, second.Header().Get("X-Agentcc-Cache"), f.upstream.served(), webhookCalls.Load(), f.backend.gets.Load(), f.backend.sets.Load())
	if second.Code != http.StatusServiceUnavailable || !strings.Contains(second.Body.String(), "guardrail_unavailable") || f.upstream.served() != 1 || webhookCalls.Load() != 2 || f.backend.gets.Load() != 0 || f.backend.sets.Load() != 0 {
		t.Fatalf("effective failure policy bypassed: status=%d upstream=%d webhook=%d body=%s", second.Code, f.upstream.served(), webhookCalls.Load(), second.Body.String())
	}
}
