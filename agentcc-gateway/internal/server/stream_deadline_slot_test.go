package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// INT-3C r4 polish (PROVIDER-CONCURRENCY P3, the PC round-2 verifier's
// StartupDeadline/chat_stream cell): a stream whose request timeout
// (x-agentcc-timeout) ends while it waits for the provider entry's upstream
// slot is answered 504 gateway_timeout as JSON, before any stream header, on
// every streaming surface. Chat and Messages streams answered 500; the
// completions stream is the control that already answered 504. Nothing is
// sent upstream.
func TestStreamTimeoutDuringSlotWaitIs504(t *testing.T) {
	u := startSlotUpstream(t, 0, 0, 0)
	srv := newSlotGateway(t, u.URL, "max_concurrent: 1\nqueue_timeout: 5s\n", 30*time.Second)
	holdTheSlot(t, srv, u)
	for _, c := range []struct{ name, path, body string }{
		{"chat stream", "/v1/chat/completions", slotChatBody("m-chat", true)},
		{"messages stream", "/v1/messages", `{"model":"m-chat","max_tokens":16,"stream":true,"messages":[{"role":"user","content":"hi"}]}`},
		{"completions stream (control)", "/v1/completions", `{"model":"m-chat","prompt":"hi","stream":true}`},
	} {
		t.Run(c.name, func(t *testing.T) {
			done := make(chan slotAnswer, 1)
			go func() {
				req := httptest.NewRequest(http.MethodPost, c.path, strings.NewReader(c.body))
				req.Header.Set("Content-Type", "application/json")
				req.Header.Set("Authorization", "Bearer "+slotKey)
				req.Header.Set("anthropic-version", "2023-06-01")
				req.Header.Set("x-agentcc-timeout", "300ms")
				w := httptest.NewRecorder()
				start := time.Now()
				srv.httpServer.Handler.ServeHTTP(w, req)
				done <- slotAnswer{status: w.Code, body: w.Body.String(), header: w.Header(), elapsed: time.Since(start), done: true}
			}()
			var a slotAnswer
			select {
			case a = <-done:
			case <-time.After(4 * time.Second):
				t.Fatal("no answer 4s after a 300ms request timeout")
			}
			if a.status != http.StatusGatewayTimeout {
				t.Errorf("answered %d %s; want 504", a.status, a.body)
			}
			if !strings.HasPrefix(a.header.Get("Content-Type"), "application/json") {
				t.Errorf("answered with Content-Type %q; want a JSON error before any stream header", a.header.Get("Content-Type"))
			}
			if strings.Contains(a.body, "internal_error") || strings.Contains(a.body, "context deadline exceeded") {
				t.Errorf("answer %s exposes the bare context error", a.body)
			}
			if a.elapsed > 2*time.Second {
				t.Errorf("answered after %v; the request timeout was 300ms", a.elapsed)
			}
		})
	}
	if n := u.calls.Load(); n != 0 {
		t.Errorf("%d timed-out requests reached the upstream", n)
	}
}
