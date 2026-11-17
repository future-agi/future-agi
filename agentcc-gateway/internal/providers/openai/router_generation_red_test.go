package openai

import "testing"

// This behavioral regression compiles at the parent commit: the pre-existing
// StreamChunk fields must survive an error event, even with no token report.
func TestRouterErrorEventRetainsIdentity(t *testing.T) {
	failure, chunk := streamErrorEnvelope([]byte(`{"error":{"message":"synthetic failure","code":502},"id":"gen-error","model":"test/served","provider":"Synthetic"}`))
	if failure == nil || chunk.ID != "gen-error" || chunk.Model != "test/served" || chunk.UpstreamProvider != "Synthetic" {
		t.Fatalf("error generation evidence lost: failure=%v chunk=%+v", failure, chunk)
	}
}
