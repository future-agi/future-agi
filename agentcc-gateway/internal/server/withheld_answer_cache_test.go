package server

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/cache"
)

// answer_withheld marks only a provider's answer that an output check
// refused. An answer the gateway gave itself before any provider call (from
// the response cache, or another pre-plugin) that the check then refuses is
// logged as a guardrail block with no such mark: no provider answered it.

func TestACachedAnswerAnOutputCheckRefusesIsNotMarkedWithheld(t *testing.T) {
	store := cache.NewStore(100)
	// A gateway with no output check serves the answer and caches it, as an
	// entry stored while the answer passed is (before the check was on).
	served := newPostBlockGateway(t, postBlockSetup{priced: true, cache: store})
	if code, body := served.chat(t, false); code != http.StatusOK {
		t.Fatalf("priming request: status %d, body %s; want 200", code, body)
	}
	if store.Len() != 1 {
		t.Fatalf("the served answer was not cached: %d entries", store.Len())
	}
	g := newPostBlockGateway(t, postBlockSetup{staticRule: answerBlockRule, priced: true, cache: store})
	code, body := g.chat(t, false)
	assertBlockAnswered(t, code, body, answerBlockRule)
	e := g.entry(t)
	if hit, _ := e["cache_hit"].(bool); !hit {
		t.Fatalf("the second request was not a cache hit: %v", e)
	}
	assertBlockLogged(t, e, answerBlockRule)
	if got, ok := loggedMetadata(e)["answer_withheld"]; ok {
		t.Errorf("a cached answer the check refused is marked answer_withheld = %v, want no mark", got)
	}
}

// The same for an answer a pre-plugin gave before the provider (as a cache
// hit does), for a plain and for a streamed request.
func TestAPreAnsweredRequestTheCheckRefusesIsNotMarkedWithheld(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(streamLabel(stream), func(t *testing.T) {
			g := newPostBlockGateway(t, postBlockSetup{staticRule: answerBlockRule, preAnswer: true, priced: true})
			code, body := g.chat(t, stream)
			assertBlockAnswered(t, code, body, answerBlockRule)
			e := g.entry(t)
			assertBlockLogged(t, e, answerBlockRule)
			if got, ok := loggedMetadata(e)["answer_withheld"]; ok {
				t.Errorf("an answer no provider gave is marked answer_withheld = %v, want no mark", got)
			}
		})
	}
}
