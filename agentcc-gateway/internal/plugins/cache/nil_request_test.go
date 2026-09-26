package cache

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The dialect pass-through handlers forward the caller's raw bytes and never
// build a canonical request, so rc.Request stays nil and the cache has to skip.
//
// Handing it a carrier with empty Messages instead would be worse than useless:
// BuildCacheKey marshals Messages, so every prompt for a model would hash to
// one key, and the response stored against it holds usage only — the next
// request would be served a content-less 200 without the provider being called.
func TestProcessRequestSkipsWhenRequestIsNil(t *testing.T) {
	p := &Plugin{}
	rc := &models.RequestContext{Metadata: map[string]string{}}

	result := p.ProcessRequest(context.Background(), rc)

	if result.Action == 1 { // ShortCircuit
		t.Fatal("cache served a response for a request it cannot key")
	}
	if got := rc.Metadata["cache_status"]; got != "skip" {
		t.Errorf("cache_status = %q, want \"skip\"", got)
	}
}

// The collision that makes the nil skip load-bearing.
func TestBuildCacheKeyCollidesOnEmptyMessages(t *testing.T) {
	a := BuildCacheKey("default", &models.ChatCompletionRequest{Model: "claude-3"})
	b := BuildCacheKey("default", &models.ChatCompletionRequest{Model: "claude-3"})

	if a != b {
		t.Fatal("expected two message-less requests to collide; if they no longer " +
			"do, the pass-through paths could safely carry a minimal request")
	}
}
