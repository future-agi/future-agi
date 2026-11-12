package server

import (
	"net/http"
	"strings"
	"testing"
	"time"
)

// A streamGenerateContent request served through translation (a provider
// that does not speak the GenAI API) ends when the upstream stream ends. It
// stayed open until the request's deadline: once every channel had closed,
// the handler waited on them forever, and its post-plugins ran only when the
// deadline cancelled the request. The fixture's requests have a 5 s deadline.
func TestGenAIStreamViaCanonical_EndsWhenTheUpstreamEnds(t *testing.T) {
	g := newPostBlockGateway(t, postBlockSetup{})
	start := time.Now()
	code, body := g.do(t, "/v1beta/models/gpt-4o:streamGenerateContent", genaiBody())
	elapsed := time.Since(start)
	if code != http.StatusOK || !strings.Contains(body, "DROP TABLE") {
		t.Fatalf("status %d, body %s; want 200 with the streamed answer", code, body)
	}
	if elapsed > 2*time.Second {
		t.Errorf("the stream ended after %v, want it to end with the upstream's (the request deadline is 5s)", elapsed)
	}
}
