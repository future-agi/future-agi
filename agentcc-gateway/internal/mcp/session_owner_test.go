package mcp

import (
	"bufio"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// ownerKeyAuth knows two keys of org-a and one of org-b, with no tool limits.
type ownerKeyAuth struct{}

func (ownerKeyAuth) AuthenticateKey(rawKey string) (KeyGrant, bool) {
	switch rawKey {
	case "key-a":
		return KeyGrant{KeyID: "id-a", OrgID: "org-a"}, true
	case "key-a2":
		return KeyGrant{KeyID: "id-a2", OrgID: "org-a"}, true
	case "key-b":
		return KeyGrant{KeyID: "id-b", OrgID: "org-b"}, true
	}
	return KeyGrant{}, false
}

func newOwnedMCPServer(t *testing.T) *Server {
	t.Helper()
	s := newTestServer(t)
	t.Cleanup(s.Close)
	s.SetKeyAuth(ownerKeyAuth{})
	return s
}

func storedSession(t *testing.T, s *Server, id string) *Session {
	t.Helper()
	v, ok := s.sessions.sessions.Load(id)
	if !ok {
		t.Fatalf("session %s is gone", id)
	}
	return v.(*Session)
}

func TestInitializeBindsTheSessionToItsKey(t *testing.T) {
	s := newOwnedMCPServer(t)
	id := initializeSessionWith(t, s, map[string]string{"Authorization": "Bearer key-a"})
	if got := storedSession(t, s, id).owner; got != (SessionOwner{KeyID: "id-a", OrgID: "org-a"}) {
		t.Fatalf("session owner = %+v, want id-a in org-a", got)
	}
}

func TestSessionManagerGetIgnoresAnotherOwner(t *testing.T) {
	sm := NewSessionManager(time.Minute)
	defer sm.Close()
	owner := SessionOwner{KeyID: "id-a", OrgID: "org-a"}
	s := sm.Create(owner, Implementation{Name: "t"}, ClientCapabilities{})
	s.lastUsed.Store(time.Now().Add(-30 * time.Second).UnixNano())
	before := s.lastUsed.Load()

	for _, other := range []SessionOwner{
		{KeyID: "id-b", OrgID: "org-b"},
		{KeyID: "id-a2", OrgID: "org-a"}, // same org, another key
		{KeyID: "id-a", OrgID: "org-b"},  // same key id, another org
		{},                               // no key at all
	} {
		if got := sm.Get(s.ID, other); got != nil {
			t.Fatalf("Get by %+v returned owner %+v's session", other, owner)
		}
	}
	if s.lastUsed.Load() != before {
		t.Fatal("another owner's Get marked the session used")
	}
	if sm.Get(s.ID, owner) == nil {
		t.Fatal("the owner's Get did not find its session")
	}

	// An expired session is not removed by another owner's Get either.
	s.lastUsed.Store(time.Now().Add(-2 * time.Minute).UnixNano())
	if sm.Get(s.ID, SessionOwner{KeyID: "id-b", OrgID: "org-b"}) != nil {
		t.Fatal("Get by another owner returned an expired session")
	}
	if sm.Count() != 1 {
		t.Fatalf("another owner's Get removed the session (count %d)", sm.Count())
	}
}

// Another key's calls inside a session neither use nor refresh it.
func TestAnotherKeysCallsLeaveTheSessionUntouched(t *testing.T) {
	s := newOwnedMCPServer(t)
	id := initializeSessionWith(t, s, map[string]string{"Authorization": "Bearer key-a"})
	sess := storedSession(t, s, id)
	sess.lastUsed.Store(time.Now().Add(-time.Minute).UnixNano())
	before := sess.lastUsed.Load()

	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`2`), Method: MethodToolsList}
	for _, key := range []string{"key-b", "key-a2"} {
		w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": id, "Authorization": "Bearer " + key})
		if w.Code != http.StatusNotFound {
			t.Fatalf("tools/list with %s on key-a's session: status %d, want 404. Body: %s", key, w.Code, w.Body.String())
		}

		ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
		req := httptest.NewRequest("GET", "/mcp", nil).WithContext(ctx)
		req.Header.Set("MCP-Session-Id", id)
		req.Header.Set("Authorization", "Bearer "+key)
		sw := httptest.NewRecorder()
		s.HandleSSE(sw, req)
		cancel()
		if sw.Code != http.StatusNotFound {
			t.Fatalf("GET /mcp with %s on key-a's session: status %d, want 404", key, sw.Code)
		}
	}
	if sess.lastUsed.Load() != before {
		t.Fatal("another key's calls marked key-a's session used")
	}
	if storedSession(t, s, id) != sess {
		t.Fatal("key-a's session was replaced")
	}
}

// GET /mcp attaches a session's notification stream for the key that opened
// the session only: another key's GET gets 404, and the owner's stream keeps
// receiving.
func TestAnotherKeyCannotTakeOverTheStream(t *testing.T) {
	s := newOwnedMCPServer(t)
	id := initializeSessionWith(t, s, map[string]string{"Authorization": "Bearer key-a"})

	ts := httptest.NewServer(http.HandlerFunc(s.HandleSSE))
	defer ts.Close()

	open := func(key string, timeout time.Duration) (*http.Response, context.CancelFunc) {
		ctx, cancel := context.WithTimeout(context.Background(), timeout)
		req, _ := http.NewRequestWithContext(ctx, "GET", ts.URL, nil)
		req.Header.Set("MCP-Session-Id", id)
		req.Header.Set("Authorization", "Bearer "+key)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			cancel()
			t.Fatalf("GET /mcp with %s: %v", key, err)
		}
		return resp, cancel
	}

	ownerResp, cancelOwner := open("key-a", 10*time.Second)
	defer cancelOwner()
	defer ownerResp.Body.Close()
	if ownerResp.StatusCode != http.StatusOK {
		t.Fatalf("owner GET /mcp: status %d", ownerResp.StatusCode)
	}
	// Wait until the owner's stream is registered.
	deadline := time.Now().Add(5 * time.Second)
	for {
		s.sseMu.RLock()
		n := len(s.sseClients)
		s.sseMu.RUnlock()
		if n == 1 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("owner's stream never registered")
		}
		time.Sleep(5 * time.Millisecond)
	}

	for _, key := range []string{"key-b", "key-a2"} {
		resp, cancel := open(key, 2*time.Second)
		resp.Body.Close()
		cancel()
		if resp.StatusCode != http.StatusNotFound {
			t.Fatalf("GET /mcp with %s on key-a's session: status %d, want 404", key, resp.StatusCode)
		}
	}

	// A tools-changed notification still reaches the owner's stream.
	s.UnregisterClient("no-such-server")
	got := make(chan string, 1)
	go func() {
		sc := bufio.NewScanner(ownerResp.Body)
		for sc.Scan() {
			if line := sc.Text(); strings.HasPrefix(line, "data: ") {
				got <- line
				return
			}
		}
		got <- ""
	}()
	select {
	case line := <-got:
		if !strings.Contains(line, MethodToolsListChanged) {
			t.Fatalf("owner's stream got %q, want a %s notification", line, MethodToolsListChanged)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("owner's stream did not receive its notification")
	}
}
