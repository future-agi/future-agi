package server

import (
	"bufio"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/secrets"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// keyPolicyCacheServer runs auth (20), the response cache (35) and guardrails
// (50) in the production order, for an org with the cache on and one key
// policy (Secret Detection, block) on key A only.
func keyPolicyCacheServer(t *testing.T, mode string) (send func(key, prompt string) *httptest.ResponseRecorder, policy *tenant.KeyGuardrailPolicy, up *dialectUpstream) {
	t.Helper()
	up = &dialectUpstream{reply: "safe reply"}
	upstream := httptest.NewServer(up)
	t.Cleanup(upstream.Close)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "a", Key: "sk-agentcc-cache-a", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "org-kp-cache"}},
		{Name: "b", Key: "sk-agentcc-cache-b", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "org-kp-cache"}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "k", APIFormat: "openai", Models: []string{"gpt-5.5"}}
	cfg.Guardrails = config.GuardrailsConfig{Enabled: true, DefaultTimeout: time.Second}
	store := tenant.NewStore()
	policy = &tenant.KeyGuardrailPolicy{ID: "pol-a", Name: "a only", Mode: mode, KeyIDs: []string{"key_1"},
		Checks: map[string]*tenant.GuardrailCheck{"secret-detection": {Enabled: true, Action: "block"}}}
	store.Set("org-kp-cache", &tenant.OrgConfig{
		Cache:      &tenant.CacheConfig{Enabled: true},
		Guardrails: &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{policy}},
	})
	registry := map[string]guardrails.Guardrail{"secret-detection": secrets.New(nil)}
	grEngine := guardrails.NewEngine(cfg.Guardrails, registry)
	providersRegistry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keys := auth.NewKeyStore(cfg.Auth)
	cachePlugin := cacheplugin.New(cache.NewMemoryBackend(cache.NewStore(100)), time.Minute)
	cachePlugin.SetTenantStore(store) // as cmd/agentcc/main.go wires it
	engine := pipeline.NewEngine(authplugin.New(keys, true), cachePlugin, guardrails.NewPlugin(grEngine, registry, nil, nil, store))
	srv := New(cfg, "", providersRegistry, engine, keys, grEngine, nil, nil, testModelDBPtr(), store, nil)
	srv.ready.Store(true)
	send = func(key, prompt string) *httptest.ResponseRecorder {
		req := guardedRoutes[0].request(false, prompt)
		req.Header.Set("Authorization", "Bearer "+key)
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		return w
	}
	return send, policy, up
}

// A cache hit skips the pre-stage guardrails, so a key with a key policy must
// never be served a response another key (without that policy) stored.
func TestKeyPolicyNotSkippedByAnotherKeysCacheEntry(t *testing.T) {
	send, _, up := keyPolicyCacheServer(t, "enforce")
	prompt := "deploy with " + awsKey

	// Key B has no policy: it passes and the response is cached.
	if w := send("sk-agentcc-cache-b", prompt); w.Code != http.StatusOK {
		t.Fatalf("key B: status %d; want 200: %s", w.Code, w.Body.String())
	}
	// Control: key B's identical request is a cache hit.
	if w := send("sk-agentcc-cache-b", prompt); !strings.HasPrefix(w.Header().Get("x-agentcc-cache"), "hit") || up.served() != 1 {
		t.Fatalf("key B control: cache header %q, upstream calls %d; want a hit and one call", w.Header().Get("x-agentcc-cache"), up.served())
	}

	// Key A's enforce policy blocks the same body, cache entry or not.
	w := send("sk-agentcc-cache-a", prompt)
	if w.Code != http.StatusForbidden {
		t.Fatalf("key A: status %d, cache %q; want 403 from its key policy: %s", w.Code, w.Header().Get("x-agentcc-cache"), w.Body.String())
	}
	if up.served() != 1 {
		t.Fatalf("upstream calls = %d; want 1 (key A is blocked before the provider)", up.served())
	}

	// Key A still uses the cache for requests its policy lets through.
	send("sk-agentcc-cache-a", "say hello")
	if w := send("sk-agentcc-cache-a", "say hello"); w.Code != http.StatusOK || !strings.HasPrefix(w.Header().Get("x-agentcc-cache"), "hit") {
		t.Fatalf("key A clean repeat: status %d, cache %q; want a cache hit", w.Code, w.Header().Get("x-agentcc-cache"))
	}
	// ...and those entries are its own: key B does not get key A's entry.
	before := up.served()
	if w := send("sk-agentcc-cache-b", "say hello"); strings.HasPrefix(w.Header().Get("x-agentcc-cache"), "hit") || up.served() != before+1 {
		t.Fatalf("key B after key A: cache %q, calls %d; want a miss", w.Header().Get("x-agentcc-cache"), up.served()-before)
	}
}

// Switching a key's policy from Monitor to Enforce blocks the same request at
// once, not when the entry stored in Monitor mode expires.
func TestKeyPolicyEnforceAfterMonitorIgnoresMonitorEntry(t *testing.T) {
	send, policy, up := keyPolicyCacheServer(t, "monitor")
	prompt := "deploy with " + awsKey

	if w := send("sk-agentcc-cache-a", prompt); w.Code != http.StatusOK {
		t.Fatalf("monitor: status %d; want 200: %s", w.Code, w.Body.String())
	}
	if w := send("sk-agentcc-cache-a", prompt); !strings.HasPrefix(w.Header().Get("x-agentcc-cache"), "hit") {
		t.Fatalf("monitor control: want a cache hit, got cache %q", w.Header().Get("x-agentcc-cache"))
	}

	policy.Mode = "enforce"
	w := send("sk-agentcc-cache-a", prompt)
	if w.Code != http.StatusForbidden {
		t.Fatalf("enforce: status %d, cache %q; want 403: %s", w.Code, w.Header().Get("x-agentcc-cache"), w.Body.String())
	}
	if up.served() != 1 {
		t.Fatalf("upstream calls = %d; want 1", up.served())
	}
}

// The key-policy partition is joined to the namespace with a NUL byte, which
// no client-chosen x-agentcc-cache-namespace can contain: the HTTP server
// refuses a request whose header value carries one.
func TestCacheNamespaceHeaderCannotCarryNUL(t *testing.T) {
	raw := "POST /v1/chat/completions HTTP/1.1\r\nHost: gw\r\nX-Agentcc-Cache-Namespace: default\x00key_policies=abc\r\nContent-Length: 0\r\n\r\n"
	if _, err := http.ReadRequest(bufio.NewReader(strings.NewReader(raw))); err == nil {
		t.Fatal("a header value with a NUL byte was accepted")
	}
}
