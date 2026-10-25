package server

// An async job queued before a promote and started after the drain sends
// the new key.

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestAsyncJobAfterDrainSendsTheNewKey(t *testing.T) {
	up := startRotUpstream(t)
	srv := rotServer(t, up.URL, 20*time.Millisecond, func(c *config.Config) { c.Async.MaxWorkers = 1 })
	submit := func(content string) {
		req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(`{"model":"gpt-4o","messages":[{"role":"user","content":"`+content+`"}]}`))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+rotInternal)
		req.Header.Set("x-agentcc-async", "true")
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != http.StatusAccepted {
			t.Fatalf("async submit %q -> %d %s", content, w.Code, w.Body.String())
		}
	}
	submit("hold")
	select {
	case <-up.held:
	case <-time.After(5 * time.Second):
		t.Fatal("job A never reached upstream")
	}
	submit("queued-B")
	if w := rotPromote(t, srv, rotNew); w.Code != http.StatusOK {
		t.Fatalf("promote -> %d %s", w.Code, w.Body.String())
	}
	rotChat(t, srv, rotInternal, "sync-after-promote")
	time.Sleep(200 * time.Millisecond) // the 20 ms drain is over
	close(up.release)
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		up.mu.Lock()
		n := len(up.chats)
		up.mu.Unlock()
		if n >= 3 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	up.mu.Lock()
	defer up.mu.Unlock()
	t.Logf("upstream chat Authorization in order (A held, sync, B queued): %v", up.chats)
	want := []string{"Bearer " + rotOld, "Bearer " + rotNew, "Bearer " + rotNew}
	if len(up.chats) != 3 || up.chats[0] != want[0] || up.chats[1] != want[1] || up.chats[2] != want[2] {
		t.Errorf("upstream Authorization = %v, want %v (A in flight on the old key; the sync call and B, started after the drain, on the new one)", up.chats, want)
	}
}
