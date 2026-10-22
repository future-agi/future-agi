package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// End to end through the HTTP server: two API keys in two orgs, both orgs
// with the response cache turned on, and an upstream that answers every call
// with a fresh, numbered reply, so a reply served from the cache is told
// apart from one the upstream made.

const (
	cacheScopeOrgA = "org-cache-a"
	cacheScopeOrgB = "org-cache-b"
	cacheScopeKeyA = "sk-agentcc-cache-scope-a"
	cacheScopeKeyB = "sk-agentcc-cache-scope-b"
	// A key minted without an org: it lands in the no-org partition, and no
	// key metadata overwrites the key_org_id the gateway sets.
	cacheScopeKeyNoOrg = "sk-agentcc-cache-scope-noorg"
)

func startCountingMockOpenAI(t *testing.T, calls *atomic.Int64) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" {
			w.WriteHeader(404)
			return
		}
		var req models.ChatCompletionRequest
		_ = json.NewDecoder(r.Body).Decode(&req)
		n := calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"id":      fmt.Sprintf("chatcmpl-upstream-%d", n),
			"object":  "chat.completion",
			"created": 1700000000,
			"model":   req.Model,
			"choices": []any{map[string]any{
				"index":         0,
				"message":       map[string]string{"role": "assistant", "content": fmt.Sprintf("upstream answer %d", n)},
				"finish_reason": "stop",
			}},
			"usage": map[string]int{"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
		})
	}))
	t.Cleanup(srv.Close)
	return srv
}

func createCacheScopeServer(t *testing.T, upstreamURL string) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Admin.Token = bindingAdminToken
	cfg.Auth.Enabled = true
	// Internal keys so both orgs can use the config.yaml provider pointed at
	// the loopback mock (org providers may not use loopback base URLs). The
	// cache partition depends only on the key's org_id metadata, which every
	// key type carries the same way.
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "org-a", Key: cacheScopeKeyA, Owner: "alice", KeyType: "internal", Metadata: map[string]string{"org_id": cacheScopeOrgA}},
		{Name: "org-b", Key: cacheScopeKeyB, Owner: "bob", KeyType: "internal", Metadata: map[string]string{"org_id": cacheScopeOrgB}},
		{Name: "no-org", Key: cacheScopeKeyNoOrg, Owner: "carol", KeyType: "internal"},
	}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   upstreamURL,
		APIKey:    "dummy",
		APIFormat: "openai",
		Models:    []string{"gpt-4o"},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("registry: %v", err)
	}

	tenantStore := tenant.NewStore()
	for _, org := range []string{cacheScopeOrgA, cacheScopeOrgB} {
		tenantStore.Set(org, &tenant.OrgConfig{Cache: &tenant.CacheConfig{Enabled: true}})
	}

	cachePlugin := cacheplugin.NewFromStore(cache.NewStore(1000), 5*time.Minute)
	cachePlugin.SetSemanticStoreFromConcrete(cache.NewSemanticStore(0.8, 256, 1000))

	keyStore := auth.NewKeyStore(cfg.Auth)
	engine := pipeline.NewEngine(authplugin.New(keyStore, true), cachePlugin)
	srv := New(cfg, "", registry, engine, keyStore, nil, nil, nil, testModelDBPtr(), tenantStore, nil)
	srv.ready.Store(true)
	return srv
}

type cachedCall struct {
	status  int
	id      string
	content string
}

func postCacheScopeChat(t *testing.T, srv *Server, key, prompt string, headers map[string]string) cachedCall {
	t.Helper()
	return postCacheScopeChatWithMetadata(t, srv, key, prompt, headers, nil)
}

// postCacheScopeChatWithMetadata also sends bodyMetadata as the request
// body's "metadata" field, the other place a caller can set dimensions.
func postCacheScopeChatWithMetadata(t *testing.T, srv *Server, key, prompt string, headers, bodyMetadata map[string]string) cachedCall {
	t.Helper()
	payload := map[string]any{
		"model":    "gpt-4o",
		"messages": []map[string]string{{"role": "user", "content": prompt}},
	}
	if bodyMetadata != nil {
		payload["metadata"] = bodyMetadata
	}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	var resp models.ChatCompletionResponse
	_ = json.Unmarshal(w.Body.Bytes(), &resp)
	out := cachedCall{status: w.Code, id: resp.ID}
	if w.Code != 200 {
		t.Logf("HTTP %d: %s", w.Code, w.Body.String())
	}
	if len(resp.Choices) > 0 {
		var s string
		_ = json.Unmarshal(resp.Choices[0].Message.Content, &s)
		out.content = s
	}
	return out
}

func TestChatCompletion_ResponseCacheNeverCrossesOrgs(t *testing.T) {
	var calls atomic.Int64
	upstream := startCountingMockOpenAI(t, &calls)
	srv := createCacheScopeServer(t, upstream.URL)

	const (
		prompt = "what is the capital of France"
		near   = "what is the capital of france?"
	)

	// Org A's first call goes upstream and fills the cache.
	a1 := postCacheScopeChat(t, srv, cacheScopeKeyA, prompt, nil)
	if a1.status != 200 || a1.content != "upstream answer 1" {
		t.Fatalf("org A first call: %+v (upstream calls %d)", a1, calls.Load())
	}

	// Org A again: served from its own cache, upstream not called.
	if a2 := postCacheScopeChat(t, srv, cacheScopeKeyA, prompt, nil); a2.content != a1.content || calls.Load() != 1 {
		t.Fatalf("org A repeat should be an exact hit on its own entry: %+v (upstream calls %d)", a2, calls.Load())
	}
	if a3 := postCacheScopeChat(t, srv, cacheScopeKeyA, near, nil); a3.content != a1.content || calls.Load() != 1 {
		t.Fatalf("org A near-duplicate should be a semantic hit on its own entry: %+v (upstream calls %d)", a3, calls.Load())
	}

	// Org B, identical prompt: must go upstream and get its own answer.
	b1 := postCacheScopeChat(t, srv, cacheScopeKeyB, prompt, nil)
	if b1.status != 200 {
		t.Fatalf("org B call failed: %+v", b1)
	}
	if b1.content == a1.content {
		t.Fatalf("org B was served org A's cached answer %q (exact layer)", b1.content)
	}
	if calls.Load() != 2 {
		t.Fatalf("org B's call should have gone upstream: upstream calls %d, want 2", calls.Load())
	}

	// Org B, near-duplicate, with a namespace header naming org A: still its
	// own partition, so it may hit its own entry but never org A's.
	b2 := postCacheScopeChat(t, srv, cacheScopeKeyB, near, map[string]string{"x-agentcc-cache-namespace": cacheScopeOrgA})
	if b2.content == a1.content {
		t.Fatalf("org B was served org A's cached answer %q (semantic layer)", b2.content)
	}
}

// The cache partition is the authenticated key's org. PUT /-/keys/{id}, which
// Django's PATCH /agentcc/api-keys/{id}/ forwards metadata to, never moves a
// key into another org's cache.
func TestChatCompletion_KeyUpdateCannotMoveCacheScope(t *testing.T) {
	var calls atomic.Int64
	upstream := startCountingMockOpenAI(t, &calls)
	srv := createCacheScopeServer(t, upstream.URL)

	a1 := postCacheScopeChat(t, srv, cacheScopeKeyA, "org A private prompt", nil)
	if a1.status != 200 || a1.content != "upstream answer 1" {
		t.Fatalf("org A first call: %+v (upstream calls %d)", a1, calls.Load())
	}

	for _, raw := range []string{cacheScopeKeyB, cacheScopeKeyNoOrg} {
		key := srv.keyStore.Lookup(raw)
		w := bindingCall(t, srv, http.MethodPut, "/-/keys/"+key.ID,
			`{"metadata":{"org_id":"`+cacheScopeOrgA+`"}}`)
		if w.Code != http.StatusConflict {
			t.Errorf("%s: PUT with org A's org_id: status %d, want 409; body %s", key.Name, w.Code, w.Body.String())
		}
		if _, err := srv.keyStore.Update(key.ID, nil, nil, nil, nil, map[string]string{"org_id": cacheScopeOrgA}); err == nil {
			t.Errorf("%s: KeyStore.Update moved the key to org A", key.Name)
		}
	}
	if got := resolvedOrg(srv, cacheScopeKeyB); got != cacheScopeOrgB {
		t.Errorf("key B resolves org %q after the update, want %q", got, cacheScopeOrgB)
	}

	before := calls.Load()
	b1 := postCacheScopeChat(t, srv, cacheScopeKeyB, "org A private prompt", nil)
	if b1.status != 200 || b1.content == a1.content || calls.Load() != before+1 {
		t.Errorf("org B's key was served org A's cached answer after a metadata update: %+v (upstream calls %d, want %d)",
			b1, calls.Load(), before+1)
	}
	if n := postCacheScopeChat(t, srv, cacheScopeKeyNoOrg, "org A private prompt", nil); n.content == a1.content {
		t.Errorf("the no-org key was served org A's cached answer after a metadata update: %+v", n)
	}
}

// A caller must not pick the partition through its own metadata: not with the
// x-agentcc-metadata header, not with the body's metadata field, and not with
// the internal key names in another case. The auth plugin writes key_org_id
// from the key itself (absent for a key without an org), and the metadata
// block list keeps caller-supplied key_* names out, so no caller picks a
// partition, with or without an org.
func TestChatCompletion_CallerMetadataCannotPickCacheScope(t *testing.T) {
	var calls atomic.Int64
	upstream := startCountingMockOpenAI(t, &calls)
	srv := createCacheScopeServer(t, upstream.URL)

	const prompt = "org A private prompt"
	a1 := postCacheScopeChat(t, srv, cacheScopeKeyA, prompt, nil)
	if a1.status != 200 || a1.content != "upstream answer 1" {
		t.Fatalf("org A first call: %+v (upstream calls %d)", a1, calls.Load())
	}

	smuggled := map[string]string{
		"key_org_id": cacheScopeOrgA,
		"KEY_ORG_ID": cacheScopeOrgA,
		"Key_Org_Id": cacheScopeOrgA,
		"org_id":     cacheScopeOrgA,
		"cache_key":  "x",
	}
	header, _ := json.Marshal(smuggled)
	for _, key := range []string{cacheScopeKeyB, cacheScopeKeyNoOrg} {
		for name, call := range map[string]func() cachedCall{
			"header": func() cachedCall {
				return postCacheScopeChat(t, srv, key, prompt, map[string]string{"x-agentcc-metadata": string(header)})
			},
			"body": func() cachedCall {
				return postCacheScopeChatWithMetadata(t, srv, key, prompt, nil, smuggled)
			},
			"both": func() cachedCall {
				return postCacheScopeChatWithMetadata(t, srv, key, prompt, map[string]string{"x-agentcc-metadata": string(header)}, smuggled)
			},
		} {
			got := call()
			if got.status != 200 {
				t.Fatalf("%s via %s: %+v", srv.keyStore.Lookup(key).Name, name, got)
			}
			if got.content == a1.content {
				t.Errorf("%s picked org A's partition via %s metadata: served %q", srv.keyStore.Lookup(key).Name, name, got.content)
			}
		}
	}
}
