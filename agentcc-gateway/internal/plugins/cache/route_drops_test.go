package cache

import (
	"context"
	"encoding/json"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// jsonBackend stores responses as JSON, as the Redis, disk and object-store
// backends do, so the notes must survive a marshal/unmarshal round trip.
type jsonBackend struct {
	mu      sync.Mutex
	entries map[string][]byte
}

func newJSONBackend() *jsonBackend { return &jsonBackend{entries: map[string][]byte{}} }

func (b *jsonBackend) Get(key string) (*models.ChatCompletionResponse, bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	data, ok := b.entries[key]
	if !ok {
		return nil, false
	}
	var resp models.ChatCompletionResponse
	if err := json.Unmarshal(data, &resp); err != nil {
		return nil, false
	}
	return &resp, true
}

func (b *jsonBackend) Set(key string, resp *models.ChatCompletionResponse, _ time.Duration) {
	data, err := json.Marshal(resp)
	if err != nil {
		return
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	b.entries[key] = data
}

func (b *jsonBackend) Delete(key string) {
	b.mu.Lock()
	defer b.mu.Unlock()
	delete(b.entries, key)
}

// cacheRoundTrip runs one request through the plugin: a lookup, and on a miss
// the given response and route notes are stored as a provider attempt would
// leave them.
func cacheRoundTrip(t *testing.T, p *Plugin, translator []string, resp *models.ChatCompletionResponse, route []string) *models.RequestContext {
	t.Helper()
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.Request = makeReq("gpt-5.6-terra", "weather in Paris")
	seed := 7
	rc.Request.Seed = &seed
	rc.Model = "gpt-5.6-terra"
	rc.TranslationDrops = translator
	if len(translator) > 0 {
		rc.SetResponsesRouteDrops(nil)
	}
	p.ProcessRequest(context.Background(), rc)
	if rc.Flags.CacheHit {
		return rc
	}
	rc.Response = resp
	rc.SetResponsesRouteDrops(route)
	p.ProcessResponse(context.Background(), rc)
	return rc
}

func respWithExtra(id string, extra map[string]json.RawMessage) *models.ChatCompletionResponse {
	resp := makeResp(id)
	resp.Extra = extra
	return resp
}

func assertNoReservedField(t *testing.T, label string, resp *models.ChatCompletionResponse) {
	t.Helper()
	if resp == nil {
		t.Fatalf("%s: no response", label)
	}
	if _, ok := resp.Extra[routeDropsField]; ok {
		t.Errorf("%s: response carries %s in Extra", label, routeDropsField)
	}
	body, err := json.Marshal(resp)
	if err != nil {
		t.Fatalf("%s: marshal: %v", label, err)
	}
	if strings.Contains(string(body), routeDropsField) {
		t.Errorf("%s: client body contains %s: %s", label, routeDropsField, body)
	}
}

func TestRouteDropsRestoredOnCacheHit(t *testing.T) {
	backends := map[string]func() cache.Backend{
		"memory": func() cache.Backend { return cache.NewMemoryBackend(cache.NewStore(10)) },
		"json":   func() cache.Backend { return newJSONBackend() },
	}
	for name, backend := range backends {
		t.Run(name, func(t *testing.T) {
			p := New(backend(), time.Minute)
			upstreamExtra := map[string]json.RawMessage{"citations": json.RawMessage(`["https://example.test"]`)}
			first := respWithExtra("resp-1", upstreamExtra)

			rc1 := cacheRoundTrip(t, p, nil, first, []string{"seed_unsupported"})
			if rc1.Flags.CacheHit {
				t.Fatal("first request was a cache hit")
			}
			assertNoReservedField(t, "miss", rc1.Response)
			if len(first.Extra) != 1 {
				t.Errorf("stored copy changed the served response's Extra: %v", first.Extra)
			}

			// Request 3 checks the stored entry still carries the notes after
			// request 2 read and stripped them from its own copy.
			for i, label := range []string{"request 2", "request 3"} {
				rc := cacheRoundTrip(t, p, nil, makeResp("never-stored"), nil)
				if !rc.Flags.CacheHit || rc.Metadata["cache_status"] != "hit_exact" {
					t.Fatalf("%s: cache_status=%q, want hit_exact", label, rc.Metadata["cache_status"])
				}
				if got := rc.Metadata["translation_drops"]; got != "seed_unsupported" {
					t.Errorf("%s: translation_drops=%q, want seed_unsupported", label, got)
				}
				if got := strings.Join(rc.ResponsesRouteDrops, ","); got != "seed_unsupported" {
					t.Errorf("%s: route notes=%q", label, got)
				}
				assertNoReservedField(t, label, rc.Response)
				if string(rc.Response.Extra["citations"]) != `["https://example.test"]` {
					t.Errorf("%s (%d): upstream extra field lost: %v", label, i, rc.Response.Extra)
				}
			}
		})
	}
}

func TestRouteDropsCacheHitWithoutNotes(t *testing.T) {
	p := New(newJSONBackend(), time.Minute)
	// An upstream body that happens to use the reserved key is not a source
	// of gateway notes: the cache rewrites the field from the attempt's notes.
	forged := respWithExtra("resp-1", map[string]json.RawMessage{routeDropsField: json.RawMessage(`["stop_unsupported"]`)})
	cacheRoundTrip(t, p, nil, forged, nil)

	rc := cacheRoundTrip(t, p, nil, makeResp("never-stored"), nil)
	if !rc.Flags.CacheHit {
		t.Fatal("second request missed the cache")
	}
	if got, ok := rc.Metadata["translation_drops"]; ok {
		t.Errorf("hit without stored notes reported %q", got)
	}
	assertNoReservedField(t, "hit", rc.Response)
}

func TestRouteDropsRestoreKeepsOnlyKnownNames(t *testing.T) {
	backend := newJSONBackend()
	p := New(backend, time.Minute)
	cacheRoundTrip(t, p, nil, makeResp("resp-1"), []string{"seed_unsupported"})

	// Simulate an altered entry: only the fixed vocabulary is restored.
	backend.mu.Lock()
	for key, data := range backend.entries {
		var raw map[string]json.RawMessage
		if err := json.Unmarshal(data, &raw); err != nil {
			t.Fatal(err)
		}
		raw[routeDropsField] = json.RawMessage(`["forged\r\nx-fake: yes","seed_unsupported","stop"]`)
		backend.entries[key], _ = json.Marshal(raw)
	}
	backend.mu.Unlock()

	rc := cacheRoundTrip(t, p, nil, makeResp("never-stored"), nil)
	if got := rc.Metadata["translation_drops"]; got != "seed_unsupported,stop" {
		t.Errorf("translation_drops=%q, want only known names", got)
	}
	assertNoReservedField(t, "hit", rc.Response)
}

// Translator notes are not part of the cache key, so they are never stored:
// a hit combines the current request's translator notes with the stored
// route notes.
func TestRouteDropsMergeWithCurrentTranslatorNotes(t *testing.T) {
	p := New(newJSONBackend(), time.Minute)
	cacheRoundTrip(t, p, []string{"top_k_unsupported"}, makeResp("resp-1"), []string{"seed_unsupported"})

	plain := cacheRoundTrip(t, p, nil, makeResp("never-stored"), nil)
	if got := plain.Metadata["translation_drops"]; got != "seed_unsupported" {
		t.Errorf("plain caller got %q, want the route note only", got)
	}
	translated := cacheRoundTrip(t, p, []string{"candidate_count_unsupported"}, makeResp("never-stored"), nil)
	if got := translated.Metadata["translation_drops"]; got != "candidate_count_unsupported,seed_unsupported" {
		t.Errorf("translated caller got %q", got)
	}
}
