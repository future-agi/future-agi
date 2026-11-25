package cache

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func keyPolicyStore(mode string) (*tenant.Store, *tenant.KeyGuardrailPolicy) {
	pol := &tenant.KeyGuardrailPolicy{ID: "pol", Name: "pol", Mode: mode, KeyIDs: []string{"key_a"},
		Checks: map[string]*tenant.GuardrailCheck{"secret-detection": {Enabled: true, Action: "block"}}}
	store := tenant.NewStore()
	store.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{pol}}})
	return store, pol
}

func keyRequest(key, text string) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.Metadata["org_id"] = "org"
	rc.Metadata["auth_key_id"] = key
	rc.Model = "gpt-4o"
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o", Messages: []models.Message{textMsg("user", text)}}
	return rc
}

// A semantic hit answers a similar, different prompt: the request's own text
// never reaches the pre-stage checks. Selected keys bypass all cache layers.
func TestKeyPolicyRequestsSkipTheSemanticCache(t *testing.T) {
	p, _, _ := newTestSemanticPlugin()
	store, _ := keyPolicyStore("enforce")
	p.SetTenantStore(store)

	rc1 := keyRequest("key_a", "deploy with akiaiosfodnn7example please")
	p.ProcessRequest(context.Background(), rc1)
	rc1.Response = makeResp("first")
	p.ProcessResponse(context.Background(), rc1)
	rc1.Release()

	rc2 := keyRequest("key_a", "deploy with AKIAIOSFODNN7EXAMPLE please")
	defer rc2.Release()
	p.ProcessRequest(context.Background(), rc2)
	if rc2.Flags.CacheHit {
		t.Fatalf("key with a key policy got a %s cache hit for a different prompt", rc2.Metadata["cache_status"])
	}

	// Control: a key without key policies still gets semantic hits.
	rc3 := keyRequest("key_b", "deploy with akiaiosfodnn7example please")
	p.ProcessRequest(context.Background(), rc3)
	rc3.Response = makeResp("third")
	p.ProcessResponse(context.Background(), rc3)
	rc3.Release()
	rc4 := keyRequest("key_b", "deploy with AKIAIOSFODNN7EXAMPLE please")
	defer rc4.Release()
	p.ProcessRequest(context.Background(), rc4)
	if rc4.Metadata["cache_status"] != "hit_semantic" {
		t.Fatalf("control: cache_status %q; want hit_semantic", rc4.Metadata["cache_status"])
	}
}

// Changing a selected policy's mode while a request runs must not turn a
// bypassed request into a cache write.
func TestResponseNotStoredWhenKeyPoliciesChangedMidRequest(t *testing.T) {
	p, exact, semantic := policySpyPlugin()
	store, pol := keyPolicyStore("enforce")
	p.SetTenantStore(store)

	rc := keyRequest("key_a", "hello")
	defer rc.Release()
	p.ProcessRequest(context.Background(), rc)
	assertPolicyBypass(t, rc, exact, semantic)
	pol.Mode = "monitor" // the control plane switched it before guardrails ran
	rc.Response = makeResp("judged-in-monitor")
	p.ProcessResponse(context.Background(), rc)
	assertPolicyBypass(t, rc, exact, semantic)

	// Control: an unscoped key still stores as usual. Even unchanged selected
	// policies now bypass caching: a fingerprint cannot prove admission replay.
	rc2 := keyRequest("key_b", "hello again")
	defer rc2.Release()
	p.ProcessRequest(context.Background(), rc2)
	rc2.Response = makeResp("stored")
	p.ProcessResponse(context.Background(), rc2)
	if _, ok := exact.Backend.Get(rc2.Metadata["cache_key"]); !ok {
		t.Fatal("control: response not stored")
	}
}

// Count backend calls, not just hits: selected policies must not read or write
// either backend, even when there is nothing in the cache to return.
type policyExactSpy struct {
	cache.Backend
	gets, sets int
}

func (s *policyExactSpy) Get(key string) (*models.ChatCompletionResponse, bool) {
	s.gets++
	return s.Backend.Get(key)
}

func (s *policyExactSpy) Set(key string, resp *models.ChatCompletionResponse, ttl time.Duration) {
	s.sets++
	s.Backend.Set(key, resp, ttl)
}

type policySemanticSpy struct {
	cache.SemanticBackend
	searches, sets int
}

func (s *policySemanticSpy) Search(scope string, vector []float32, partition string) *cache.SearchResult {
	s.searches++
	return s.SemanticBackend.Search(scope, vector, partition)
}

func (s *policySemanticSpy) Set(scope, key string, vector []float32, partition string, resp *models.ChatCompletionResponse, ttl time.Duration) {
	s.sets++
	s.SemanticBackend.Set(scope, key, vector, partition, resp, ttl)
}

func policySpyPlugin() (*Plugin, *policyExactSpy, *policySemanticSpy) {
	p, _, _ := newTestSemanticPlugin()
	exact := &policyExactSpy{Backend: p.store}
	semantic := &policySemanticSpy{SemanticBackend: p.semanticStore}
	p.store = exact
	p.semanticStore = semantic
	p.SetEdgeHandler(NewEdgeCacheHandler(config.CacheEdgeConfig{Enabled: true}))
	return p, exact, semantic
}

func assertPolicyBypass(t *testing.T, rc *models.RequestContext, exact *policyExactSpy, semantic *policySemanticSpy) {
	t.Helper()
	if got := rc.Metadata["cache_status"]; got != "skip_key_policy" {
		t.Errorf("cache_status = %q; want skip_key_policy", got)
	}
	if rc.Flags.CacheHit {
		t.Error("selected request got a cache hit")
	}
	if exact.gets != 0 || exact.sets != 0 || semantic.searches != 0 || semantic.sets != 0 {
		t.Errorf("selected request touched backends: exact Get/Set = %d/%d; semantic Search/Set = %d/%d", exact.gets, exact.sets, semantic.searches, semantic.sets)
	}
	for _, key := range []string{"cache_key", keyPoliciesMetadata, cacheInitializedMetadata, "cache_semantic_partition", "cache_similarity", "edge_cache_etag", "edge_cache_vary"} {
		if _, ok := rc.Metadata[key]; ok {
			t.Errorf("bypass retained internal %s", key)
		}
	}
	if rc.Metadata["edge_cache"] != "SKIP" || rc.Metadata["edge_cache_control"] != "no-store" {
		t.Errorf("bypass permitted edge caching: %v", rc.Metadata)
	}
}

func injectCacheOutputs(rc *models.RequestContext) {
	for _, key := range []string{"cache_key", keyPoliciesMetadata, "cache_semantic_partition", "cache_similarity", "edge_cache_etag", "edge_cache_vary"} {
		rc.Metadata[key] = "forged"
	}
	rc.Metadata[cacheInitializedMetadata] = "true"
	rc.Metadata["cache_status"] = "hit_exact"
	rc.Metadata["edge_cache"] = "HIT"
	rc.Metadata["edge_cache_control"] = "public, max-age=99999"
}

func TestSelectedPoliciesBypassEveryCacheLayer(t *testing.T) {
	for _, mode := range []string{"enforce", "monitor", "disabled_checks", "empty_checks"} {
		for _, directive := range []string{"ordinary", "force_refresh", "no-cache", "no-store"} {
			t.Run(mode+"/"+directive, func(t *testing.T) {
				p, exact, semantic := policySpyPlugin()
				store, pol := keyPolicyStore(mode)
				switch mode {
				case "disabled_checks":
					pol.Mode = "enforce"
					pol.Checks["secret-detection"].Enabled = false
				case "empty_checks":
					pol.Mode = "monitor"
					pol.Checks = nil
				}
				p.SetTenantStore(store)
				for attempt := 0; attempt < 2; attempt++ {
					rc := keyRequest("key_a", "hello")
					defer rc.Release()
					injectCacheOutputs(rc)
					if directive == "force_refresh" {
						rc.Metadata["cache_force_refresh"] = "true"
					} else if directive != "ordinary" {
						rc.Metadata["cache_control"] = directive
					}
					// A matching conditional must not short-circuit admission. Seed
					// both the old policy partition and the unscoped namespace too.
					fp, _ := p.keyPolicyCacheState(rc)
					key := BuildCacheKey(orgScope(rc), namespaceWithKeyPolicies("default", fp), rc.Request)
					exact.Backend.Set(key, makeResp("cached"), time.Minute)
					exact.Backend.Set(BuildCacheKey(orgScope(rc), "default", rc.Request), makeResp("unscoped"), time.Minute)
					rc.Metadata["if_none_match"] = `"` + key + `"`
					p.ProcessRequest(context.Background(), rc)
					assertPolicyBypass(t, rc, exact, semantic)
					rc.Response = makeResp("provider")
					p.ProcessResponse(context.Background(), rc)
					assertPolicyBypass(t, rc, exact, semantic)
				}
			})
		}
	}
}

func TestSelectedPolicyRevokedMidflightStillCannotStore(t *testing.T) {
	p, exact, semantic := policySpyPlugin()
	store, _ := keyPolicyStore("monitor")
	p.SetTenantStore(store)
	rc := keyRequest("key_a", "hello")
	defer rc.Release()
	injectCacheOutputs(rc)
	p.ProcessRequest(context.Background(), rc)
	assertPolicyBypass(t, rc, exact, semantic)
	store.Set("org", &tenant.OrgConfig{})
	rc.Response = makeResp("provider")
	p.ProcessResponse(context.Background(), rc)
	assertPolicyBypass(t, rc, exact, semantic)
}

func TestUnscopedRequestAssignedPolicyMidflightCannotStore(t *testing.T) {
	p, exact, semantic := policySpyPlugin()
	store := tenant.NewStore()
	p.SetTenantStore(store)
	rc := keyRequest("key_a", "hello")
	defer rc.Release()
	p.ProcessRequest(context.Background(), rc)
	if rc.Metadata["cache_status"] != "miss" || rc.Metadata["cache_key"] == "" || exact.gets != 1 || semantic.searches != 1 {
		t.Fatalf("initially unscoped lookup did not run: %v", rc.Metadata)
	}
	selected, _ := keyPolicyStore("monitor")
	store.Set("org", selected.Get("org"))
	// Only response-stage backend calls count from here.
	exact.gets, semantic.searches = 0, 0
	rc.Response = makeResp("provider")
	p.ProcessResponse(context.Background(), rc)
	assertPolicyBypass(t, rc, exact, semantic)
}

func TestUnscopedCacheLifecycleUnchanged(t *testing.T) {
	for _, layer := range []string{"exact", "semantic", "conditional"} {
		t.Run(layer, func(t *testing.T) {
			p, exact, semantic := policySpyPlugin()
			store, _ := keyPolicyStore("enforce")
			p.SetTenantStore(store)
			rc := keyRequest("key_b", "deploy with akiaiosfodnn7example please")
			defer rc.Release()
			injectCacheOutputs(rc)
			p.ProcessRequest(context.Background(), rc)
			if rc.Metadata["cache_status"] != "miss" || rc.Metadata["cache_key"] == "forged" {
				t.Fatalf("unscoped request did not initialize cleanly: %v", rc.Metadata)
			}
			rc.Response = makeResp("provider")
			p.ProcessResponse(context.Background(), rc)
			if exact.sets != 1 || semantic.sets != 1 || rc.Metadata["edge_cache_control"] != "public, max-age=300" {
				t.Fatalf("unscoped store/edge headers changed: exact=%d semantic=%d metadata=%v", exact.sets, semantic.sets, rc.Metadata)
			}
			next := keyRequest("key_b", "deploy with akiaiosfodnn7example please")
			defer next.Release()
			if layer == "semantic" {
				next.Request.Messages = []models.Message{textMsg("user", "deploy with AKIAIOSFODNN7EXAMPLE please")}
			}
			if layer == "conditional" {
				next.Metadata["if_none_match"] = rc.Metadata["edge_cache_etag"]
			}
			p.ProcessRequest(context.Background(), next)
			if !next.Flags.CacheHit || next.Metadata["cache_status"] != "hit_"+layer {
				t.Fatalf("unscoped %s hit changed: %v", layer, next.Metadata)
			}
		})
	}
}

func TestCacheStoreRequiresRequestInitialization(t *testing.T) {
	for _, skip := range []string{"missing_request_stage", "no-store", "org_disabled", "stream", "nil_request"} {
		t.Run(skip, func(t *testing.T) {
			p, exact, semantic := policySpyPlugin()
			rc := keyRequest("key_b", "hello")
			defer rc.Release()
			injectCacheOutputs(rc)
			if skip == "missing_request_stage" {
				// cache_ keys are reserved by the HTTP parser. Even a legacy or
				// internal caller injecting cache_key cannot stand in for init.
				delete(rc.Metadata, cacheInitializedMetadata)
			} else {
				switch skip {
				case "no-store":
					rc.Metadata["cache_control"] = "no-store"
				case "org_disabled":
					rc.Metadata["org_cache_disabled"] = "true"
				case "stream":
					rc.IsStream = true
				case "nil_request":
					rc.Request = nil
				}
				p.ProcessRequest(context.Background(), rc)
				for _, key := range []string{"cache_key", keyPoliciesMetadata, cacheInitializedMetadata, "cache_semantic_partition", "edge_cache_control"} {
					if _, ok := rc.Metadata[key]; ok {
						t.Errorf("early exit retained injected %s", key)
					}
				}
				// Clearing the reason for bypass does not authorize a later store.
				delete(rc.Metadata, "cache_control")
				delete(rc.Metadata, "org_cache_disabled")
				rc.IsStream = false
				rc.Request = makeReq("gpt-4o", "hello")
			}
			rc.Response = makeResp("provider")
			p.ProcessResponse(context.Background(), rc)
			if exact.gets != 0 || exact.sets != 0 || semantic.searches != 0 || semantic.sets != 0 {
				t.Fatalf("uninitialized request touched cache: exact=%+v semantic=%+v", exact, semantic)
			}
		})
	}
}
