package server

import (
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
)

// Signal only when real Messages content has reached the client writer. The
// upstream fails after that signal, not after a timing-dependent sleep.
type messagesRoutingWriter struct {
	*httptest.ResponseRecorder
	once   sync.Once
	output chan struct{}
}

func (w *messagesRoutingWriter) Write(b []byte) (int, error) {
	n, err := w.ResponseRecorder.Write(b)
	if strings.Contains(string(b), "partial answer") {
		w.once.Do(func() { close(w.output) })
	}
	return n, err
}

func TestMessagesRoutingNoFallbackAfterOutput(t *testing.T) {
	output := make(chan struct{})
	var calls atomic.Int32
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
			io.WriteString(w, `{"object":"list","data":[]}`)
			return
		}
		calls.Add(1)
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, "data: {\"id\":\"c1\",\"model\":\"primary-model\",\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"partial answer\"}}]}\n\n")
		w.(http.Flusher).Flush()
		select {
		case <-output:
			io.WriteString(w, "data: {\"error\":{\"message\":\"synthetic failure after output\",\"code\":502}}\n\n")
		case <-r.Context().Done():
		}
	}))
	t.Cleanup(up.Close)
	ht := newHookOrderTest(t, up.URL, hookModelFallback, false)
	req := httptest.NewRequest(http.MethodPost, "/v1/messages", strings.NewReader(`{"model":"primary-model","max_tokens":32,"stream":true,"messages":[{"role":"user","content":"hello"}]}`))
	req.Header.Set("x-api-key", "sk-hook")
	w := &messagesRoutingWriter{ResponseRecorder: httptest.NewRecorder(), output: output}
	ht.srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != 200 || !strings.Contains(w.Body.String(), "partial answer") || calls.Load() != 1 {
		t.Fatalf("stream after output -> %d %s; upstream calls=%d, want exactly one", w.Code, w.Body, calls.Load())
	}
	if !strings.Contains(w.Body.String(), "event: error\n") || strings.Contains(w.Body.String(), "event: message_stop\n") {
		t.Fatalf("failed stream must report an error, not a successful stop: %s", w.Body)
	}
}
