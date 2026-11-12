package server

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// Signal only after the created event reaches the caller. The upstream remains
// paused, so the test can delete the registered ID before any later event.
// The recorder is read by the test only after the stream handler has returned.
type responseCreatedRecorder struct {
	*httptest.ResponseRecorder
	created chan struct{}
	once    sync.Once
}

func (w *responseCreatedRecorder) Flush() {
	w.ResponseRecorder.Flush()
	if strings.Contains(w.Body.String(), "event: response.created\n") && strings.Contains(w.Body.String(), "\n\n") {
		w.once.Do(func() { close(w.created) })
	}
}

func TestResponsesStream_DeleteDuringStreamCannotRestoreAuthority(t *testing.T) {
	for _, deleted := range []bool{false, true} {
		t.Run(fmt.Sprintf("deleted=%t", deleted), func(t *testing.T) {
			resume := make(chan struct{})
			release := sync.OnceFunc(func() { close(resume) })
			srv, calls := newContinuationGatewayWithStreamHook(t, true, func(w http.ResponseWriter, id string) {
				<-resume
				fmt.Fprintf(w, "event: response.in_progress\ndata: {\"type\":\"response.in_progress\",\"response\":{\"id\":%q,\"status\":\"in_progress\"}}\n\n", id)
				w.(http.Flusher).Flush()
			})
			// Unblock the upstream before the fixture's httptest server closes,
			// including when an assertion fails before the stream has completed.
			defer release()
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			req := httptest.NewRequest(http.MethodPost, "/v1/responses", strings.NewReader(`{"model":"gpt-4o","input":"private","stream":true,"store":true}`)).WithContext(ctx)
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("Authorization", "Bearer "+objKeyA)
			w := &responseCreatedRecorder{ResponseRecorder: httptest.NewRecorder(), created: make(chan struct{})}
			done := make(chan struct{})
			go func() {
				srv.httpServer.Handler.ServeHTTP(w, req)
				close(done)
			}()
			defer func() {
				release()
				cancel()
				select {
				case <-done:
				case <-time.After(5 * time.Second):
					t.Error("stream handler did not exit during cleanup")
				}
			}()
			select {
			case <-w.created:
			case <-done:
				t.Fatalf("stream ended before response.created: %d %s", w.Code, w.Body.String())
			case <-ctx.Done():
				t.Fatal("timed out waiting for response.created")
			}

			const id = "resp_cont_1"
			path := "/v1/responses/" + id
			if got := objCall(t, srv, http.MethodGet, path, objKeyA, "", nil); got.Code != http.StatusNotFound {
				t.Fatalf("unfinished stream must be unreadable: %d %s", got.Code, got.Body.String())
			}
			if deleted {
				got := objCall(t, srv, http.MethodDelete, path, objKeyA, "", nil)
				if got.Code != http.StatusOK || mediaDecode(t, got)["deleted"] != true {
					t.Fatalf("DELETE in-flight response: %d %s", got.Code, got.Body.String())
				}
				before := calls.Load()
				got = objCall(t, srv, http.MethodPost, "/v1/responses", objKeyA, continuationBody(id, false), nil)
				if got.Code != http.StatusNotFound || calls.Load() != before {
					t.Fatalf("DELETE did not immediately revoke continuation: %d %s", got.Code, got.Body.String())
				}
			}

			release()
			select {
			case <-done:
			case <-ctx.Done():
				t.Fatal("stream failed to finish after release")
			}
			if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), "response.in_progress") || !strings.Contains(w.Body.String(), "response.completed") {
				t.Fatalf("stream did not relay both late lifecycle events: %d %s", w.Code, w.Body.String())
			}
			want := http.StatusOK
			if deleted {
				want = http.StatusNotFound
			}
			if got := objCall(t, srv, http.MethodGet, path, objKeyA, "", nil); got.Code != want {
				t.Errorf("GET after stream completion: %d %s, want %d", got.Code, got.Body.String(), want)
			}
			for _, stream := range []bool{false, true} {
				before := calls.Load()
				got := objCall(t, srv, http.MethodPost, "/v1/responses", objKeyA, continuationBody(id, stream), nil)
				wantCalls := before + 1
				if deleted {
					wantCalls = before
				}
				if got.Code != want || calls.Load() != wantCalls {
					t.Errorf("continuation stream=%t after completion: %d %s, calls=%d; want status=%d calls=%d", stream, got.Code, got.Body.String(), calls.Load(), want, wantCalls)
				}
			}
			if deleted {
				if got := objCall(t, srv, http.MethodDelete, path, objKeyA, "", nil); got.Code != http.StatusNotFound {
					t.Errorf("repeated DELETE after completion: %d, want 404", got.Code)
				}
			}
		})
	}
}
