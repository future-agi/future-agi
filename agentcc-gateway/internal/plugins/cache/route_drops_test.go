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
// backends do, so the mark must survive a marshal/unmarshal round trip.
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

// carriageCall is one request through the plugin. stop is the caller's stop
// setting, which the cache key does not cover; translatedFrom marks a
// translated route; translator holds that route's own notes.
type carriageCall struct {
	stop           string
	translatedFrom string
	translator     []string
}

// throughCache runs call through the plugin: a lookup, and on a miss resp is
// stored as a provider attempt leaves it, over the Responses route when
// carriage is set (with that attempt's notes for its request).
func throughCache(t *testing.T, p *Plugin, call carriageCall, resp *models.ChatCompletionResponse, carriage bool) *models.RequestContext {
	t.Helper()
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.Request = makeReq("gpt-5.6-terra", "weather in Paris")
	seed := 7
	rc.Request.Seed = &seed
	if call.stop != "" {
		rc.Request.Stop = json.RawMessage(`["` + call.stop + `"]`)
	}
	rc.Request.TranslatedFrom = call.translatedFrom
	rc.Model = "gpt-5.6-terra"
	rc.TranslationDrops = call.translator
	rc.SetResponsesRouteDrops(nil)
	p.ProcessRequest(context.Background(), rc)
	if rc.Flags.CacheHit {
		return rc
	}
	rc.Response = resp
	rc.ResponsesCarriage = carriage
	if carriage {
		rc.SetResponsesRouteDrops(models.ResponsesRouteNotes(rc.Request))
	}
	p.ProcessResponse(context.Background(), rc)
	return rc
}

func respWithExtra(id string, extra map[string]json.RawMessage) *models.ChatCompletionResponse {
	resp := makeResp(id)
	resp.Extra = extra
	return resp
}

func assertNoCarriageField(t *testing.T, label string, resp *models.ChatCompletionResponse) {
	t.Helper()
	if resp == nil {
		t.Fatalf("%s: no response", label)
	}
	if _, ok := resp.Extra[carriageField]; ok {
		t.Errorf("%s: response carries %s in Extra", label, carriageField)
	}
	body, err := json.Marshal(resp)
	if err != nil {
		t.Fatalf("%s: marshal: %v", label, err)
	}
	if strings.Contains(string(body), carriageField) {
		t.Errorf("%s: client body contains %s: %s", label, carriageField, body)
	}
}

func wantHitNotes(t *testing.T, label string, rc *models.RequestContext, want string) {
	t.Helper()
	if !rc.Flags.CacheHit || rc.Metadata["cache_status"] != "hit_exact" {
		t.Fatalf("%s: cache_status=%q, want hit_exact", label, rc.Metadata["cache_status"])
	}
	if got := rc.Metadata["translation_drops"]; got != want {
		t.Errorf("%s: translation_drops=%q, want %q", label, got, want)
	}
	assertNoCarriageField(t, label, rc.Response)
}

func TestCarriageRestoredOnCacheHit(t *testing.T) {
	backends := map[string]func() cache.Backend{
		"memory": func() cache.Backend { return cache.NewMemoryBackend(cache.NewStore(10)) },
		"json":   func() cache.Backend { return newJSONBackend() },
	}
	for name, backend := range backends {
		t.Run(name, func(t *testing.T) {
			p := New(backend(), time.Minute)
			upstreamExtra := map[string]json.RawMessage{"citations": json.RawMessage(`["https://example.test"]`)}
			first := respWithExtra("resp-1", upstreamExtra)

			rc1 := throughCache(t, p, carriageCall{}, first, true)
			if rc1.Flags.CacheHit {
				t.Fatal("first request was a cache hit")
			}
			assertNoCarriageField(t, "miss", rc1.Response)
			if len(first.Extra) != 1 {
				t.Errorf("stored copy changed the served response's Extra: %v", first.Extra)
			}

			// Request 3 checks the stored entry is still marked after request
			// 2 read and stripped its own copy.
			for _, label := range []string{"request 2", "request 3"} {
				rc := throughCache(t, p, carriageCall{}, makeResp("never-stored"), false)
				wantHitNotes(t, label, rc, "seed_unsupported")
				if !rc.ResponsesCarriage {
					t.Errorf("%s: carriage not restored", label)
				}
				if string(rc.Response.Extra["citations"]) != `["https://example.test"]` {
					t.Errorf("%s: upstream extra field lost: %v", label, rc.Response.Extra)
				}
			}
		})
	}
}

// stop is not part of the cache key, so requests that differ only in stop
// share an entry. The notes follow the request being answered.
func TestCarriageNotesFollowTheCurrentRequest(t *testing.T) {
	t.Run("entry without stop, hit with stop", func(t *testing.T) {
		p := New(newJSONBackend(), time.Minute)
		throughCache(t, p, carriageCall{}, makeResp("resp-1"), true)
		rc := throughCache(t, p, carriageCall{stop: "END"}, makeResp("never-stored"), false)
		wantHitNotes(t, "with stop", rc, "stop_unsupported,seed_unsupported")
	})
	t.Run("entry with stop, hit without stop", func(t *testing.T) {
		p := New(newJSONBackend(), time.Minute)
		first := throughCache(t, p, carriageCall{stop: "END"}, makeResp("resp-1"), true)
		if got := first.Metadata["translation_drops"]; got != "stop_unsupported,seed_unsupported" {
			t.Fatalf("producing request notes=%q", got)
		}
		rc := throughCache(t, p, carriageCall{}, makeResp("never-stored"), false)
		wantHitNotes(t, "without stop", rc, "seed_unsupported")
	})
	t.Run("translated route names", func(t *testing.T) {
		p := New(newJSONBackend(), time.Minute)
		throughCache(t, p, carriageCall{}, makeResp("resp-1"), true)
		rc := throughCache(t, p, carriageCall{stop: "END", translatedFrom: "anthropic", translator: []string{"top_k_unsupported"}}, makeResp("never-stored"), false)
		wantHitNotes(t, "translated", rc, "top_k_unsupported,stop,seed")
	})
}

// An entry produced over ordinary chat completions omitted nothing, so a hit
// reports nothing even for a request whose settings that route would omit.
func TestCarriageAbsentReportsNothing(t *testing.T) {
	p := New(newJSONBackend(), time.Minute)
	throughCache(t, p, carriageCall{}, makeResp("resp-1"), false)
	rc := throughCache(t, p, carriageCall{stop: "END"}, makeResp("never-stored"), false)
	if !rc.Flags.CacheHit {
		t.Fatal("second request missed the cache")
	}
	if got, ok := rc.Metadata["translation_drops"]; ok {
		t.Errorf("hit of an ordinary chat entry reported %q", got)
	}
	if rc.ResponsesCarriage {
		t.Error("carriage set for an ordinary chat entry")
	}
	assertNoCarriageField(t, "hit", rc.Response)
}

// An upstream body that happens to use the reserved key is not the gateway's
// mark: the cache rewrites the field from the attempt's own route.
func TestCarriageUpstreamFieldIsRewritten(t *testing.T) {
	p := New(newJSONBackend(), time.Minute)
	forged := respWithExtra("resp-1", map[string]json.RawMessage{carriageField: json.RawMessage(`true`)})
	throughCache(t, p, carriageCall{}, forged, false)

	rc := throughCache(t, p, carriageCall{stop: "END"}, makeResp("never-stored"), false)
	if !rc.Flags.CacheHit {
		t.Fatal("second request missed the cache")
	}
	if got, ok := rc.Metadata["translation_drops"]; ok {
		t.Errorf("upstream field reported as notes: %q", got)
	}
	assertNoCarriageField(t, "hit", rc.Response)
}
