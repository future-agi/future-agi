package cache

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/cache"
)

// Every cache backend in the tree, driven through the plugin with keys from
// two orgs and a key with no org. The exact (L1) backends are key-value
// stores, so the org has to be in the key; the semantic (L2) backends search
// by similarity, so the org has to be in the filter. Both are checked here for
// every implementation that can run in-process.
//
// S3, GCS and Azure Blob are L1 key-value stores like disk and Redis: they
// store and fetch exactly the key the plugin hands them, so the key-derivation
// tests cover them. They need cloud credentials, so they are not run here.

// --- L1 -------------------------------------------------------------------

type exactBackendCase struct {
	name string
	make func(t *testing.T) cache.Backend
}

// redisTestTarget reads the Redis the cache tests may write to. It is opt-in
// and separate from TEST_REDIS_ADDR (which other packages default to
// localhost:6379 DB 0) so this suite never lands on a shared database by
// accident.
func redisTestTarget(t *testing.T) (addr string, db int) {
	addr = os.Getenv("TEST_CACHE_REDIS_ADDR")
	if addr == "" {
		t.Skip("set TEST_CACHE_REDIS_ADDR (and TEST_CACHE_REDIS_DB) to run the Redis cache backend case")
	}
	if v := os.Getenv("TEST_CACHE_REDIS_DB"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			t.Fatalf("TEST_CACHE_REDIS_DB=%q: %v", v, err)
		}
		db = n
	}
	return addr, db
}

func exactBackends() []exactBackendCase {
	return []exactBackendCase{
		{"memory", func(t *testing.T) cache.Backend {
			return cache.NewMemoryBackend(cache.NewStore(1000))
		}},
		{"disk", func(t *testing.T) cache.Backend {
			d, err := cache.NewDiskBackend(t.TempDir(), 0, false, time.Hour)
			if err != nil {
				t.Fatalf("disk backend: %v", err)
			}
			t.Cleanup(d.Close)
			return d
		}},
		{"redis", func(t *testing.T) cache.Backend {
			addr, db := redisTestTarget(t)
			// A per-run prefix keeps this suite's keys apart from anything
			// else in the database; cleanup deletes exactly the keys it wrote
			// (never FLUSHDB) and the short plugin TTL is the backstop.
			prefix := fmt.Sprintf("agentcc-test:cache-org-scope:%d:", time.Now().UnixNano())
			r, err := cache.NewRedisBackend(addr, "", db, prefix, false, 2, 2*time.Second)
			if err != nil {
				t.Skipf("redis at %s db %d not reachable: %v", addr, db, err)
			}
			t.Cleanup(r.Close)
			return r
		}},
	}
}

func TestBackendMatrix_ExactNeverCrossesOrgs(t *testing.T) {
	for _, bc := range exactBackends() {
		t.Run(bc.name, func(t *testing.T) {
			backend := bc.make(t)
			p := New(backend, 30*time.Second)
			var written []string
			t.Cleanup(func() {
				for _, k := range written {
					backend.Delete(k)
				}
			})

			written = append(written, prime(t, p, orgA, "gpt-4o", franceQ, "org-a-secret"))

			if status, got := ask(p, orgB, "gpt-4o", franceQ); got != "" {
				t.Errorf("org B served org A's entry %q (status %q)", got, status)
			}
			if status, got := ask(p, "", "gpt-4o", franceQ); got != "" {
				t.Errorf("no-org key served org A's entry %q (status %q)", got, status)
			}
			if status, got := ask(p, orgA, "gpt-4o", franceQ); status != "hit_exact" || got != "org-a-secret" {
				t.Errorf("org A lost its own entry: status %q resp %q", status, got)
			}

			written = append(written, prime(t, p, "", "gpt-4o", franceQ, "no-org-resp"))
			if status, got := ask(p, orgB, "gpt-4o", franceQ); got != "" {
				t.Errorf("org B served the no-org entry %q (status %q)", got, status)
			}
			if status, got := ask(p, "", "gpt-4o", franceQ); status != "hit_exact" || got != "no-org-resp" {
				t.Errorf("no-org lost its own entry: status %q resp %q", status, got)
			}
			if status, got := ask(p, orgA, "gpt-4o", franceQ); got != "org-a-secret" {
				t.Errorf("org A served %q after a no-org write (status %q), want its own entry", got, status)
			}
		})
	}
}

// --- L2 -------------------------------------------------------------------

type semanticBackendCase struct {
	name string
	// make returns the backend and a function that writes an entry with no
	// scope field, or nil
	// when the backend holds nothing across a restart.
	make func(t *testing.T) (cache.SemanticBackend, func(model, prompt, respID string))
}

const (
	testDims      = 256
	testThreshold = 0.8
)

func unscopedResponseJSON(respID string) string {
	b, _ := json.Marshal(makeResp(respID))
	return string(b)
}

func semanticBackends() []semanticBackendCase {
	expires := func() int64 { return time.Now().Add(time.Hour).Unix() }
	return []semanticBackendCase{
		{"memory", func(t *testing.T) (cache.SemanticBackend, func(string, string, string)) {
			return cache.NewMemorySemanticBackend(cache.NewSemanticStore(testThreshold, testDims, 1000)), nil
		}},
		{"qdrant", func(t *testing.T) (cache.SemanticBackend, func(string, string, string)) {
			f := newFakeQdrant(t)
			b, err := cache.NewQdrantBackend(f.srv.URL, "agentcc_cache", "", testThreshold, testDims, 2*time.Second)
			if err != nil {
				t.Fatalf("qdrant backend: %v", err)
			}
			unscoped := func(model, prompt, respID string) {
				f.mu.Lock()
				defer f.mu.Unlock()
				id := fmt.Sprintf("%032x", len(f.points)+1)
				f.points[id] = &fakePoint{id: id, vector: cache.Vectorize(prompt, testDims), payload: map[string]any{
					"model": model, "cache_key": id, "response": unscopedResponseJSON(respID), "expires_at": float64(expires()),
				}}
			}
			return b, unscoped
		}},
		{"pinecone", func(t *testing.T) (cache.SemanticBackend, func(string, string, string)) {
			f := newFakePinecone(t)
			b, err := cache.NewPineconeBackend(f.srv.URL, "test-key", testThreshold, testDims, 2*time.Second)
			if err != nil {
				t.Fatalf("pinecone backend: %v", err)
			}
			unscoped := func(model, prompt, respID string) {
				f.mu.Lock()
				defer f.mu.Unlock()
				id := fmt.Sprintf("unscoped-%d", len(f.vectors)+1)
				f.vectors[id] = &fakePoint{id: id, vector: cache.Vectorize(prompt, testDims), payload: map[string]any{
					"model": model, "cache_key": id, "response": unscopedResponseJSON(respID), "expires_at": float64(expires()),
				}}
			}
			return b, unscoped
		}},
		{"weaviate", func(t *testing.T) (cache.SemanticBackend, func(string, string, string)) {
			f := newFakeWeaviate(t, "AgentccSemanticCache")
			b, err := cache.NewWeaviateBackend(f.srv.URL, "", "", testThreshold, testDims, 2*time.Second)
			if err != nil {
				t.Fatalf("weaviate backend: %v", err)
			}
			unscoped := func(model, prompt, respID string) {
				f.mu.Lock()
				defer f.mu.Unlock()
				f.objects = append(f.objects, &fakePoint{vector: cache.Vectorize(prompt, testDims), payload: map[string]any{
					"cache_key": "unscoped", "model_name": model, "response_json": unscopedResponseJSON(respID), "expires_at": float64(expires()),
				}})
			}
			return b, unscoped
		}},
	}
}

// newSemanticPlugin pairs a semantic backend with an in-memory exact layer.
// The tests ask with a near-duplicate prompt, which has a different exact key,
// so a hit can only come from the semantic layer.
func newSemanticPlugin(sem cache.SemanticBackend) *Plugin {
	p := NewFromStore(cache.NewStore(1000), 5*time.Minute)
	p.SetSemanticStore(sem)
	return p
}

func TestBackendMatrix_SemanticNeverCrossesOrgs(t *testing.T) {
	for _, bc := range semanticBackends() {
		t.Run(bc.name, func(t *testing.T) {
			t.Run("org B never gets org A's near-duplicate", func(t *testing.T) {
				sem, _ := bc.make(t)
				p := newSemanticPlugin(sem)
				prime(t, p, orgA, "gpt-4o", franceQ, "org-a-secret")
				if status, got := ask(p, orgB, "gpt-4o", franceQNear); got != "" {
					t.Fatalf("org B served org A's entry %q (status %q)", got, status)
				}
			})
			t.Run("same org hits", func(t *testing.T) {
				sem, _ := bc.make(t)
				p := newSemanticPlugin(sem)
				prime(t, p, orgA, "gpt-4o", franceQ, "org-a-resp")
				if status, got := ask(p, orgA, "gpt-4o", franceQNear); status != "hit_semantic" || got != "org-a-resp" {
					t.Fatalf("status %q resp %q, want hit_semantic org-a-resp", status, got)
				}
			})
			t.Run("no-org and org partitions are separate", func(t *testing.T) {
				sem, _ := bc.make(t)
				p := newSemanticPlugin(sem)
				prime(t, p, orgA, "gpt-4o", franceQ, "org-a-secret")
				if status, got := ask(p, "", "gpt-4o", franceQNear); got != "" {
					t.Fatalf("no-org key served org A's entry %q (status %q)", got, status)
				}
				prime(t, p, "", "gpt-4o", franceQ, "no-org-resp")
				if status, got := ask(p, orgB, "gpt-4o", franceQNear); got != "" {
					t.Fatalf("org B served the no-org entry %q (status %q)", got, status)
				}
				if status, got := ask(p, "", "gpt-4o", franceQNear); status != "hit_semantic" || got != "no-org-resp" {
					t.Fatalf("no-org lost its own entry: status %q resp %q", status, got)
				}
			})
			t.Run("entries written with no scope are never served", func(t *testing.T) {
				sem, unscoped := bc.make(t)
				if unscoped == nil {
					t.Skip("the in-memory backend holds no entry without a scope")
				}
				p := newSemanticPlugin(sem)
				unscoped("gpt-4o", franceQ, "unscoped-entry")
				for _, org := range []string{orgA, orgB, ""} {
					if status, got := ask(p, org, "gpt-4o", franceQNear); got != "" {
						t.Errorf("org %q served an entry with no scope %q (status %q)", org, got, status)
					}
				}
			})
		})
	}
}

// The scope filter has to reach the vector store, not just the plugin. These
// pin the wire shape each remote backend sends.

func TestQdrantSearchSendsScopeFilter(t *testing.T) {
	f := newFakeQdrant(t)
	b, err := cache.NewQdrantBackend(f.srv.URL, "agentcc_cache", "", testThreshold, testDims, 2*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	p := newSemanticPlugin(b)
	prime(t, p, orgA, "gpt-4o", franceQ, "org-a")
	ask(p, orgA, "gpt-4o", franceQNear)

	f.mu.Lock()
	defer f.mu.Unlock()
	var found bool
	for _, cond := range f.lastMust {
		var key string
		_ = json.Unmarshal(cond["key"], &key)
		if key != "cache_scope" {
			continue
		}
		var m struct {
			Value string `json:"value"`
		}
		_ = json.Unmarshal(cond["match"], &m)
		found = m.Value != ""
	}
	if !found {
		t.Fatalf("qdrant search filter has no cache_scope match: %v", f.lastMust)
	}
	var indexed bool
	for _, ix := range f.indexes {
		indexed = indexed || ix == "cache_scope:keyword"
	}
	if !indexed {
		t.Errorf("qdrant backend did not request a keyword index on cache_scope (got %v)", f.indexes)
	}
}

func TestPineconeQuerySendsScopeFilter(t *testing.T) {
	f := newFakePinecone(t)
	b, err := cache.NewPineconeBackend(f.srv.URL, "test-key", testThreshold, testDims, 2*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	p := newSemanticPlugin(b)
	prime(t, p, orgA, "gpt-4o", franceQ, "org-a")
	ask(p, orgA, "gpt-4o", franceQNear)

	f.mu.Lock()
	defer f.mu.Unlock()
	cond, ok := f.lastFilter["cache_scope"].(map[string]any)
	if !ok || cond["$eq"] == nil || cond["$eq"] == "" {
		t.Fatalf("pinecone query filter has no cache_scope $eq: %v", f.lastFilter)
	}
}

// model is caller input that Weaviate's backend puts inside a GraphQL query.
// The backend escapes it, so the model name is always one literal and the
// where clause always holds the scope filter.
func TestWeaviateSearchSendsScopeFilterAndEscapesModel(t *testing.T) {
	f := newFakeWeaviate(t, "AgentccSemanticCache")
	b, err := cache.NewWeaviateBackend(f.srv.URL, "", "", testThreshold, testDims, 2*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	f.mu.Lock()
	prop, ok := f.properties["cache_scope"]
	f.mu.Unlock()
	if !ok || prop["tokenization"] != "field" {
		t.Errorf("weaviate class has no field-tokenized cache_scope property: %v", prop)
	}

	p := newSemanticPlugin(b)
	prime(t, p, orgA, "gpt-4o", franceQ, "org-a-secret")

	injected := `gpt-4o"}, {path: ["model_name"], operator: Equal, valueText: "gpt-4o`
	if status, got := ask(p, orgB, injected, franceQNear); got != "" {
		t.Fatalf("org B with an injected model name was served %q (status %q)", got, status)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	var scopeOp, modelOp *weaviateOperand
	for i := range f.lastOperands {
		switch f.lastOperands[i].Path {
		case "cache_scope":
			scopeOp = &f.lastOperands[i]
		case "model_name":
			modelOp = &f.lastOperands[i]
		}
	}
	if scopeOp == nil || scopeOp.Text == nil || *scopeOp.Text == "" {
		t.Fatalf("weaviate where clause has no cache_scope operand: %+v", f.lastOperands)
	}
	if modelOp == nil || modelOp.Text == nil || *modelOp.Text != injected {
		t.Fatalf("model name did not round-trip as one literal: %+v", f.lastOperands)
	}
	if len(f.lastOperands) != 3 {
		t.Fatalf("where clause has %d operands, want 3 (scope, model, expiry): %+v", len(f.lastOperands), f.lastOperands)
	}
}

// A class without the scope property gets it added on start-up.
func TestWeaviateAddsScopePropertyToExistingClass(t *testing.T) {
	f := newFakeWeaviate(t, "AgentccSemanticCache")
	f.mu.Lock()
	f.classMade = true
	f.properties["cache_key"] = map[string]any{"name": "cache_key", "dataType": []any{"text"}}
	f.mu.Unlock()
	if _, err := cache.NewWeaviateBackend(f.srv.URL, "", "", testThreshold, testDims, 2*time.Second); err != nil {
		t.Fatal(err)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if prop, ok := f.properties["cache_scope"]; !ok || prop["tokenization"] != "field" {
		t.Fatalf("existing class was not given a field-tokenized cache_scope property: %v", f.properties)
	}
}
