package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// managerSession opens a session on sm's memory store bound to b.
func managerSession(t *testing.T, sm *SessionManager, b sessionBinding) *Session {
	t.Helper()
	s, err := sm.create(context.Background(), sm.mem, b, "2025-06-18", Implementation{Name: "test", Version: "1.0"}, ClientCapabilities{}, nil)
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	return s
}

func resumeManager(t *testing.T, sm *SessionManager, id string, b sessionBinding) *Session {
	t.Helper()
	s, err := sm.resume(context.Background(), sm.mem, id, b)
	if err != nil {
		t.Fatalf("lookup: %v", err)
	}
	return s
}

var bindingA = sessionBinding{Kind: PrincipalKey, KeyID: "key-a", OrgID: "org-a"}

func TestSessionManagerCreate(t *testing.T) {
	sm := NewSessionManager(5 * time.Minute)
	defer sm.Close()

	s := managerSession(t, sm, bindingA)
	if s.ID == "" || s.ClientInfo.Name != "test" || s.ProtocolVersion != "2025-06-18" {
		t.Fatalf("session = %+v", s)
	}
	if s.Hash() != SessionHash(s.ID) || len(s.Hash()) != 32 {
		t.Fatalf("hash %q is not SessionHash(id)", s.Hash())
	}
	if _, ok, _ := sm.mem.Load(context.Background(), sessionKey("org-a", s.Hash())); !ok {
		t.Fatalf("no record under %s", sessionKey("org-a", s.Hash()))
	}
}

func TestSessionManagerGetValid(t *testing.T) {
	sm := NewSessionManager(5 * time.Minute)
	defer sm.Close()

	s := managerSession(t, sm, bindingA)
	got := resumeManager(t, sm, s.ID, bindingA)
	if got == nil || got.ID != s.ID {
		t.Fatalf("lookup = %+v, want the session", got)
	}
}

func TestSessionManagerGetExpired(t *testing.T) {
	sm := NewSessionManager(50 * time.Millisecond)
	defer sm.Close()

	s := managerSession(t, sm, bindingA)
	time.Sleep(80 * time.Millisecond)
	if got := resumeManager(t, sm, s.ID, bindingA); got != nil {
		t.Fatal("expected an expired session to be gone")
	}
	if sm.Count() != 0 {
		t.Fatalf("an expired session is still counted (%d)", sm.Count())
	}
}

func TestSessionManagerGetNotFound(t *testing.T) {
	sm := NewSessionManager(5 * time.Minute)
	defer sm.Close()

	for _, id := range []string{"nonexistent", "", strings.Repeat("A", 120)} {
		if got := resumeManager(t, sm, id, bindingA); got != nil {
			t.Fatalf("lookup(%q) found a session", id)
		}
	}
}

func TestSessionManagerDelete(t *testing.T) {
	sm := NewSessionManager(5 * time.Minute)
	defer sm.Close()

	s := managerSession(t, sm, bindingA)
	if err := sm.end(context.Background(), sm.mem, s); err != nil {
		t.Fatal(err)
	}
	if got := resumeManager(t, sm, s.ID, bindingA); got != nil {
		t.Fatal("expected a deleted session to be gone")
	}
}

func TestSessionManagerCount(t *testing.T) {
	sm := NewSessionManager(5 * time.Minute)
	defer sm.Close()

	for i := 0; i < 3; i++ {
		managerSession(t, sm, bindingA)
	}
	if sm.Count() != 3 {
		t.Fatalf("expected 3 sessions, got %d", sm.Count())
	}
}

// Use keeps a session alive: each lookup renews its record's ttl once its
// last_seen is a tenth of the ttl old.
func TestSessionTouch(t *testing.T) {
	sm := NewSessionManager(400 * time.Millisecond)
	defer sm.Close()

	s := managerSession(t, sm, bindingA)
	for i := 0; i < 8; i++ { // 640 ms in all, well past the ttl
		time.Sleep(80 * time.Millisecond)
		if got := resumeManager(t, sm, s.ID, bindingA); got == nil {
			t.Fatalf("session expired after %d uses", i)
		}
	}
}

func TestSessionIdleSince(t *testing.T) {
	s := &Session{
		CreatedAt: time.Now(),
	}

	// No touch yet — should use CreatedAt.
	idle := s.IdleSince()
	if idle < 0 {
		t.Fatal("idle should be non-negative")
	}

	time.Sleep(5 * time.Millisecond)
	s.Touch()

	idle2 := s.IdleSince()
	if idle2 > 10*time.Millisecond {
		t.Fatalf("expected small idle after touch, got %v", idle2)
	}
}

// A session id is a sealed, unique, visible-ASCII token for the session
// purpose: it does not open as any other purpose, and its payload names the
// org but no secret.
func TestSessionIDsAreSealed(t *testing.T) {
	sm := NewSessionManager(5 * time.Minute)
	defer sm.Close()
	codec, err := sm.codec()
	if err != nil {
		t.Fatal(err)
	}

	ids := map[string]bool{}
	for i := 0; i < 50; i++ {
		id := managerSession(t, sm, bindingA).ID
		if ids[id] {
			t.Fatal("duplicate session id")
		}
		ids[id] = true
		for _, c := range id {
			if c < 0x21 || c > 0x7e {
				t.Fatalf("session id has a non-visible character %q", c)
			}
		}
		payload, err := codec.Open(StatePurposeSession, id)
		if err != nil {
			t.Fatalf("session id does not open: %v", err)
		}
		var tok sessionToken
		if json.Unmarshal(payload, &tok) != nil || tok.Org != "org-a" || len(tok.Nonce) != 32 {
			t.Fatalf("payload = %s", payload)
		}
		if _, err := codec.Open(StatePurposeCursor, id); err == nil {
			t.Fatal("a session id opened as a cursor")
		}
	}
}

// The memory store sweeps expired entries on Save, so a session nobody
// loads again does not stay in memory.
func TestMemorySessionStoreSweeps(t *testing.T) {
	m := newMemorySessionStore()
	now := time.Date(2026, 10, 18, 9, 0, 0, 0, time.UTC)
	m.now = func() time.Time { return now }
	ctx := context.Background()

	if err := m.Save(ctx, "short", []byte("a"), time.Second); err != nil {
		t.Fatal(err)
	}
	if err := m.Save(ctx, "long", []byte("b"), time.Hour); err != nil {
		t.Fatal(err)
	}
	if err := m.Save(ctx, "forever", []byte("c"), 0); err != nil {
		t.Fatal(err)
	}

	now = now.Add(2 * time.Minute) // "short" expired; nobody loads it
	if err := m.Save(ctx, "new", []byte("d"), time.Hour); err != nil {
		t.Fatal(err)
	}
	m.mu.Lock()
	_, kept := m.entries["short"]
	n := len(m.entries)
	m.mu.Unlock()
	if kept || n != 3 {
		t.Fatalf("after a Save past the sweep interval: %d entries, short kept=%v; want 3, swept", n, kept)
	}

	// The reaper's explicit sweep removes what expired since.
	now = now.Add(2 * time.Hour)
	m.sweep()
	m.mu.Lock()
	n = len(m.entries)
	_, forever := m.entries["forever"]
	m.mu.Unlock()
	if n != 1 || !forever {
		t.Fatalf("after sweep: %d entries (forever kept=%v), want only the one with no ttl", n, forever)
	}

	// Refresh never brings back an entry that is gone.
	if ok, _ := m.Refresh(ctx, "long", "", []byte("x"), time.Hour); ok {
		t.Fatal("Refresh rewrote an expired entry")
	}
	if _, ok, _ := m.Load(ctx, "long"); ok {
		t.Fatal("an expired entry came back")
	}
}

func TestMemoryEventLogCappedAndOrdered(t *testing.T) {
	l := newMemoryEventLog()
	var first, last string
	for i := 0; i < sessionEventLogMax+5; i++ {
		id := l.append("ev", []byte{byte(i)})
		if first == "" {
			first = id
		}
		if last != "" {
			if less, ok := eventIDLess(last, id); !ok || !less {
				t.Fatalf("id %s does not follow %s", id, last)
			}
		}
		last = id
	}
	var got []string
	l.after("ev", "0-0", 2*sessionEventLogMax, func(id string, _ []byte) { got = append(got, id) })
	if len(got) != sessionEventLogMax || got[0] == first || got[len(got)-1] != last {
		t.Fatalf("kept %d events from %s to %s, want the last %d ending %s", len(got), got[0], got[len(got)-1], sessionEventLogMax, last)
	}
	var after []string
	l.after("ev", got[len(got)-2], 10, func(id string, _ []byte) { after = append(after, id) })
	if len(after) != 1 || after[0] != last {
		t.Fatalf("after the second-to-last event: %v, want [%s]", after, last)
	}
}

// --- sessions over HTTP (sessionHarness, sse_stream_test.go) ---

func TestInitializeNegotiatesVersion(t *testing.T) {
	s := newSessionServer(t)
	h := newSessionHarness(t, s)
	for _, tc := range []struct{ asked, want string }{
		{"2025-11-25", "2025-11-25"},
		{"2025-06-18", "2025-06-18"},
		{"2025-03-26", "2025-03-26"},
		{"2024-11-05", "2025-11-25"}, // HTTP+SSE's revision: served on its own routes
		{"2026-07-28", "2025-11-25"}, // the stateless revision has no initialize
		{"2099-01-01", "2025-11-25"},
		{"", "2025-11-25"},
	} {
		id, got := h.initialize("/mcp", "a", tc.asked)
		if got != tc.want {
			t.Errorf("asked %q: negotiated %q, want %q", tc.asked, got, tc.want)
		}
		if rec, ok := storedRecord(t, s, id); !ok || rec.Era != tc.want {
			t.Errorf("asked %q: record era %q", tc.asked, rec.Era)
		}
	}

	// Only the served revisions count (mcp.protocol.versions).
	s.ConfigureSessions(SessionOptions{Versions: []string{"2026-07-28", "2025-06-18", "2025-03-26"}})
	for _, tc := range []struct{ asked, want string }{
		{"2025-11-25", "2025-06-18"},
		{"2025-03-26", "2025-03-26"},
	} {
		if _, got := h.initialize("/mcp", "a", tc.asked); got != tc.want {
			t.Errorf("with 2025-11-25 off, asked %q: negotiated %q, want %q", tc.asked, got, tc.want)
		}
	}

	// No 2025 revision served, or legacy sessions off: initialize is refused
	// with -32022 and the supported list, and opens nothing.
	before := s.SessionCount()
	for _, o := range []SessionOptions{
		{Versions: []string{"2026-07-28"}},
		{Versions: []string{"2026-07-28", "2025-06-18"}, LegacyDisabled: true},
	} {
		s.ConfigureSessions(o)
		code, hdr, body := h.do(http.MethodPost, "/mcp", "a", "", rpc(MethodInitialize, map[string]any{"protocolVersion": "2025-06-18"}))
		var resp Message
		_ = json.Unmarshal([]byte(body), &resp)
		if code != http.StatusBadRequest || hdr.Get("MCP-Session-Id") != "" || resp.Error == nil ||
			resp.Error.Code != errCodeUnsupportedVersion || string(resp.Error.Data) != `{"requested":"2025-06-18","supported":["2026-07-28"]}` {
			t.Fatalf("options %+v: initialize answered %d %s", o, code, body)
		}
	}
	if s.SessionCount() != before {
		t.Fatal("a refused initialize opened a session")
	}
}

func TestSessionBoundToPrincipal(t *testing.T) {
	s := newSessionServer(t)
	h := newSessionHarness(t, s)
	before := time.Now().UnixMilli()
	id, _ := h.initialize("/mcp", "a", "2025-06-18")

	// The record binds the principal, virtual server, era, capabilities and
	// times, under the shared key, and never holds the id.
	sess, _ := s.sessions.sessions.Load(id)
	if key := sess.(*Session).key(); key != "mcps:{org-a}:"+SessionHash(id) {
		t.Fatalf("record key %q", key)
	}
	rec, ok := storedRecord(t, s, id)
	if !ok || rec.PrincipalKind != PrincipalKey || rec.KeyID != "key-a" || rec.OrgID != "org-a" || rec.UserID != "" ||
		rec.VirtualServer != "" || rec.Era != "2025-06-18" || rec.ClientInfo.Name != "session-test" ||
		!strings.Contains(string(rec.ClientCapabilities), `"elicitation":{}`) || rec.CreatedMs < before || rec.LastSeenMs < rec.CreatedMs {
		t.Fatalf("record = %+v", rec)
	}
	raw, _ := json.Marshal(rec)
	if strings.Contains(string(raw), id) {
		t.Fatal("the record holds the session id")
	}

	// The same principal uses it; any principal that differs in one field
	// is answered exactly as an unknown session, and changes nothing.
	if code, body := h.toolsList("/mcp", "a", id); code != http.StatusOK {
		t.Fatalf("owner tools/list: %d %s", code, body)
	}
	recBefore := recordJSON(t, s, id)
	unknownCode, unknownBody := h.toolsList("/mcp", "a", "unknown-session")
	for _, other := range []string{"other-org", "other-key", "user", ""} {
		if code, body := h.toolsList("/mcp", other, id); code != unknownCode || body != unknownBody || code != http.StatusNotFound {
			t.Fatalf("principal %q on a's session: %d %q, want the unknown-session answer %d %q", other, code, body, unknownCode, unknownBody)
		}
		if code, _, _ := h.do(http.MethodGet, "/mcp", other, id, nil); code != http.StatusNotFound {
			t.Fatalf("principal %q GET on a's session: %d", other, code)
		}
	}
	if recAfter := recordJSON(t, s, id); recAfter != recBefore {
		t.Fatalf("other principals changed the record: %s -> %s", recBefore, recAfter)
	}
	st := h.openStream("/mcp", "a", id, "")
	st.close()

	// A user principal's session binds the user and client.
	uid, _ := h.initialize("/mcp", "user", "2025-06-18")
	if rec, _ := storedRecord(t, s, uid); rec.PrincipalKind != PrincipalOAuth || rec.UserID != "user-1" || rec.ClientID != "client-1" {
		t.Fatalf("user record = %+v", rec)
	}
	if code, _ := h.toolsList("/mcp", "other-user", uid); code != http.StatusNotFound {
		t.Fatalf("another user on user-1's session: %d", code)
	}

	// The virtual server is bound too.
	vid, _ := h.initialize("/mcp/v/vs_1", "a", "2025-06-18")
	if rec, _ := storedRecord(t, s, vid); rec.VirtualServer != "vs_1" {
		t.Fatalf("virtual server = %q", rec.VirtualServer)
	}
	for path, want := range map[string]int{"/mcp/v/vs_1": 200, "/mcp/v/vs_2": 404, "/mcp": 404} {
		if code, body := h.toolsList(path, "a", vid); code != want {
			t.Fatalf("vs_1's session on %s: %d %s, want %d", path, code, body, want)
		}
	}
	if code, _ := h.toolsList("/mcp/v/vs_1", "a", id); code != http.StatusNotFound {
		t.Fatalf("the /mcp session on vs_1: %d", code)
	}

	// An id sealed under another state key opens nowhere here.
	other, err := NewStateCodec([]string{strings.Repeat("k", 40)}, false)
	if err != nil {
		t.Fatal(err)
	}
	forged, _ := other.Seal(StatePurposeSession, []byte(`{"o":"org-a","n":"00"}`), time.Hour)
	if code, _ := h.toolsList("/mcp", "a", forged); code != http.StatusNotFound {
		t.Fatalf("an id sealed under another key: %d", code)
	}
}

// keyPairAuth accepts two keys with no tool limits: per-key auth as the
// gateway's KeyAuth sets it at the PIN.
type keyPairAuth struct{}

// Another key presenting a session's id gets the unknown-session answer on
// every method, and the session stays its owner's. It fails at the PIN,
// where any valid key could use any session.
func TestSessionFromOtherKey404(t *testing.T) {
	s := newSessionServer(t)
	s.SetKeyAuth(keyPairAuth{})
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodPost:
			s.HandlePost(w, r)
		case http.MethodGet:
			s.HandleSSE(w, r)
		case http.MethodDelete:
			s.HandleDelete(w, r)
		}
	}))
	defer ts.Close()
	send := func(method, key, sessionID string, body []byte) (int, string) {
		req, _ := http.NewRequest(method, ts.URL+"/mcp", bytes.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+key)
		if sessionID != "" {
			req.Header.Set("MCP-Session-Id", sessionID)
		}
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		resp, err := http.DefaultClient.Do(req.WithContext(ctx))
		if err != nil {
			t.Fatalf("%s: %v", method, err)
		}
		defer resp.Body.Close()
		data, _ := io.ReadAll(resp.Body)
		return resp.StatusCode, string(data)
	}

	initBody, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": MethodInitialize,
		"params": map[string]any{"protocolVersion": "2025-06-18", "clientInfo": map[string]any{"name": "a"}}})
	req, _ := http.NewRequest(http.MethodPost, ts.URL+"/mcp", bytes.NewReader(initBody))
	req.Header.Set("Content-Type", "application/json") // the protocol handler answers 415 without it
	req.Header.Set("Authorization", "Bearer key-a")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	id := resp.Header.Get("MCP-Session-Id")
	if id == "" {
		t.Fatalf("initialize with key-a: %d", resp.StatusCode)
	}

	list := rpc(MethodToolsList, nil)
	for _, method := range []string{http.MethodPost, http.MethodGet, http.MethodDelete} {
		body := list
		if method != http.MethodPost {
			body = nil
		}
		unknownCode, unknownBody := send(method, "key-b", "not-a-session", body)
		code, got := send(method, "key-b", id, body)
		if code != http.StatusNotFound || code != unknownCode || got != unknownBody {
			t.Fatalf("%s with key-b on key-a's session: %d %q, want the unknown-session answer %d %q", method, code, got, unknownCode, unknownBody)
		}
	}
	if code, body := send(http.MethodPost, "key-a", id, list); code != http.StatusOK || strings.Contains(body, `"error"`) {
		t.Fatalf("key-a after key-b's attempts: %d %s", code, body)
	}
}

// Ids are logged only as their hash, from initialize to DELETE.
func TestSessionIdNeverLogged(t *testing.T) {
	var buf lockedBuffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, &slog.HandlerOptions{Level: slog.LevelDebug})))
	defer slog.SetDefault(prev)

	s := newSessionServer(t)
	h := newSessionHarness(t, s)
	id, _ := h.initialize("/mcp", "a", "2025-06-18")
	h.toolsList("/mcp", "a", id)
	h.toolsList("/mcp", "other-key", id)
	st := h.openStream("/mcp", "a", id, "")
	s.notifyToolsChanged()
	st.next("list_changed")
	st.close()
	waitStreams(t, s, id, 0)
	if code, _, _ := h.do(http.MethodDelete, "/mcp", "a", id, nil); code != http.StatusNoContent {
		t.Fatalf("DELETE: %d", code)
	}
	h.toolsList("/mcp", "a", id)

	logs := buf.String()
	if strings.Contains(logs, id) || strings.Contains(logs, id[:24]) {
		t.Fatalf("the session id reached the logs:\n%s", logs)
	}
	if !strings.Contains(logs, "session_hash="+SessionHash(id)) {
		t.Fatalf("the logs never name the session by its hash:\n%s", logs)
	}
}

// DELETE ends the session (204) everywhere: its record, event log and open
// streams; every later request on it is 404. Without a session id it is 405
// with Allow: POST, as GET without one.
// Replaces dispatch_test.go's TestHandleDeleteAnswers405.
func TestDeleteEndsSession(t *testing.T) {
	s := newSessionServer(t)
	h := newSessionHarness(t, s)

	code, hdr, body := h.do(http.MethodDelete, "/mcp", "a", "", nil)
	if code != http.StatusMethodNotAllowed || hdr.Get("Allow") != http.MethodPost || !strings.Contains(body, `"method_not_allowed"`) {
		t.Fatalf("DELETE without a session: %d %v %s", code, hdr, body)
	}

	id, _ := h.initialize("/mcp", "a", "2025-06-18")
	st := h.openStream("/mcp", "a", id, "")
	sess, _ := s.sessions.sessions.Load(id)
	key, eventsKey := sess.(*Session).key(), sess.(*Session).eventsKey()
	s.notifyToolsChanged()
	st.next("before DELETE")

	if code, _, body := h.do(http.MethodDelete, "/mcp", "other-key", id, nil); code != http.StatusNotFound {
		t.Fatalf("DELETE by another key: %d %s", code, body)
	}
	if code, _, body := h.do(http.MethodDelete, "/mcp", "a", id, nil); code != http.StatusNoContent || body != "" {
		t.Fatalf("DELETE: %d %q", code, body)
	}
	st.expectEnd("the open stream after DELETE")
	if _, ok, _ := s.sessionStore().Load(context.Background(), key); ok {
		t.Fatal("the record outlived DELETE")
	}
	var logged int
	s.sessions.events.after(eventsKey, "0-0", 10, func(string, []byte) { logged++ })
	if logged != 0 {
		t.Fatalf("the event log outlived DELETE (%d events)", logged)
	}
	if code, _ := h.toolsList("/mcp", "a", id); code != http.StatusNotFound {
		t.Fatalf("tools/list after DELETE: %d", code)
	}
	if code, _, _ := h.do(http.MethodGet, "/mcp", "a", id, nil); code != http.StatusNotFound {
		t.Fatalf("GET after DELETE: %d", code)
	}
	if code, _, _ := h.do(http.MethodDelete, "/mcp", "a", id, nil); code != http.StatusNotFound {
		t.Fatalf("a second DELETE: %d", code)
	}
	if s.SessionCount() != 0 {
		t.Fatalf("%d sessions after DELETE", s.SessionCount())
	}
	// The client initializes again.
	if again, _ := h.initialize("/mcp", "a", "2025-06-18"); again == id {
		t.Fatal("a new session reused the ended id")
	}
}

// ping answers on legacy sessions only: on its own session, as the session's
// principal; another principal's ping on it is the unknown-session 404; a
// stateless (2026) ping is an unknown method; a ping before initialize, with
// no session, is answered.
func TestPingLegacyOnly(t *testing.T) {
	s := newSessionServer(t)
	h := newSessionHarness(t, s)
	id, _ := h.initialize("/mcp", "a", "2025-06-18")
	ping := rpc(MethodPing, nil)

	if code, _, body := h.do(http.MethodPost, "/mcp", "a", id, ping); code != http.StatusOK || body != `{"jsonrpc":"2.0","id":7,"result":{}}`+"\n" {
		t.Fatalf("ping on the session: %d %s", code, body)
	}
	if code, _, body := h.do(http.MethodPost, "/mcp", "other-key", id, ping); code != http.StatusNotFound || body != "" {
		t.Fatalf("ping by another key on the session: %d %q", code, body)
	}
	if code, _, body := h.do(http.MethodPost, "/mcp", "a", "unknown-session", ping); code != http.StatusNotFound || body != "" {
		t.Fatalf("ping on an unknown session: %d %q", code, body)
	}
	stateless := rpc(MethodPing, map[string]any{"_meta": map[string]any{
		"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": map[string]any{},
	}})
	// 2026 ignores MCP-Session-Id, so a stale (or live) one changes nothing.
	// A well-formed 2026-07-28 request carries the headers the protocol core checks before
	// dispatch (MCP-Protocol-Version, Mcp-Method, Accept), and an unknown
	// method on that era is HTTP 404 with -32601 (the protocol core's
	// TestUnknownMethod404On2026And200OnLegacy pins ping among them).
	for _, sid := range []string{"", id, "unknown-session"} {
		code, _, body := h.do(http.MethodPost, "/mcp", "a", sid, stateless,
			"MCP-Protocol-Version", "2026-07-28", "Mcp-Method", MethodPing, "Accept", "application/json, text/event-stream")
		var resp Message
		if code != http.StatusNotFound || json.Unmarshal([]byte(body), &resp) != nil || resp.Error == nil || resp.Error.Code != ErrCodeMethodNotFound {
			t.Fatalf("a stateless ping with session id %q: %d %s, want HTTP 404 -32601", sid, code, body)
		}
	}
	if code, _, body := h.do(http.MethodPost, "/mcp", "a", "", ping); code != http.StatusOK || !strings.Contains(body, `"result":{}`) {
		t.Fatalf("a ping before initialize: %d %s", code, body)
	}
}

// Two replicas on one Redis and one state key: either serves the session,
// replays the other's events, and a DELETE on one ends it on both.
func TestSessionResumesOnOtherReplica(t *testing.T) {
	store, rdb, prefix := redisSessionStore(t)
	codec, err := NewStateCodec([]string{"m12-replica-test-state-key-0000000000000"}, false)
	if err != nil {
		t.Fatal(err)
	}
	replica := func() (*Server, *sessionHarness) {
		s := newSessionServer(t)
		s.SetDeps(Deps{SessionStore: store})
		s.ConfigureSessions(SessionOptions{Codec: codec})
		return s, newSessionHarness(t, s)
	}
	a, ha := replica()
	b, hb := replica()

	id, _ := ha.initialize("/mcp", "a", "2025-06-18")
	key := prefix + "mcps:{org-a}:" + SessionHash(id)
	ttl, err := rdb.PTTL(context.Background(), key).Result()
	if err != nil || ttl <= 0 || ttl > 5*time.Minute {
		t.Fatalf("record %s: PTTL %v %v, want within the 5m session ttl", key, ttl, err)
	}
	if raw, _ := rdb.Get(context.Background(), key).Result(); strings.Contains(raw, id) || !strings.Contains(raw, `"key_id":"key-a"`) {
		t.Fatalf("record = %s", raw)
	}

	if code, body := hb.toolsList("/mcp", "a", id); code != http.StatusOK {
		t.Fatalf("tools/list on replica b: %d %s", code, body)
	}
	if code, _ := hb.toolsList("/mcp", "other-org", id); code != http.StatusNotFound {
		t.Fatalf("another org's key on replica b: %d", code)
	}

	// A stream on b gets b's events, ids from the shared log; an event a
	// raises while b's stream is away is replayed on b.
	st := hb.openStream("/mcp", "a", id, "")
	b.notifyToolsChanged()
	e1 := st.next("b's event")
	requireListChanged(t, e1, "b's event")
	if _, _, ok := parseEventID(e1.id); !ok || strings.HasPrefix(e1.id, "0-") {
		t.Fatalf("event id %q is not the shared log's", e1.id)
	}
	st.close()
	waitStreams(t, b, id, 0)
	a.notifyToolsChanged()
	waitLogged(t, b, id, e1.id, 1)
	replayed := hb.openStream("/mcp", "a", id, e1.id).next("a's event replayed on b")
	requireListChanged(t, replayed, "a's event on b")
	requireAfter(t, e1.id, replayed.id)

	// A replica with another state key cannot open the id.
	other := newSessionServer(t)
	other.SetDeps(Deps{SessionStore: store})
	if code, _ := newSessionHarness(t, other).toolsList("/mcp", "a", id); code != http.StatusNotFound {
		t.Fatalf("a replica with another state key: %d", code)
	}

	if code, _, _ := hb.do(http.MethodDelete, "/mcp", "a", id, nil); code != http.StatusNoContent {
		t.Fatalf("DELETE on b: %d", code)
	}
	if code, _ := ha.toolsList("/mcp", "a", id); code != http.StatusNotFound {
		t.Fatalf("tools/list on a after b's DELETE: %d", code)
	}
	if n, _ := rdb.Exists(context.Background(), key, prefix+"mcpev:{org-a}:"+SessionHash(id)).Result(); n != 0 {
		t.Fatalf("%d of the session's keys outlived DELETE", n)
	}
}

// failingStore fails every call, as a Redis that is down does.
type failingStore struct{}

var errStoreDown = errors.New("store down")

func (failingStore) Save(context.Context, string, []byte, time.Duration) error { return errStoreDown }
func (failingStore) Load(context.Context, string) ([]byte, bool, error) {
	return nil, false, errStoreDown
}
func (failingStore) Delete(context.Context, string) error { return errStoreDown }

// A store that cannot be read or written is neither a found nor a missing
// session: every session request gets 503, so the client retries instead of
// initializing again (a 404 would drop a live session).
func TestSessionStoreFailureIs503(t *testing.T) {
	s := newSessionServer(t)
	h := newSessionHarness(t, s)
	id, _ := h.initialize("/mcp", "a", "2025-06-18") // on the memory store
	s.SetDeps(Deps{SessionStore: failingStore{}})

	code, hdr, body := h.do(http.MethodPost, "/mcp", "a", "", rpc(MethodInitialize, map[string]any{"protocolVersion": "2025-06-18"}))
	if code != http.StatusServiceUnavailable || hdr.Get("MCP-Session-Id") != "" || !strings.Contains(body, `"session store unavailable"`) {
		t.Fatalf("initialize with the store down: %d %v %s", code, hdr, body)
	}
	if code, body := h.toolsList("/mcp", "a", id); code != http.StatusServiceUnavailable || !strings.Contains(body, `"code":-32603`) {
		t.Fatalf("tools/list with the store down: %d %s", code, body)
	}
	for _, method := range []string{http.MethodGet, http.MethodDelete} {
		if code, _, _ := h.do(method, "/mcp", "a", id, nil); code != http.StatusServiceUnavailable {
			t.Fatalf("%s with the store down: %d", method, code)
		}
	}
}
