package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/oklog/ulid/v2"
)

// Work the gateway runs without an HTTP request of its own (an A2A task, a
// /-/batches item, a scheduled job) takes its request and trace ids from
// NewID. They must come from the source the RequestID middleware gives every
// request its ids from: one monotonic reader over crypto/rand. With the clock
// stopped every id carries one timestamp, and ids drawn from one monotonic
// reader rise strictly in the order they are made. Ids from a second source
// would land at random among the middleware's.
func TestNewIDDrawsFromTheRequestIDSource(t *testing.T) {
	prev := now
	t.Cleanup(func() { now = prev })
	at := time.UnixMilli(1760000000500)
	now = func() time.Time { return at }

	handler := RequestID(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	var ids []string
	for i := 0; i < 32; i++ {
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/", nil))
		ids = append(ids, w.Header().Get("x-agentcc-request-id"), w.Header().Get("x-agentcc-trace-id"), NewID())
	}
	for i, id := range ids {
		parsed, err := ulid.ParseStrict(id)
		if err != nil {
			t.Fatalf("id %d %q is not a ULID: %v", i, id, err)
		}
		if parsed.Time() != ulid.Timestamp(at) {
			t.Fatalf("id %d %s is stamped %d, not the stopped clock's %d", i, id, parsed.Time(), ulid.Timestamp(at))
		}
		if i > 0 && id <= ids[i-1] {
			t.Fatalf("id %d %s does not follow id %d %s: NewID and the RequestID middleware do not draw from one source", i, id, i-1, ids[i-1])
		}
	}
}
