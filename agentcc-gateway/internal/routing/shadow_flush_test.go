package routing

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// A webhook that never answers cannot hold Close past its deadline, and Close
// ends Run.
func TestShadowFlusherClose_IsBoundedWhenTheWebhookHangs(t *testing.T) {
	released := make(chan struct{})
	webhook := httptest.NewServer(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		select {
		case <-r.Context().Done():
		case <-released:
		}
	}))
	defer webhook.Close()
	defer close(released) // before webhook.Close, which waits for the handler
	store := NewShadowStore(10)
	store.Add(ShadowResult{RequestID: "req-1"})
	f := NewShadowFlusher(store, webhook.URL, "secret", time.Hour)
	stopped := make(chan struct{})
	go func() {
		defer close(stopped)
		f.Run(context.Background())
	}()

	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	start := time.Now()
	f.Close(ctx)
	if elapsed := time.Since(start); elapsed > 2*time.Second {
		t.Errorf("Close took %s with a 200ms deadline", elapsed)
	}
	select {
	case <-stopped:
	case <-time.After(time.Second):
		t.Error("Run did not return after Close")
	}
}
