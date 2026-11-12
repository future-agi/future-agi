package cache

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A response carrying Gemini Grounded Results is
// never stored; any other response still is.
func TestGroundedResponseNeverCached(t *testing.T) {
	p, store := newTestPlugin()
	for _, tc := range []struct {
		name   string
		usage  *models.ServerToolUsage
		stored bool
	}{
		{"grounded search", &models.ServerToolUsage{WebSearchQueries: 2, GroundedPrompts: 1}, false},
		{"maps grounded", &models.ServerToolUsage{MapsGroundedPrompts: 1}, false},
		{"anthropic web search", &models.ServerToolUsage{WebSearchRequests: 1}, true},
		{"no server tools", nil, true},
	} {
		rc := models.AcquireRequestContext()
		rc.Request = makeReq("gemini-3-pro", "q "+tc.name)
		rc.Model = "gemini-3-pro"
		p.ProcessRequest(context.Background(), rc)
		key := rc.Metadata["cache_key"]
		if key == "" {
			t.Fatalf("%s: no cache key", tc.name)
		}
		rc.Response = makeResp("r-" + tc.name)
		rc.Response.Usage = &models.Usage{PromptTokens: 1, CompletionTokens: 1, ServerToolUse: tc.usage}
		p.ProcessResponse(context.Background(), rc)
		if _, ok := store.Get(key); ok != tc.stored {
			t.Errorf("%s: stored = %v, want %v", tc.name, ok, tc.stored)
		}
		rc.Release()
	}
}
