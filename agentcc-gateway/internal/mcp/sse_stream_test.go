package mcp

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// Principals the session tests present. Each differs from principalA in one
// binding field.
var (
	principalA         = Principal{Kind: PrincipalKey, KeyID: "key-a", OrgID: "org-a"}
	principalOtherOrg  = Principal{Kind: PrincipalKey, KeyID: "key-a", OrgID: "org-b"}
	principalOtherKey  = Principal{Kind: PrincipalKey, KeyID: "key-a2", OrgID: "org-a"}
	principalUser      = Principal{Kind: PrincipalOAuth, UserID: "user-1", ClientID: "client-1", OrgID: "org-a"}
	principalOtherUser = Principal{Kind: PrincipalOAuth, UserID: "user-2", ClientID: "client-1", OrgID: "org-a"}
)

// withPrincipal sets the principal a session binds to, as authenticateHTTP
// does once MCP auth resolves it (these tests set it).
func withPrincipal(r *http.Request, p Principal) *http.Request {
	return r.WithContext(context.WithValue(r.Context(), sessionPrincipalKey{}, p))
}

// sessionHarness serves s over HTTP the way the gateway routes the endpoint
// (POST, GET and DELETE on /mcp and /mcp/v/{vs_id}), presenting the principal
// the X-Test-Principal header names.
type sessionHarness struct {
	t          *testing.T
	s          *Server
	ts         *httptest.Server
	principals map[string]Principal
}

func newSessionHarness(t *testing.T, s *Server) *sessionHarness {
	t.Helper()
	h := &sessionHarness{t: t, s: s, principals: map[string]Principal{
		"a": principalA, "other-org": principalOtherOrg, "other-key": principalOtherKey,
		"user": principalUser, "other-user": principalOtherUser,
	}}
	h.ts = httptest.NewServer(h.handler())
	t.Cleanup(h.ts.Close)
	return h
}

func (h *sessionHarness) handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if name := r.Header.Get("X-Test-Principal"); name != "" {
			r = withPrincipal(r, h.principals[name])
		}
		switch r.Method {
		case http.MethodPost:
			h.s.HandlePost(w, r)
		case http.MethodGet:
			h.s.HandleSSE(w, r)
		case http.MethodDelete:
			h.s.HandleDelete(w, r)
		}
	})
}

func newSessionServer(t *testing.T) *Server {
	t.Helper()
	s := newTestServer(t)
	t.Cleanup(s.Close)
	return s
}

// do sends one request and returns its status, headers and body.
func (h *sessionHarness) do(method, path, principal, sessionID string, body []byte, headers ...string) (int, http.Header, string) {
	h.t.Helper()
	req, err := http.NewRequest(method, h.ts.URL+path, bytes.NewReader(body))
	if err != nil {
		h.t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	if principal != "" {
		req.Header.Set("X-Test-Principal", principal)
	}
	if sessionID != "" {
		req.Header.Set("MCP-Session-Id", sessionID)
	}
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		h.t.Fatalf("%s %s: %v", method, path, err)
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, resp.Header, string(data)
}

func rpc(method string, params any) []byte {
	msg := map[string]any{"jsonrpc": "2.0", "id": 7, "method": method}
	if params != nil {
		msg["params"] = params
	}
	data, _ := json.Marshal(msg)
	return data
}

// initialize opens a session on path as principal, asking for version, and
// returns its id and the negotiated version.
func (h *sessionHarness) initialize(path, principal, version string) (string, string) {
	h.t.Helper()
	code, hdr, body := h.do(http.MethodPost, path, principal, "", rpc(MethodInitialize, map[string]any{
		"protocolVersion": version,
		"capabilities":    map[string]any{"roots": map[string]any{"listChanged": true}, "elicitation": map[string]any{}},
		"clientInfo":      map[string]any{"name": "session-test", "version": "1"},
	}))
	var resp struct {
		Result InitializeResult `json:"result"`
	}
	if code != http.StatusOK || json.Unmarshal([]byte(body), &resp) != nil || hdr.Get("MCP-Session-Id") == "" {
		h.t.Fatalf("initialize: %d %v %s", code, hdr, body)
	}
	return hdr.Get("MCP-Session-Id"), resp.Result.ProtocolVersion
}

// toolsList posts tools/list on the session and returns the status and body.
func (h *sessionHarness) toolsList(path, principal, sessionID string) (int, string) {
	h.t.Helper()
	code, _, body := h.do(http.MethodPost, path, principal, sessionID, rpc(MethodToolsList, nil))
	return code, body
}

// streamRead is one event read from a GET stream.
type streamRead struct{ id, data string }

// testStream is an open GET stream and its events, read as they arrive.
type testStream struct {
	t      *testing.T
	resp   *http.Response
	cancel context.CancelFunc
	events chan streamRead
}

// openStream opens GET on path for the session; lastEventID may be "".
func (h *sessionHarness) openStream(path, principal, sessionID, lastEventID string) *testStream {
	h.t.Helper()
	return openStreamAt(h.t, h.ts.URL+path, map[string]string{
		"X-Test-Principal": principal, "MCP-Session-Id": sessionID, "Last-Event-ID": lastEventID,
	})
}

func openStreamAt(t *testing.T, url string, headers map[string]string) *testStream {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	req.Header.Set("Accept", "text/event-stream")
	for k, v := range headers {
		if v != "" {
			req.Header.Set(k, v)
		}
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		cancel()
		t.Fatalf("GET %s: %v", url, err)
	}
	if resp.StatusCode != http.StatusOK || resp.Header.Get("Content-Type") != "text/event-stream" {
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		cancel()
		t.Fatalf("GET %s: %d %q %s", url, resp.StatusCode, resp.Header.Get("Content-Type"), body)
	}
	st := &testStream{t: t, resp: resp, cancel: cancel, events: make(chan streamRead, 64)}
	go func() {
		defer close(st.events)
		sc := bufio.NewScanner(resp.Body)
		var ev streamRead
		for sc.Scan() {
			line := sc.Text()
			switch {
			case line == "":
				if ev.data != "" {
					st.events <- ev
				}
				ev = streamRead{}
			case strings.HasPrefix(line, "id: "):
				ev.id = strings.TrimPrefix(line, "id: ")
			case strings.HasPrefix(line, "data: "):
				ev.data += strings.TrimPrefix(line, "data: ")
			}
		}
	}()
	t.Cleanup(st.close)
	return st
}

func (st *testStream) next(what string) streamRead {
	st.t.Helper()
	select {
	case ev, ok := <-st.events:
		if !ok {
			st.t.Fatalf("%s: the stream ended", what)
		}
		return ev
	case <-time.After(5 * time.Second):
		st.t.Fatalf("%s: no event within 5s", what)
	}
	return streamRead{}
}

func (st *testStream) expectNone(what string, wait time.Duration) {
	st.t.Helper()
	select {
	case ev, ok := <-st.events:
		if ok {
			st.t.Fatalf("%s: unexpected event %+v", what, ev)
		}
	case <-time.After(wait):
	}
}

// expectEnd waits for the server to end the stream.
func (st *testStream) expectEnd(what string) {
	st.t.Helper()
	deadline := time.After(5 * time.Second)
	for {
		select {
		case _, ok := <-st.events:
			if !ok {
				return
			}
		case <-deadline:
			st.t.Fatalf("%s: the stream is still open", what)
		}
	}
}

func (st *testStream) close() {
	st.cancel()
	st.resp.Body.Close()
}

// waitStreams waits until this process holds n streams for the session.
func waitStreams(t *testing.T, s *Server, sessionID string, n int) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for s.sessions.hub.streamCount(SessionHash(sessionID)) != n {
		if time.Now().After(deadline) {
			t.Fatalf("session has %d streams, want %d", s.sessions.hub.streamCount(SessionHash(sessionID)), n)
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func requireListChanged(t *testing.T, ev streamRead, what string) {
	t.Helper()
	if ev.id == "" || !strings.Contains(ev.data, MethodToolsListChanged) {
		t.Fatalf("%s: event %+v, want a %s with an id", what, ev, MethodToolsListChanged)
	}
}

func requireAfter(t *testing.T, earlier, later string) {
	t.Helper()
	if less, ok := eventIDLess(earlier, later); !ok || !less {
		t.Fatalf("event id %q does not follow %q", later, earlier)
	}
}

// storedRecord reads the session's record from the store the server uses.
func storedRecord(t *testing.T, s *Server, sessionID string) (sessionRecord, bool) {
	t.Helper()
	var rec sessionRecord
	v, ok := s.sessions.sessions.Load(sessionID)
	if !ok {
		return rec, false
	}
	data, found, err := s.sessionStore().Load(context.Background(), v.(*Session).key())
	if err != nil {
		t.Fatal(err)
	}
	if !found {
		return rec, false
	}
	if err := json.Unmarshal(data, &rec); err != nil {
		t.Fatal(err)
	}
	return rec, true
}

func recordJSON(t *testing.T, s *Server, sessionID string) string {
	t.Helper()
	rec, ok := storedRecord(t, s, sessionID)
	if !ok {
		t.Fatal("no record")
	}
	data, _ := json.Marshal(rec)
	return string(data)
}

// AuthenticateKey is in the KeyGrant shape: each key is its own id, with no org and no tool limits.
func (keyPairAuth) AuthenticateKey(raw string) (KeyGrant, bool) {
	if raw != "key-a" && raw != "key-b" {
		return KeyGrant{}, false
	}
	return KeyGrant{KeyID: "id-" + raw}, true
}

func TestGetStreamEventIdsAndReplay(t *testing.T) {
	t.Run("memory", func(t *testing.T) {
		s := newSessionServer(t)
		checkStreamEventIdsAndReplay(t, s)
	})
	t.Run("redis", func(t *testing.T) {
		store, rdb, prefix := redisSessionStore(t)
		s := newSessionServer(t)
		s.SetDeps(Deps{SessionStore: store})
		id := checkStreamEventIdsAndReplay(t, s)
		// The events live in the shared stream beside the record.
		if n, err := rdb.XLen(context.Background(), prefix+"mcpev:{org-a}:"+SessionHash(id)).Result(); err != nil || n != 4 {
			t.Fatalf("XLEN of the session's event log = %d %v, want 4", n, err)
		}
	})
}

// checkStreamEventIdsAndReplay drives one server's GET streams: ids on every
// event, replay after Last-Event-ID of what a closed stream missed, and no
// replay without it or past the last id. It returns the session id.
func checkStreamEventIdsAndReplay(t *testing.T, s *Server) string {
	h := newSessionHarness(t, s)
	id, _ := h.initialize("/mcp", "a", "2025-06-18")

	st1 := h.openStream("/mcp", "a", id, "")
	s.notifyToolsChanged()
	e1 := st1.next("first event")
	requireListChanged(t, e1, "first event")
	s.notifyToolsChanged()
	e2 := st1.next("second event")
	requireListChanged(t, e2, "second event")
	requireAfter(t, e1.id, e2.id)
	st1.close()
	waitStreams(t, s, id, 0)

	// Raised while no stream is open: kept for replay.
	s.notifyToolsChanged()
	waitLogged(t, s, id, e2.id, 1)

	st2 := h.openStream("/mcp", "a", id, e1.id)
	r2 := st2.next("replayed second event")
	r3 := st2.next("replayed missed event")
	if r2.id != e2.id || r2.data != e2.data {
		t.Fatalf("replay began with %+v, want %+v", r2, e2)
	}
	requireListChanged(t, r3, "missed event")
	requireAfter(t, e2.id, r3.id)
	s.notifyToolsChanged()
	e4 := st2.next("live event after the replay")
	requireAfter(t, r3.id, e4.id)
	st2.expectNone("after the live event", 150*time.Millisecond)
	st2.close()
	waitStreams(t, s, id, 0)

	// Past the last id, or with no or a malformed id, nothing is replayed.
	for _, last := range []string{e4.id, "", "not-an-id"} {
		st := h.openStream("/mcp", "a", id, last)
		st.expectNone("replay with Last-Event-ID "+strconv.Quote(last), 150*time.Millisecond)
		st.close()
		waitStreams(t, s, id, 0)
	}
	return id
}

// waitLogged waits until the session's log holds n events after afterID.
func waitLogged(t *testing.T, s *Server, sessionID, afterID string, n int) {
	t.Helper()
	v, ok := s.sessions.sessions.Load(sessionID)
	if !ok {
		t.Fatal("no such session here")
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		got := 0
		if err := s.sessionEventsAfter(context.Background(), v.(*Session), afterID, func(string, []byte) { got++ }); err != nil {
			t.Fatal(err)
		}
		if got == n {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("%d events logged after %s, want %d", got, afterID, n)
		}
		time.Sleep(5 * time.Millisecond)
	}
}

// A second GET stream on a session has its own registration: the first
// closing leaves it receiving. With both open, a message goes to one stream
// only (never broadcast). It fails at the PIN (bug 21).
func TestSecondStreamKeepsRegistration(t *testing.T) {
	s := newSessionServer(t)
	h := newSessionHarness(t, s)
	id, _ := h.initialize("/mcp", "a", "2025-06-18")

	first := h.openStream("/mcp", "a", id, "")
	second := h.openStream("/mcp", "a", id, "")
	waitStreams(t, s, id, 2)
	first.close()
	waitStreams(t, s, id, 1)

	s.notifyToolsChanged()
	requireListChanged(t, second.next("the second stream after the first closed"), "second stream")

	third := h.openStream("/mcp", "a", id, "")
	waitStreams(t, s, id, 2)
	s.notifyToolsChanged()
	got := third.next("the newest stream")
	requireListChanged(t, got, "newest stream")
	second.expectNone("the other open stream (no broadcast)", 150*time.Millisecond)
}

// The server's WriteTimeout no longer cuts GET /mcp: the stream writes
// through SSEWriter's per-write deadline.
func TestGetStreamOutlivesServerWriteTimeout(t *testing.T) {
	s := newSessionServer(t)
	h := &sessionHarness{t: t, s: s, principals: map[string]Principal{"a": principalA}}
	h.ts = httptest.NewUnstartedServer(h.handler())
	h.ts.Config.WriteTimeout = 300 * time.Millisecond
	h.ts.Start()
	t.Cleanup(h.ts.Close)

	id, _ := h.initialize("/mcp", "a", "2025-06-18")
	st := h.openStream("/mcp", "a", id, "")
	time.Sleep(900 * time.Millisecond) // three write timeouts
	s.notifyToolsChanged()
	requireListChanged(t, st.next("an event after the server's WriteTimeout"), "late event")
}

// redisSessionStore is an MCP session store on the test Redis
// (TEST_REDIS_ADDR; TEST_REDIS_DB, else this lane's DB 5) under the prefix
// test:M12:<test>:<nonce>:, whose keys are deleted when the test ends (SCAN
// and DEL, never FLUSHDB). It skips without Redis.
func redisSessionStore(t *testing.T) (*redisstate.MCPSessionStore, redis.UniversalClient, string) {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("skipping: TEST_REDIS_ADDR is not set")
	}
	db := 5
	if v := os.Getenv("TEST_REDIS_DB"); v != "" {
		db, _ = strconv.Atoi(v)
	}
	client, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: db, Timeout: time.Second})
	if err != nil {
		t.Skipf("skipping: Redis not available at %s: %v", addr, err)
	}
	prefix := fmt.Sprintf("test:M12:%s:%d:", t.Name(), time.Now().UnixNano())
	rdb := client.Redis()
	t.Cleanup(func() {
		ctx := context.Background()
		iter := rdb.Scan(ctx, 0, prefix+"*", 1000).Iterator()
		var keys []string
		for iter.Next(ctx) {
			keys = append(keys, iter.Val())
		}
		if len(keys) > 0 {
			rdb.Del(ctx, keys...)
		}
		client.Close()
	})
	return redisstate.NewMCPSessionStore(client, prefix), rdb, prefix
}
