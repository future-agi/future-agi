package cache

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

func logprobsRequest(t *testing.T, native bool, count *int) *models.RequestContext {
	t.Helper()
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.Request = makeReq("gpt-4o", "hello")
	rc.Model = rc.Request.Model
	if native {
		rc.EndpointType = "completion"
		rc.Metadata["endpoint_format"] = "completions"
		if count != nil {
			rc.Metadata["cache_native_logprobs"] = fmt.Sprint(*count)
		}
	} else if count != nil {
		enabled := true
		rc.Request.Logprobs = &enabled
		rc.Request.TopLogprobs = count
	}
	return rc
}

func TestCacheLogprobsPartitions(t *testing.T) {
	for _, custom := range []bool{false, true} {
		t.Run(fmt.Sprintf("custom=%t", custom), func(t *testing.T) {
			p, _ := newTestPlugin()
			seen := make(map[string]string)
			for _, native := range []bool{false, true} {
				for _, count := range []int{0, 1, 2} {
					rc := logprobsRequest(t, native, &count)
					if custom {
						rc.RequestHeaders = http.Header{http.CanonicalHeaderKey(HeaderCacheKey): {"same-answer"}}
					}
					p.ProcessRequest(context.Background(), rc)
					key := rc.Metadata["cache_key"]
					name := fmt.Sprintf("native=%t/count=%d", native, count)
					if other, exists := seen[key]; exists {
						t.Errorf("logprobs key collision: %s and %s", name, other)
					}
					seen[key] = name
					// Old entries contain no route/option provenance. Never reuse
					// one for a caller who requested logprobs after the upgrade.
					old := BuildCacheKey(cache.NoOrgScope, "default", rc.Request)
					if custom {
						old = buildCustomCacheKey(cache.NoOrgScope, "default", "same-answer")
					}
					if key == old {
						t.Error("logprobs request reused the old unqualified key")
					}
				}
			}
		})
	}
}

func TestCacheLogprobsUnrequestedRead(t *testing.T) {
	for _, semantic := range []bool{false, true} {
		for _, native := range []bool{false, true} {
			t.Run(fmt.Sprintf("semantic=%t/native=%t", semantic, native), func(t *testing.T) {
				p, store := newTestPlugin()
				rc := logprobsRequest(t, native, nil)
				key := BuildCacheKey(cache.NoOrgScope, "default", rc.Request)
				response := makeResp("pre-upgrade")
				raw := json.RawMessage(`{"tokens":["hello"],"token_logprobs":[-0.25],"top_logprobs":[{}],"text_offset":[0]}`)
				response.Choices[0].Logprobs = &raw
				if semantic {
					fuzzy := cache.NewSemanticStore(0.8, 256, 10)
					p.SetSemanticStoreFromConcrete(fuzzy)
					fuzzy.Set(cache.NoOrgScope, key, cache.Vectorize("hello", 256), rc.Model, response, time.Minute)
				} else {
					store.Set(key, response, time.Minute)
				}
				result := p.ProcessRequest(context.Background(), rc)
				if result.Action != pipeline.ShortCircuit || rc.Response == nil {
					t.Fatal("unrequested logprobs should not prevent text reuse")
				}
				if rc.Response.Choices[0].Logprobs != nil || result.Response.Choices[0].Logprobs != nil {
					t.Error("cached answer exposed unrequested protocol-specific logprobs")
				}
				if response.Choices[0].Logprobs == nil {
					t.Error("sanitizing a hit mutated the stored answer")
				}
			})
		}
	}
}

func TestCacheLogprobsUnrequestedWrite(t *testing.T) {
	p, store := newTestPlugin()
	rc := logprobsRequest(t, false, nil)
	p.ProcessRequest(context.Background(), rc)
	rc.Response = makeResp("unsolicited")
	raw := json.RawMessage(`{"content":[]}`)
	rc.Response.Choices[0].Logprobs = &raw
	p.ProcessResponse(context.Background(), rc)
	cached, ok := store.Get(rc.Metadata["cache_key"])
	if !ok || cached.Choices[0].Logprobs != nil {
		t.Error("unrequested logprobs stored in the shared route-neutral entry")
	}
	if rc.Response.Choices[0].Logprobs == nil {
		t.Error("cache storage mutated the live provider response")
	}
}

func TestCacheLogprobsSkipSemantic(t *testing.T) {
	for _, native := range []bool{false, true} {
		t.Run(fmt.Sprintf("native=%t", native), func(t *testing.T) {
			p, _ := newTestPlugin()
			fuzzy := cache.NewSemanticStore(0.8, 256, 10)
			p.SetSemanticStoreFromConcrete(fuzzy)
			fuzzy.Set(cache.NoOrgScope, "prior", cache.Vectorize("hello", 256), "gpt-4o", makeResp("wrong-shape"), time.Minute)
			count := 1
			rc := logprobsRequest(t, native, &count)
			result := p.ProcessRequest(context.Background(), rc)
			if result.Action != pipeline.Continue || rc.Metadata["cache_status"] != "miss" {
				t.Error("logprobs request consumed an unqualified semantic answer")
			}
			// Prove the write policy independently of whether the read failed.
			fresh := logprobsRequest(t, native, &count)
			fresh.Metadata["cache_force_refresh"] = "true"
			p.ProcessRequest(context.Background(), fresh)
			fresh.Response = makeResp("fresh")
			p.ProcessResponse(context.Background(), fresh)
			if fuzzy.Len() != 1 {
				t.Error("protocol-specific logprobs request polluted the semantic store")
			}
		})
	}
}

func TestCacheLogprobsFalseKeepsSharedKey(t *testing.T) {
	p, _ := newTestPlugin()
	rc := logprobsRequest(t, false, nil)
	disabled, ignoredTop := false, 2
	rc.Request.Logprobs, rc.Request.TopLogprobs = &disabled, &ignoredTop
	p.ProcessRequest(context.Background(), rc)
	if rc.Metadata["cache_key"] != BuildCacheKey(cache.NoOrgScope, "default", rc.Request) {
		t.Error("logprobs=false changed the existing route-neutral key")
	}
}
