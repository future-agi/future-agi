package cache

import (
	"context"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// recordingBackend is an exact-match backend that keeps the TTL of every Set.
type recordingBackend struct {
	entries map[string]*models.ChatCompletionResponse
	ttls    map[string]time.Duration
}

func newRecordingBackend() *recordingBackend {
	return &recordingBackend{entries: map[string]*models.ChatCompletionResponse{}, ttls: map[string]time.Duration{}}
}

func (b *recordingBackend) Get(key string) (*models.ChatCompletionResponse, bool) {
	r, ok := b.entries[key]
	return r, ok
}

func (b *recordingBackend) Set(key string, resp *models.ChatCompletionResponse, ttl time.Duration) {
	b.entries[key], b.ttls[key] = resp, ttl
}

func (b *recordingBackend) Delete(key string) { delete(b.entries, key) }

// cacheCall is one request through the plugin: an org, a prompt, request
// headers, and the context the plugin saw.
type cacheCall struct {
	rc *models.RequestContext
}

func newCacheCall(t *testing.T, org, prompt string, headers ...string) *cacheCall {
	t.Helper()
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.Request = makeReq("gpt-4o", prompt)
	rc.Model = "gpt-4o"
	rc.Metadata["key_org_id"] = org
	rc.RequestHeaders = http.Header{}
	for i := 0; i+1 < len(headers); i += 2 {
		rc.RequestHeaders.Set(headers[i], headers[i+1])
	}
	return &cacheCall{rc: rc}
}

// request runs ProcessRequest; respond then runs ProcessResponse with resp
// when the request was not served from the cache.
func (c *cacheCall) request(p *Plugin) pipeline.PluginResult {
	return p.ProcessRequest(context.Background(), c.rc)
}

func (c *cacheCall) respond(p *Plugin, id string) {
	if c.rc.Flags.CacheHit {
		return
	}
	c.rc.Response = makeResp(id)
	p.ProcessResponse(context.Background(), c.rc)
}

func (c *cacheCall) status() string {
	for _, h := range c.rc.ResponseHeaders() {
		if strings.EqualFold(h.Name, HeaderCacheStatus) {
			return h.Value
		}
	}
	return ""
}

func wantBadCacheHeader(t *testing.T, res pipeline.PluginResult, header string) {
	t.Helper()
	if res.Error == nil || res.Error.Status != http.StatusBadRequest || res.Error.Code != ErrCodeInvalidCacheHeader ||
		res.Error.Param == nil || *res.Error.Param != header {
		t.Fatalf("result error = %+v, want 400 %s with param %s", res.Error, ErrCodeInvalidCacheHeader, header)
	}
}

// TestCacheKeyHeader: x-agentcc-cache-key replaces the request's cache key,
// within the caller's own org and namespace only.
func TestCacheKeyHeader(t *testing.T) {
	p, store := newTestPlugin()

	first := newCacheCall(t, "org-a", "hello", HeaderCacheKey, "conv-1")
	if res := first.request(p); res.Action != pipeline.Continue || first.status() != "MISS" {
		t.Fatalf("first call: action %v status %q, want a MISS", res.Action, first.status())
	}
	first.respond(p, "resp-conv-1")

	// Another prompt under the same key is served from it: the header is
	// the cache key, not part of the default one.
	other := newCacheCall(t, "org-a", "a different prompt", HeaderCacheKey, "conv-1")
	if res := other.request(p); res.Action != pipeline.ShortCircuit || other.rc.Response.ID != "resp-conv-1" || other.status() != "HIT" {
		t.Fatalf("same key, other prompt: action %v status %q, want a HIT on resp-conv-1", res.Action, other.status())
	}

	// The prompt without the header has the default key, and was never stored.
	plain := newCacheCall(t, "org-a", "hello")
	if res := plain.request(p); res.Action != pipeline.Continue || plain.rc.Metadata["cache_key"] != BuildCacheKey(cache.OrgScope("org-a"), "default", plain.rc.Request) {
		t.Fatalf("no header: action %v key %q, want a miss on the default key", res.Action, plain.rc.Metadata["cache_key"])
	}

	for _, tc := range []struct {
		name, org, key, namespace string
	}{
		{"another org's entry under the same key is never served", "org-b", "conv-1", ""},
		{"another key misses", "org-a", "conv-2", ""},
		{"another namespace misses", "org-a", "conv-1", "ns-2"},
	} {
		c := newCacheCall(t, tc.org, "a different prompt", HeaderCacheKey, tc.key)
		if tc.namespace != "" {
			c.rc.Metadata["cache_namespace"] = tc.namespace
		}
		if res := c.request(p); res.Action != pipeline.Continue || c.status() != "MISS" {
			t.Errorf("%s: action %v status %q, want a MISS", tc.name, res.Action, c.status())
		}
	}

	// The key is derived, not used raw, so it cannot address another
	// request's default-keyed entry either.
	defaultKey := BuildCacheKey(cache.OrgScope("org-a"), "default", makeReq("gpt-4o", "hello"))
	store.Set(defaultKey, makeResp("default-entry"), time.Minute)
	raw := newCacheCall(t, "org-a", "x", HeaderCacheKey, defaultKey)
	if res := raw.request(p); res.Action != pipeline.Continue {
		t.Fatalf("a raw stored key used as the header was served (%+v)", raw.rc.Response)
	}

	t.Run("length", func(t *testing.T) {
		ok := newCacheCall(t, "org-a", "x", HeaderCacheKey, strings.Repeat("k", MaxCacheKeyLen))
		if res := ok.request(p); res.Error != nil {
			t.Fatalf("a %d-character key is allowed: %+v", MaxCacheKeyLen, res.Error)
		}
		long := newCacheCall(t, "org-a", "x", HeaderCacheKey, strings.Repeat("k", MaxCacheKeyLen+1))
		wantBadCacheHeader(t, long.request(p), HeaderCacheKey)
		empty := newCacheCall(t, "org-a", "x")
		empty.rc.RequestHeaders[http.CanonicalHeaderKey(HeaderCacheKey)] = []string{"  "}
		wantBadCacheHeader(t, empty.request(p), HeaderCacheKey)
	})

	t.Run("only when the org cache is enabled", func(t *testing.T) {
		c := newCacheCall(t, "org-a", "a different prompt", HeaderCacheKey, "conv-1")
		c.rc.Metadata["org_cache_disabled"] = "true"
		if res := c.request(p); res.Action != pipeline.Continue || c.status() != "BYPASS" || c.rc.Metadata["cache_key"] != "" {
			t.Fatalf("org cache off: action %v status %q key %q, want a BYPASS with no key", res.Action, c.status(), c.rc.Metadata["cache_key"])
		}
	})

	t.Run("a caller-chosen key is exact-match only", func(t *testing.T) {
		p, _, sem := newTestSemanticPlugin()
		c := newCacheCall(t, "org-a", "what is the capital of France", HeaderCacheKey, "k-1")
		c.request(p)
		c.respond(p, "france")
		if sem.Len() != 0 {
			t.Fatalf("a request with its own cache key was stored in the fuzzy layer (%d entries)", sem.Len())
		}
		plain := newCacheCall(t, "org-a", "what is the capital of France")
		plain.request(p)
		plain.respond(p, "france-plain")
		near := newCacheCall(t, "org-a", "what is the capital of france?", HeaderCacheKey, "k-2")
		if res := near.request(p); res.Action != pipeline.Continue {
			t.Fatalf("a near-duplicate under its own key %q was served by the fuzzy layer (%s)", "k-2", near.rc.Response.ID)
		}
	})
}

// TestCustomCacheKeyIsPartitionedLikeTheDefaultKey: the store key for a
// caller's own cache key starts from the same org partition token as the
// default key (cache.OrgScope), so two orgs, or an org and the no-org
// tenant, never share it, and it has the default key's shape (SHA-256, 128
// bits, 32 lowercase hex digits).
func TestCustomCacheKeyIsPartitionedLikeTheDefaultKey(t *testing.T) {
	orgA := buildCustomCacheKey(cache.OrgScope("org-a"), "default", "conv-1")
	if len(orgA) != 32 || strings.Trim(orgA, "0123456789abcdef") != "" {
		t.Fatalf("custom cache key %q, want 32 lowercase hex digits", orgA)
	}
	for name, other := range map[string]string{
		"another org":        buildCustomCacheKey(cache.OrgScope("org-b"), "default", "conv-1"),
		"the no-org tenant":  buildCustomCacheKey(cache.NoOrgScope, "default", "conv-1"),
		"the raw org id":     buildCustomCacheKey("org-a", "default", "conv-1"),
		"the default key":    BuildCacheKey(cache.OrgScope("org-a"), "default", makeReq("gpt-4o", "conv-1")),
		"another namespace":  buildCustomCacheKey(cache.OrgScope("org-a"), "ns-2", "conv-1"),
		"another caller key": buildCustomCacheKey(cache.OrgScope("org-a"), "default", "conv-2"),
	} {
		if other == orgA {
			t.Errorf("%s has org A's custom cache key %q", name, orgA)
		}
	}
	if again := buildCustomCacheKey(cache.OrgScope("org-a"), "default", "conv-1"); again != orgA {
		t.Errorf("custom cache key is not deterministic: %q then %q", orgA, again)
	}
}

// TestCacheTTLClamp: x-agentcc-cache-ttl is seconds (or a duration), clamped
// to 60 s - 30 days; an unreadable value is ignored.
func TestCacheTTLClamp(t *testing.T) {
	for _, tc := range []struct {
		value string
		want  time.Duration
	}{
		{"30", time.Minute},
		{"0", time.Minute},
		{"60", time.Minute},
		{"3600", time.Hour},
		{"2592000", MaxCacheTTL},
		{"2592001", MaxCacheTTL},
		{"10m", 10 * time.Minute},
		{"50ms", time.Minute},
		{"1000h", MaxCacheTTL},
	} {
		b := newRecordingBackend()
		p := New(b, 5*time.Minute)
		c := newCacheCall(t, "org-a", "hello "+tc.value, HeaderCacheTTL, tc.value)
		if res := c.request(p); res.Error != nil {
			t.Fatalf("ttl %q refused: %+v", tc.value, res.Error)
		}
		c.respond(p, "r")
		if got := b.ttls[c.rc.Metadata["cache_key"]]; got != tc.want {
			t.Errorf("x-agentcc-cache-ttl %q stored with TTL %v, want %v", tc.value, got, tc.want)
		}
	}

	// The handler's copy of the header (rc.Metadata["cache_ttl"]) is clamped
	// the same way.
	b := newRecordingBackend()
	p := New(b, 5*time.Minute)
	c := newCacheCall(t, "org-a", "metadata ttl")
	c.rc.Metadata["cache_ttl"] = "5"
	c.request(p)
	c.respond(p, "r")
	if got := b.ttls[c.rc.Metadata["cache_key"]]; got != time.Minute {
		t.Errorf("cache_ttl 5 stored with TTL %v, want 1m", got)
	}

	// Without the header, the org's TTL and then the default apply unclamped.
	c = newCacheCall(t, "org-a", "org ttl")
	c.rc.Metadata["org_cache_ttl"] = "30s"
	c.request(p)
	c.respond(p, "r")
	if got := b.ttls[c.rc.Metadata["cache_key"]]; got != 30*time.Second {
		t.Errorf("org TTL 30s stored with TTL %v, want 30s", got)
	}
	c = newCacheCall(t, "org-a", "default ttl")
	c.request(p)
	c.respond(p, "r")
	if got := b.ttls[c.rc.Metadata["cache_key"]]; got != 5*time.Minute {
		t.Errorf("default stored with TTL %v, want 5m", got)
	}

	// An unreadable or negative TTL is ignored: the request is served and the
	// entry gets the default TTL.
	for _, bad := range []string{"abc", "-5", "-1m", "1.5", ""} {
		c := newCacheCall(t, "org-a", "unreadable ttl "+bad)
		c.rc.RequestHeaders[http.CanonicalHeaderKey(HeaderCacheTTL)] = []string{bad}
		if res := c.request(p); res.Error != nil || res.Action != pipeline.Continue {
			t.Fatalf("ttl %q: result %+v, want the request served", bad, res)
		}
		c.respond(p, "r")
		if got := b.ttls[c.rc.Metadata["cache_key"]]; got != 5*time.Minute {
			t.Errorf("unreadable ttl %q stored with TTL %v, want the default 5m", bad, got)
		}
	}
}

// TestCacheStatusHeader: every request the plugin checks and does not
// refuse gets x-agentcc-cache-status HIT, MISS or BYPASS.
func TestCacheStatusHeader(t *testing.T) {
	p, store, _ := newTestSemanticPlugin()
	seed := newCacheCall(t, "org-a", "what is the capital of France")
	seed.request(p)
	seed.respond(p, "seed")
	if seed.status() != "MISS" {
		t.Fatalf("a miss got status %q", seed.status())
	}

	edge, _ := newTestPlugin()
	edge.SetEdgeHandler(NewEdgeCacheHandler(config.CacheEdgeConfig{Enabled: true}))
	conditional := newCacheCall(t, "org-a", "hello")
	conditional.rc.Metadata["if_none_match"] = `"` + BuildCacheKey(cache.OrgScope("org-a"), "default", conditional.rc.Request) + `"`
	if edge.ProcessRequest(context.Background(), conditional.rc); conditional.status() != "HIT" {
		t.Errorf("a conditional 304 got status %q, want HIT", conditional.status())
	}

	for _, tc := range []struct {
		name  string
		setup func(c *cacheCall)
		want  string
	}{
		{"exact hit", func(c *cacheCall) {}, "HIT"},
		{"fuzzy hit", func(c *cacheCall) { c.rc.Request = makeReq("gpt-4o", "what is the capital of france?") }, "HIT"},
		{"miss", func(c *cacheCall) {
			c.rc.Request = makeReq("gpt-4o", "something else entirely, about quantum entanglement")
		}, "MISS"},
		{"force refresh", func(c *cacheCall) { c.rc.Metadata["cache_force_refresh"] = "true" }, "MISS"},
		{"no-cache", func(c *cacheCall) { c.rc.Metadata["cache_control"] = "no-cache" }, "MISS"},
		{"no-store", func(c *cacheCall) { c.rc.Metadata["cache_control"] = "no-store" }, "BYPASS"},
		{"stream", func(c *cacheCall) { c.rc.IsStream = true }, "BYPASS"},
		{"org cache off", func(c *cacheCall) { c.rc.Metadata["org_cache_disabled"] = "true" }, "BYPASS"},
		{"no chat request", func(c *cacheCall) { c.rc.Request = nil }, "BYPASS"},
		{"bypass header", func(c *cacheCall) { c.rc.RequestHeaders.Set(HeaderCacheMode, "bypass") }, "BYPASS"},
	} {
		c := newCacheCall(t, "org-a", "what is the capital of France")
		tc.setup(c)
		c.request(p)
		if got := c.status(); got != tc.want {
			t.Errorf("%s: x-agentcc-cache-status = %q, want %q", tc.name, got, tc.want)
		}
	}
	if store.Len() != 1 {
		t.Errorf("store has %d entries, want only the seed", store.Len())
	}
}

// TestCacheHeadersParseWithoutAllocating: reading the cache headers, which
// the plugin does twice per request, costs no allocation when none is sent.
func TestCacheHeadersParseWithoutAllocating(t *testing.T) {
	c := newCacheCall(t, "org-a", "x", "Authorization", "Bearer k")
	allocs := testing.AllocsPerRun(100, func() {
		if _, apiErr := parseControls(c.rc); apiErr != nil {
			t.Fatal(apiErr)
		}
	})
	if allocs != 0 {
		t.Fatalf("parseControls made %v allocations, want 0", allocs)
	}
}

// TestCacheBypass: x-agentcc-cache: bypass neither reads nor writes the
// cache.
func TestCacheBypass(t *testing.T) {
	p, store, sem := newTestSemanticPlugin()
	seed := newCacheCall(t, "org-a", "hello")
	seed.request(p)
	seed.respond(p, "cached")

	for _, v := range []string{"bypass", "BYPASS", " Bypass "} {
		c := newCacheCall(t, "org-a", "hello")
		c.rc.RequestHeaders[http.CanonicalHeaderKey(HeaderCacheMode)] = []string{v}
		if res := c.request(p); res.Action != pipeline.Continue || c.rc.Flags.CacheHit || c.status() != "BYPASS" {
			t.Fatalf("bypass %q: action %v hit %v status %q, want the cache skipped", v, res.Action, c.rc.Flags.CacheHit, c.status())
		}
		c.respond(p, "fresh")
	}
	if got, _ := store.Get(BuildCacheKey(cache.OrgScope("org-a"), "default", makeReq("gpt-4o", "hello"))); got == nil || got.ID != "cached" {
		t.Fatalf("a bypassed response overwrote the cached entry: %+v", got)
	}

	fresh := newCacheCall(t, "org-a", "never cached", HeaderCacheMode, "bypass")
	fresh.request(p)
	fresh.respond(p, "fresh")
	if store.Len() != 1 || sem.Len() != 1 {
		t.Fatalf("store %d, fuzzy %d entries after a bypassed request, want only the seed in each", store.Len(), sem.Len())
	}

	bad := newCacheCall(t, "org-a", "hello", HeaderCacheMode, "skip")
	wantBadCacheHeader(t, bad.request(p), HeaderCacheMode)
}

// TestFuzzyCacheSkippedForAgentTrafficStoreAndSearch: an agent-tagged request
// neither searches nor fills the fuzzy layer, and still uses the exact one.
// The end-to-end case, tagged through x-claude-code-session-id alone, is
// TestFuzzyCacheSkippedForAgentTraffic.
func TestFuzzyCacheSkippedForAgentTrafficStoreAndSearch(t *testing.T) {
	p, store, sem := newTestSemanticPlugin()
	seed := newCacheCall(t, "org-a", "what is the capital of France")
	seed.request(p)
	seed.respond(p, "france")

	agent := newCacheCall(t, "org-a", "what is the capital of france?")
	agent.rc.Metadata["agent_tagged"] = "true"
	if res := agent.request(p); res.Action != pipeline.Continue || agent.status() != "MISS" {
		t.Fatalf("agent near-duplicate: action %v status %q, want a MISS", res.Action, agent.status())
	}
	agent.respond(p, "agent")
	if sem.Len() != 1 || store.Len() != 2 {
		t.Fatalf("after the agent call: fuzzy %d, exact %d entries; want the agent response in the exact layer only", sem.Len(), store.Len())
	}

	again := newCacheCall(t, "org-a", "what is the capital of france?")
	again.rc.Metadata["agent_tagged"] = "true"
	if res := again.request(p); res.Action != pipeline.ShortCircuit || again.rc.Response.ID != "agent" || again.status() != "HIT" {
		t.Fatalf("agent exact repeat: action %v status %q, want an exact HIT", res.Action, again.status())
	}

	untagged := newCacheCall(t, "org-a", "What is the capital of France!")
	if res := untagged.request(p); res.Action != pipeline.ShortCircuit || untagged.rc.Metadata["cache_status"] != "hit_semantic" {
		t.Fatalf("untagged near-duplicate: action %v cache_status %q, want a fuzzy hit", res.Action, untagged.rc.Metadata["cache_status"])
	}
}
