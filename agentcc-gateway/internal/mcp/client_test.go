package mcp

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
	"github.com/futureagi/agentcc-gateway/internal/mcp/upstream/wire"
)

// TestStdioChildSurvivesConnectCancel: the connect context bounds the
// handshake only. Cancelling it, as the static pool does right after Connect,
// leaves the child running and callable.
func TestStdioChildSurvivesConnectCancel(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "marker")
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	c := connectStdioHelperCtx(t, ctx, ClientConfig{ServerID: "survive-connect"}, "--marker", marker)
	cancel() // as the static pool does right after Connect

	time.Sleep(300 * time.Millisecond)
	text, err := callText(t, c, "echo", map[string]interface{}{"text": "still here"})
	if err != nil {
		t.Fatalf("call after the connect context ended: %v (child marker: %v)", err, readMarker(marker))
	}
	if text != "still here" {
		t.Fatalf("echo = %q", text)
	}
	if !c.Healthy() {
		t.Fatal("client unhealthy after a successful call")
	}
	if got := readMarker(marker); len(got) != 1 || !strings.HasPrefix(got[0], "start") {
		t.Fatalf("child lifecycle = %v, want only its start", got)
	}
}

// stdioPid is the pid of the client's current stdio child.
func stdioPid(t *testing.T, c *Client) int {
	t.Helper()
	st, ok := c.currentTransport().(*StdioTransport)
	if !ok || st.proc == nil {
		t.Fatalf("client %s has no stdio transport", c.ServerID())
	}
	return st.proc.Pid()
}

// waitReconnected waits until the client is healthy on a child other than old,
// and returns the new child's pid.
func waitReconnected(t *testing.T, c *Client, old int, within time.Duration) int {
	t.Helper()
	for deadline := time.Now().Add(within); time.Now().Before(deadline); time.Sleep(20 * time.Millisecond) {
		if !c.Healthy() {
			continue
		}
		if st, ok := c.currentTransport().(*StdioTransport); ok && st.proc != nil && st.proc.Pid() != old {
			return st.proc.Pid()
		}
	}
	t.Fatalf("client %s did not reconnect to a new child within %s", c.ServerID(), within)
	return 0
}

// processGone reports whether pid has exited (and been reaped) within d.
func processGone(pid int, d time.Duration) bool {
	for deadline := time.Now().Add(d); time.Now().Before(deadline); time.Sleep(20 * time.Millisecond) {
		if err := syscall.Kill(pid, 0); errors.Is(err, syscall.ESRCH) {
			return true
		}
	}
	return false
}

// TestStdioReconnectSurvives: after the child dies, reconnect starts a new one
// under a 30 s handshake context; the new child outlives that context.
func TestStdioReconnectSurvives(t *testing.T) {
	c := connectStdioHelper(t, ClientConfig{ServerID: "reconnect"})
	first := stdioPid(t, c)
	if err := syscall.Kill(first, syscall.SIGKILL); err != nil {
		t.Fatalf("kill the child: %v", err)
	}

	second := waitReconnected(t, c, first, 15*time.Second)
	time.Sleep(500 * time.Millisecond) // the reconnect's handshake context has ended
	text, err := callText(t, c, "pid", nil)
	if err != nil {
		t.Fatalf("call after the reconnect: %v", err)
	}
	if text != strconv.Itoa(second) {
		t.Fatalf("answered by pid %s, want the reconnected child %d", text, second)
	}
	if processGone(second, 200*time.Millisecond) {
		t.Fatal("the reconnected child died after its handshake")
	}
}

// TestStdioOversizeFailsPendingAndReconnects: a message over the limit fails
// every pending call with upstream_message_too_large, marks the client
// unhealthy, and reconnect restarts the child.
func TestStdioOversizeFailsPendingAndReconnects(t *testing.T) {
	c := connectStdioHelper(t, ClientConfig{ServerID: "oversize", MaxMessageBytes: 64 << 10})
	first := stdioPid(t, c)

	slowErr := make(chan error, 1)
	go func() {
		_, err := callText(t, c, "echo", map[string]interface{}{"text": "slow", "delay_ms": 3000})
		slowErr <- err
	}()
	time.Sleep(200 * time.Millisecond) // the slow call is pending

	_, err := callText(t, c, "big", map[string]interface{}{"bytes": 128 << 10})
	if !errors.Is(err, ErrUpstreamMessageTooLarge) || !strings.Contains(err.Error(), "upstream_message_too_large") {
		t.Fatalf("oversize call error = %v, want upstream_message_too_large", err)
	}
	if c.Healthy() {
		t.Fatal("client still healthy after an oversize message")
	}
	select {
	case err := <-slowErr:
		if !errors.Is(err, ErrUpstreamMessageTooLarge) {
			t.Fatalf("pending call error = %v, want upstream_message_too_large", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("the pending call was not failed by the oversize message")
	}

	waitReconnected(t, c, first, 15*time.Second)
	if text, err := callText(t, c, "echo", map[string]interface{}{"text": "after"}); err != nil || text != "after" {
		t.Fatalf("call after the restart = %q, %v", text, err)
	}
	if !processGone(first, 5*time.Second) {
		t.Fatal("the oversize child is still running after the restart")
	}
}

// TestStdioConcurrentIdsNoCrossTalk: concurrent calls each get their own
// result, and the transport refuses a request whose id is already pending.
func TestStdioConcurrentIdsNoCrossTalk(t *testing.T) {
	c := connectStdioHelper(t, ClientConfig{ServerID: "ids"})

	const calls = 64
	var wg sync.WaitGroup
	errs := make(chan error, calls)
	for i := 0; i < calls; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			want := "call-" + strconv.Itoa(i)
			// Varied delays make the child answer out of order.
			got, err := callText(t, c, "echo", map[string]interface{}{"text": want, "delay_ms": (i * 7) % 40})
			if err != nil || got != want {
				errs <- fmt.Errorf("call %d got %q, %v", i, got, err)
			}
		}(i)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Error(err)
	}

	tr := c.currentTransport().(*StdioTransport)
	params, _ := json.Marshal(ToolCallParams{Name: "echo", Arguments: map[string]interface{}{"text": "first", "delay_ms": 2000}})
	msg := func() *Message {
		return &Message{JSONRPC: "2.0", ID: json.RawMessage(`"dup"`), Method: MethodToolsCall, Params: params}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	first := make(chan *Message, 1)
	go func() {
		resp, _ := tr.Send(ctx, msg())
		first <- resp
	}()
	for deadline, pending := time.Now().Add(5*time.Second), false; !pending; time.Sleep(5 * time.Millisecond) {
		if time.Now().After(deadline) {
			t.Fatal("the first request never became pending")
		}
		tr.pendMu.Lock()
		_, pending = tr.pending[`"dup"`]
		tr.pendMu.Unlock()
	}
	if _, err := tr.Send(ctx, msg()); !errors.Is(err, ErrDuplicateRequestID) {
		t.Fatalf("second send with a pending id: err = %v, want ErrDuplicateRequestID", err)
	}
	resp := <-first
	var res ToolCallResult
	if resp == nil || json.Unmarshal(resp.Result, &res) != nil || len(res.Content) != 1 || res.Content[0].Text != "first" {
		t.Fatalf("the first request lost its answer: %+v", resp)
	}
}

// TestStdioConnectFailureClosesChild: a handshake that fails leaves no child
// running.
func TestStdioConnectFailureClosesChild(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "marker")
	command, args := stdioHelperCommand("--marker", marker, "--fail-initialize")
	c := NewClient(ClientConfig{ServerID: "bad-init", TransportType: "stdio", Command: command, Args: args})
	defer c.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := c.Connect(ctx); err == nil || !strings.Contains(err.Error(), "initialize refused") {
		t.Fatalf("connect error = %v, want the initialize error", err)
	}
	lines := readMarker(marker)
	if len(lines) == 0 || lines[len(lines)-1] != "graceful-exit" {
		t.Fatalf("child lifecycle after a failed connect = %v, want it shut down", lines)
	}
}

// TestStdioCloseStopsReconnect: Close during a reconnect's backoff starts no
// new child.
func TestStdioCloseStopsReconnect(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "marker")
	c := connectStdioHelper(t, ClientConfig{ServerID: "close-reconnect"}, "--marker", marker)
	syscall.Kill(stdioPid(t, c), syscall.SIGKILL)
	for deadline := time.Now().Add(5 * time.Second); c.Healthy(); time.Sleep(10 * time.Millisecond) {
		if time.Now().After(deadline) {
			t.Fatal("the client never saw its child exit")
		}
	}
	c.Close()                           // during the reconnect's first backoff
	time.Sleep(1500 * time.Millisecond) // past the first backoff
	starts := 0
	for _, l := range readMarker(marker) {
		if strings.HasPrefix(l, "start") {
			starts++
		}
	}
	if starts != 1 {
		t.Fatalf("%d children started, want 1 (no reconnect after Close)", starts)
	}
}

// TestStdioCloseAbortsConnect: Close during a handshake that would otherwise
// wait for its context ends the handshake at once and shuts the child down.
func TestStdioCloseAbortsConnect(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "marker")
	command, args := stdioHelperCommand("--marker", marker, "--silent")
	c := NewClient(ClientConfig{ServerID: "close-connect", TransportType: "stdio", Command: command, Args: args})
	done := make(chan error, 1)
	go func() { done <- c.Connect(context.Background()) }()
	waitMarker(t, marker, "start")

	c.Close()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("Connect succeeded against a server that never answers")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("Close did not abort the handshake")
	}
	lines := readMarker(marker)
	if len(lines) == 0 || lines[len(lines)-1] != "graceful-exit" {
		t.Fatalf("child lifecycle after the aborted connect = %v, want it shut down", lines)
	}
}

// TestStdioReconnectBackoffPersists: a child that dies right after every
// handshake (a crash loop) is reconnected with a backoff that keeps growing
// across reconnects, instead of restarting at the base delay after each
// short-lived success.
func TestStdioReconnectBackoffPersists(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "marker")
	cfg := ClientConfig{ServerID: "crash-loop", TransportType: "stdio"}
	cfg.Command, cfg.Args = stdioHelperCommand("--marker", marker, "--exit-after-handshake")
	c := NewClient(cfg)
	c.reconnectBase = 200 * time.Millisecond
	t.Cleanup(func() { c.Close() })
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := c.Connect(ctx); err != nil {
		t.Fatalf("connect: %v", err)
	}
	var starts []int64
	for deadline := time.Now().Add(15 * time.Second); time.Now().Before(deadline) && len(starts) < 4; time.Sleep(50 * time.Millisecond) {
		starts = starts[:0]
		for _, l := range readMarker(marker) {
			if f := strings.Split(l, "_"); f[0] == "start" && len(f) == 3 {
				ms, _ := strconv.ParseInt(f[2], 10, 64)
				starts = append(starts, ms)
			}
		}
	}
	if len(starts) < 4 {
		t.Fatalf("%d child starts in 15 s; the crash loop was not reconnected", len(starts))
	}
	// Delays 200, 400 and 800 ms: the third gap is about 600 ms longer than
	// the first. A backoff that restarts at 200 ms keeps the gaps equal.
	first, third := starts[1]-starts[0], starts[3]-starts[2]
	if third-first < 400 {
		t.Errorf("gaps between child starts %d ms then %d ms: the backoff did not grow across reconnects", first, third)
	}
}

// --- M13: the upstream client against a fake upstream ----------------------------

// fakeUpstream is an MCP upstream for the client's tests (plan M13): one
// endpoint at a configurable path, either era, JSON or SSE answers, legacy
// sessions with a GET stream, 2026 subscriptions/listen, pagination, and a
// record of every request it was sent.
type fakeUpstream struct {
	*httptest.Server
	t    *testing.T
	path string

	mu         sync.Mutex
	era2026    bool   // stateless 2026-07-28; else legacy sessions
	version    string // the revision a legacy initialize gets (default ProtocolVersion)
	refuse2026 bool   // answer 2026 _meta requests 400 -32022 and discover as legacy
	sse        bool   // answer POSTs as SSE streams
	pageSize   int
	tools      []json.RawMessage
	sessions   map[string]bool
	nextSID    int
	streams    map[chan []byte]bool
	received   []fakeRequest
	// onCall answers a tools/call itself when set: it returns the result or
	// the error to answer with, and may write extra events first on a stream
	// (send is nil on a JSON answer).
	onCall func(req fakeRequest, send func(msg any)) (result any, rpcErr *RPCError)
	// wrap, when set, wraps the fake's handler (a test that answers some
	// requests itself).
	wrap func(next http.Handler) http.Handler
}

// fakeRequest is one request the fake was sent.
type fakeRequest struct {
	HTTPMethod string
	Path       string
	Method     string
	ID         json.RawMessage
	Params     json.RawMessage
	Header     http.Header
}

// fakeTool is a tool definition with a schema the gateway accepts.
func fakeTool(name string) json.RawMessage {
	return json.RawMessage(fmt.Sprintf(`{"name":%q,"description":"fake %s","inputSchema":{"type":"object"}}`, name, name))
}

func fakeTools(n int) []json.RawMessage {
	out := make([]json.RawMessage, n)
	for i := range out {
		out[i] = fakeTool(fmt.Sprintf("t%03d", i))
	}
	return out
}

func newFakeUpstream(t *testing.T, path string, configure func(f *fakeUpstream)) *fakeUpstream {
	t.Helper()
	f := &fakeUpstream{t: t, path: path, version: ProtocolVersion, tools: []json.RawMessage{fakeTool("echo")},
		sessions: map[string]bool{}, streams: map[chan []byte]bool{}}
	if configure != nil {
		configure(f)
	}
	var h http.Handler = http.HandlerFunc(f.serve)
	if f.wrap != nil {
		h = f.wrap(h)
	}
	f.Server = httptest.NewServer(h)
	t.Cleanup(func() {
		f.closeStreams()
		f.Server.Close()
	})
	return f
}

// URLPath is the fake's endpoint URL.
func (f *fakeUpstream) URLPath() string { return f.URL + f.path }

func (f *fakeUpstream) set(fn func(f *fakeUpstream)) {
	f.mu.Lock()
	defer f.mu.Unlock()
	fn(f)
}

// requests returns what the fake was sent, optionally only one method.
func (f *fakeUpstream) requests(method string) []fakeRequest {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []fakeRequest
	for _, r := range f.received {
		if method == "" || r.Method == method {
			out = append(out, r)
		}
	}
	return out
}

// dropSessions forgets every legacy session, as an upstream restart does.
func (f *fakeUpstream) dropSessions() {
	f.mu.Lock()
	f.sessions = map[string]bool{}
	f.mu.Unlock()
	f.closeStreams()
}

func (f *fakeUpstream) closeStreams() {
	f.mu.Lock()
	defer f.mu.Unlock()
	for ch := range f.streams {
		close(ch)
		delete(f.streams, ch)
	}
}

// broadcast sends a notification on every open stream.
func (f *fakeUpstream) broadcast(method string) {
	data, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "method": method, "params": map[string]any{}})
	f.mu.Lock()
	defer f.mu.Unlock()
	for ch := range f.streams {
		select {
		case ch <- data:
		default:
		}
	}
}

// waitStreams waits until n notification streams are open.
func (f *fakeUpstream) waitStreams(t *testing.T, n int) {
	t.Helper()
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(10 * time.Millisecond) {
		f.mu.Lock()
		got := len(f.streams)
		f.mu.Unlock()
		if got >= n {
			return
		}
	}
	t.Fatalf("no notification stream opened")
}

func (f *fakeUpstream) serve(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	var msg Message
	_ = json.Unmarshal(body, &msg)
	req := fakeRequest{HTTPMethod: r.Method, Path: r.URL.Path, Method: msg.Method, ID: msg.ID, Params: msg.Params, Header: r.Header.Clone()}
	f.mu.Lock()
	f.received = append(f.received, req)
	era2026, refuse, version := f.era2026, f.refuse2026, f.version
	f.mu.Unlock()
	if r.URL.Path != f.path {
		http.NotFound(w, r)
		return
	}
	if r.Method == http.MethodGet {
		if era2026 || !f.validSession(r) {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		f.stream(w, r)
		return
	}
	if msg.IsNotification() || msg.IsResponse() {
		w.WriteHeader(http.StatusAccepted)
		return
	}
	has2026Meta := strings.Contains(string(msg.Params), `"io.modelcontextprotocol/protocolVersion"`)
	switch {
	case msg.Method == MethodServerDiscover && era2026 && !refuse:
		f.answer(w, r, req, map[string]any{"supportedVersions": []string{"2026-07-28"}, "capabilities": map[string]any{"tools": map[string]any{"listChanged": true}}}, nil)
		return
	case msg.Method == MethodServerDiscover:
		writeJSON(w, http.StatusBadRequest, NewErrorResponse(msg.ID, ErrCodeInvalidRequest, "Bad Request: No valid session ID provided"))
		return
	case era2026 && refuse && has2026Meta:
		e := NewErrorResponse(msg.ID, -32022, "unsupported protocol version")
		e.Error.Data = json.RawMessage(`{"supported":["2025-11-25"]}`)
		writeJSON(w, http.StatusBadRequest, e)
		return
	case era2026 && !refuse:
		if msg.Method == MethodSubscriptionsListen {
			f.stream(w, r)
			return
		}
	case msg.Method == MethodInitialize:
		f.mu.Lock()
		f.nextSID++
		sid := "sid-" + strconv.Itoa(f.nextSID)
		f.sessions[sid] = true
		f.mu.Unlock()
		w.Header().Set("Mcp-Session-Id", sid)
		f.answer(w, r, req, map[string]any{"protocolVersion": version, "capabilities": map[string]any{"tools": map[string]any{"listChanged": true}},
			"serverInfo": map[string]any{"name": "fake", "version": "1"}}, nil)
		return
	case r.Header.Get("Mcp-Session-Id") != "" && !f.validSession(r):
		writeJSON(w, http.StatusNotFound, NewErrorResponse(msg.ID, -32001, "session not found"))
		return
	}
	f.serveMethod(w, r, req)
}

func (f *fakeUpstream) validSession(r *http.Request) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.sessions[r.Header.Get("Mcp-Session-Id")]
}

func (f *fakeUpstream) serveMethod(w http.ResponseWriter, r *http.Request, req fakeRequest) {
	switch req.Method {
	case MethodToolsList:
		var p struct {
			Cursor string `json:"cursor"`
		}
		_ = json.Unmarshal(req.Params, &p)
		f.mu.Lock()
		tools, size := append([]json.RawMessage(nil), f.tools...), f.pageSize
		f.mu.Unlock()
		start, _ := strconv.Atoi(p.Cursor)
		end := len(tools)
		if size > 0 && start+size < end {
			end = start + size
		}
		if start > end {
			start = end
		}
		res := map[string]any{"tools": tools[start:end]}
		if end < len(tools) {
			res["nextCursor"] = strconv.Itoa(end)
		}
		f.answer(w, r, req, res, nil)
	case MethodToolsCall:
		f.mu.Lock()
		onCall := f.onCall
		f.mu.Unlock()
		if onCall != nil {
			f.answerCall(w, r, req, onCall)
			return
		}
		var p struct {
			Name string `json:"name"`
		}
		_ = json.Unmarshal(req.Params, &p)
		f.answer(w, r, req, map[string]any{"content": []any{map[string]any{"type": "text", "text": "called " + p.Name}}, "structuredContent": map[string]any{"ok": true}}, nil)
	case MethodResourcesList:
		f.answer(w, r, req, map[string]any{"resources": []any{}}, nil)
	case MethodPromptsList:
		f.answer(w, r, req, map[string]any{"prompts": []any{}}, nil)
	case MethodPing, MethodServerDiscover:
		f.answer(w, r, req, map[string]any{}, nil)
	default:
		f.answer(w, r, req, nil, &RPCError{Code: ErrCodeMethodNotFound, Message: "unknown method"})
	}
}

// answerCall runs onCall, on an SSE stream when the fake answers in SSE.
func (f *fakeUpstream) answerCall(w http.ResponseWriter, r *http.Request, req fakeRequest, onCall func(fakeRequest, func(any)) (any, *RPCError)) {
	f.mu.Lock()
	sse := f.sse
	f.mu.Unlock()
	if !sse {
		result, rerr := onCall(req, nil)
		f.answer(w, r, req, result, rerr)
		return
	}
	sw := startSSE(w)
	result, rerr := onCall(req, sw.send)
	sw.send(responseOf(req.ID, result, rerr))
}

func responseOf(id json.RawMessage, result any, rerr *RPCError) *Message {
	if rerr != nil {
		return &Message{JSONRPC: "2.0", ID: id, Error: rerr}
	}
	m, _ := NewResponse(id, result)
	return m
}

// answer writes a result or error as JSON or, when the fake answers in SSE,
// as a stream with one unrelated notification and a message for another id
// before the answer.
func (f *fakeUpstream) answer(w http.ResponseWriter, r *http.Request, req fakeRequest, result any, rerr *RPCError) {
	f.mu.Lock()
	sse := f.sse
	f.mu.Unlock()
	resp := responseOf(req.ID, result, rerr)
	if !sse {
		writeJSON(w, http.StatusOK, resp)
		return
	}
	sw := startSSE(w)
	sw.send(map[string]any{"jsonrpc": "2.0", "method": "notifications/message", "params": map[string]any{"level": "info", "data": "x"}})
	sw.send(map[string]any{"jsonrpc": "2.0", "id": 987654, "result": map[string]any{}})
	sw.send(resp)
}

// stream holds a notification stream open until the client leaves or the
// fake closes it.
func (f *fakeUpstream) stream(w http.ResponseWriter, r *http.Request) {
	ch := make(chan []byte, 16)
	f.mu.Lock()
	f.streams[ch] = true
	f.mu.Unlock()
	defer func() {
		f.mu.Lock()
		delete(f.streams, ch)
		f.mu.Unlock()
	}()
	sw := startSSE(w)
	for {
		select {
		case data, ok := <-ch:
			if !ok {
				return
			}
			sw.raw(data)
		case <-r.Context().Done():
			return
		}
	}
}

type sseTestWriter struct {
	mu sync.Mutex
	w  http.ResponseWriter
}

func startSSE(w http.ResponseWriter) *sseTestWriter {
	w.Header().Set("Content-Type", "text/event-stream")
	w.WriteHeader(http.StatusOK)
	if fl, ok := w.(http.Flusher); ok {
		fl.Flush()
	}
	return &sseTestWriter{w: w}
}

func (s *sseTestWriter) send(msg any) {
	data, _ := json.Marshal(msg)
	s.raw(data)
}

func (s *sseTestWriter) raw(data []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	fmt.Fprintf(s.w, "event: message\ndata: %s\n\n", data)
	if fl, ok := s.w.(http.Flusher); ok {
		fl.Flush()
	}
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// connectFake connects a client to the fake; the client closes with the test.
func connectFake(t *testing.T, f *fakeUpstream, cfg ClientConfig) *Client {
	t.Helper()
	if cfg.ServerID == "" {
		cfg.ServerID = "fake"
	}
	if cfg.URL == "" {
		cfg.URL = f.URLPath()
	}
	c := NewClient(cfg)
	t.Cleanup(func() { c.Close() })
	ctx, cancel := contextWithTimeout(10 * time.Second)
	defer cancel()
	if err := c.Connect(ctx); err != nil {
		t.Fatalf("connect: %v", err)
	}
	return c
}

func contextWithTimeout(d time.Duration) (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), d)
}

// clientToolNames are the names of the client's cached tools (renamed at the
// W3b merge: M10's hook_acl.go declares type toolNames in this package).
func clientToolNames(c *Client) []string {
	var names []string
	for _, t := range c.Tools() {
		names = append(names, t.Name)
	}
	return names
}

// TestUpstreamExactURL: every request goes to the configured URL exactly,
// never <url>/mcp (MCA C32); the fake answers only on its own path.
func TestUpstreamExactURL(t *testing.T) {
	f := newFakeUpstream(t, "/tools/rpc", nil)
	c := connectFake(t, f, ClientConfig{})
	if got := clientToolNames(c); len(got) != 1 || got[0] != "echo" {
		t.Fatalf("tools = %v, want [echo]", got)
	}
	ctx, cancel := contextWithTimeout(5 * time.Second)
	defer cancel()
	if _, err := c.CallToolRaw(ctx, "echo", json.RawMessage(`{}`), CallOptions{}); err != nil {
		t.Fatalf("call: %v", err)
	}
	for _, r := range f.requests("") {
		if r.Path != "/tools/rpc" {
			t.Errorf("%s %s went to %s, want /tools/rpc exactly", r.HTTPMethod, r.Method, r.Path)
		}
	}
}

// TestUpstreamAcceptBothAndSSE: every POST accepts JSON and SSE, and an SSE
// answer is read to the message for the request's own id, past an unrelated
// notification and a message for another id.
func TestUpstreamAcceptBothAndSSE(t *testing.T) {
	f := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) { f.sse = true; f.tools = fakeTools(3) })
	c := connectFake(t, f, ClientConfig{})
	if got := c.ToolCount(); got != 3 {
		t.Fatalf("tools over SSE = %d, want 3", got)
	}
	ctx, cancel := contextWithTimeout(5 * time.Second)
	defer cancel()
	raw, err := c.CallToolRaw(ctx, "t001", nil, CallOptions{})
	if err != nil || !strings.Contains(string(raw), `"structuredContent":{"ok":true}`) {
		t.Fatalf("call over SSE: %s, %v", raw, err)
	}
	for _, r := range f.requests("") {
		if r.HTTPMethod != http.MethodPost {
			continue
		}
		if a := r.Header.Get("Accept"); !strings.Contains(a, "application/json") || !strings.Contains(a, "text/event-stream") {
			t.Errorf("%s sent Accept %q, want both application/json and text/event-stream", r.Method, a)
		}
	}
}

// TestUpstreamProtocolVersionHeader: after negotiation every request carries
// MCP-Protocol-Version with the negotiated revision (the server's choice),
// the initialize itself none; on the 2026 era every request carries
// 2026-07-28.
func TestUpstreamProtocolVersionHeader(t *testing.T) {
	legacy := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) { f.version = "2025-06-18" })
	c := connectFake(t, legacy, ClientConfig{})
	if c.Era() != "2025-06-18" {
		t.Fatalf("era = %q, want the server's 2025-06-18", c.Era())
	}
	ctx, cancel := contextWithTimeout(5 * time.Second)
	defer cancel()
	if _, err := c.CallToolRaw(ctx, "echo", nil, CallOptions{}); err != nil {
		t.Fatal(err)
	}
	afterInit := false
	for _, r := range legacy.requests("") {
		switch {
		case r.Method == MethodInitialize:
			if v := r.Header.Get("MCP-Protocol-Version"); v != "" {
				t.Errorf("initialize sent MCP-Protocol-Version %q", v)
			}
			afterInit = true
		case afterInit:
			if v := r.Header.Get("MCP-Protocol-Version"); v != "2025-06-18" {
				t.Errorf("%s %s sent MCP-Protocol-Version %q, want 2025-06-18", r.HTTPMethod, r.Method, v)
			}
		}
	}

	stateless := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) { f.era2026 = true })
	c2 := connectFake(t, stateless, ClientConfig{})
	if _, err := c2.CallToolRaw(ctx, "echo", nil, CallOptions{}); err != nil {
		t.Fatal(err)
	}
	for _, r := range stateless.requests("") {
		if v := r.Header.Get("MCP-Protocol-Version"); r.HTTPMethod == http.MethodPost && v != "2026-07-28" {
			t.Errorf("2026 %s sent MCP-Protocol-Version %q", r.Method, v)
		}
	}
}

// slowCall makes the fake's tools/call take d.
func slowCall(d time.Duration) func(fakeRequest, func(any)) (any, *RPCError) {
	return func(fakeRequest, func(any)) (any, *RPCError) {
		time.Sleep(d)
		return map[string]any{"content": []any{map[string]any{"type": "text", "text": "slow ok"}}}, nil
	}
}

// TestUpstreamTimeoutFromServerConfig: a call is bounded by the server's
// timeout_ms, else by the caller's tool_call_timeout, and always by the
// caller's deadline; no fixed client timeout cuts a long call.
func TestUpstreamTimeoutFromServerConfig(t *testing.T) {
	f := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) { f.onCall = slowCall(300 * time.Millisecond) })
	call := func(server, caller, deadline time.Duration) error {
		c := connectFake(t, f, ClientConfig{Call: CallSettings{Timeout: server}})
		ctx, cancel := contextWithTimeout(deadline)
		defer cancel()
		_, err := c.CallToolRaw(ctx, "echo", nil, CallOptions{Timeout: caller})
		return err
	}
	if err := call(100*time.Millisecond, 5*time.Second, 10*time.Second); !errors.Is(err, context.DeadlineExceeded) {
		t.Errorf("timeout_ms 100 ms on a 300 ms tool: %v, want a timeout", err)
	}
	if err := call(2*time.Second, 100*time.Millisecond, 10*time.Second); err != nil {
		t.Errorf("timeout_ms 2 s over a 100 ms tool_call_timeout: %v, want the server's timeout to win", err)
	}
	if err := call(0, 100*time.Millisecond, 10*time.Second); !errors.Is(err, context.DeadlineExceeded) {
		t.Errorf("no timeout_ms, tool_call_timeout 100 ms: %v, want a timeout", err)
	}
	if err := call(2*time.Second, 0, 100*time.Millisecond); !errors.Is(err, context.DeadlineExceeded) {
		t.Errorf("a 100 ms caller deadline: %v, want the deadline to bound the call", err)
	}
	g, _ := egress.New(egress.Options{PrivateAllow: []string{"127.0.0.1"}})
	for name, d := range map[string]Dialer{"plain": nil, "egress": g} {
		hc := upstreamHTTPClient(d, AuthConfig{})
		tr, _ := hc.Transport.(*http.Transport)
		if hc.Timeout != 0 || tr == nil || tr.ResponseHeaderTimeout != 0 {
			t.Errorf("%s client: Timeout %s, ResponseHeaderTimeout set: a fixed bound would cut a long tool", name, hc.Timeout)
		}
	}
}

// TestEraDetectionFallback: server/discover with the 2026 _meta first; any
// answer that is not 2026-shaped falls back to initialize with the newest
// 2025 revision; a configured era skips detection; -32022 re-probes; and
// transport sse speaks 2024-11-05.
func TestEraDetectionFallback(t *testing.T) {
	stateless := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) { f.era2026 = true })
	c := connectFake(t, stateless, ClientConfig{})
	if c.Era() != "2026-07-28" || len(stateless.requests(MethodInitialize)) != 0 {
		t.Fatalf("2026 server: era %q, %d initializes", c.Era(), len(stateless.requests(MethodInitialize)))
	}
	d := stateless.requests(MethodServerDiscover)[0]
	if meta := wire.MetaOf(d.Params); string(meta[wire.MetaProtocolVersion]) != `"2026-07-28"` || meta[wire.MetaClientCaps] == nil {
		t.Errorf("server/discover _meta = %s", d.Params)
	}
	for _, r := range stateless.requests(MethodToolsList) {
		if !strings.Contains(string(r.Params), wire.MetaProtocolVersion) || r.Header.Get("Mcp-Method") != MethodToolsList {
			t.Errorf("2026 tools/list without its _meta or Mcp-Method: %s %v", r.Params, r.Header)
		}
	}

	// Legacy servers answer discover in many ways; none is 2026-shaped.
	for name, answer := range map[string]func(http.ResponseWriter, *Message){
		"400 no session": func(w http.ResponseWriter, m *Message) {
			writeJSON(w, 400, NewErrorResponse(m.ID, -32600, "no session"))
		},
		"-32601": func(w http.ResponseWriter, m *Message) { writeJSON(w, 200, NewErrorResponse(m.ID, -32601, "unknown")) },
		"empty result": func(w http.ResponseWriter, m *Message) {
			r, _ := NewResponse(m.ID, map[string]any{})
			writeJSON(w, 200, r)
		},
		"405": func(w http.ResponseWriter, _ *Message) { w.WriteHeader(405) },
		"not JSON": func(w http.ResponseWriter, _ *Message) {
			w.Header().Set("Content-Type", "text/html")
			w.Write([]byte("<html>"))
		},
		"older versions": func(w http.ResponseWriter, m *Message) {
			r, _ := NewResponse(m.ID, map[string]any{"supportedVersions": []string{"2025-06-18"}})
			writeJSON(w, 200, r)
		},
	} {
		t.Run(name, func(t *testing.T) {
			legacy := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) {
				f.wrap = func(next http.Handler) http.Handler {
					return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						if r.Method == http.MethodPost {
							var m Message
							body, _ := readBody(r)
							if json.Unmarshal(body, &m) == nil && m.Method == MethodServerDiscover {
								answer(w, &m)
								return
							}
						}
						next.ServeHTTP(w, r)
					})
				}
			})
			lc := connectFake(t, legacy, ClientConfig{})
			init := legacy.requests(MethodInitialize)
			if lc.Era() != ProtocolVersion || len(init) != 1 || !strings.Contains(string(init[0].Params), ProtocolVersion) {
				t.Errorf("era %q, initializes %d, want initialize with %s", lc.Era(), len(init), ProtocolVersion)
			}
		})
	}

	pinned := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) { f.version = "2025-06-18" })
	if pc := connectFake(t, pinned, ClientConfig{Era: "2025-06-18"}); pc.Era() != "2025-06-18" || len(pinned.requests(MethodServerDiscover)) != 0 {
		t.Errorf("configured era: %q, %d discovers", pc.Era(), len(pinned.requests(MethodServerDiscover)))
	}
	pinned26 := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) { f.era2026 = true })
	if pc := connectFake(t, pinned26, ClientConfig{Era: "2026-07-28"}); pc.Era() != "2026-07-28" || len(pinned26.requests(MethodServerDiscover)) != 0 {
		t.Errorf("configured 2026: %q, %d discovers", pc.Era(), len(pinned26.requests(MethodServerDiscover)))
	}

	// The server stops accepting 2026: the next call gets 400 -32022, the
	// client detects again, initializes and the call succeeds.
	switching := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) { f.era2026 = true })
	sc := connectFake(t, switching, ClientConfig{})
	switching.set(func(f *fakeUpstream) { f.refuse2026 = true })
	ctx, cancel := contextWithTimeout(5 * time.Second)
	defer cancel()
	if _, err := sc.CallToolRaw(ctx, "echo", nil, CallOptions{}); err != nil {
		t.Fatalf("call after the server dropped 2026: %v", err)
	}
	if sc.Era() != ProtocolVersion || len(switching.requests(MethodInitialize)) != 1 {
		t.Errorf("after -32022: era %q, %d initializes", sc.Era(), len(switching.requests(MethodInitialize)))
	}
}

func readBody(r *http.Request) ([]byte, error) {
	var b strings.Builder
	buf := make([]byte, 4096)
	for {
		n, err := r.Body.Read(buf)
		b.Write(buf[:n])
		if err != nil {
			break
		}
	}
	data := []byte(b.String())
	r.Body = ioNopCloser(data)
	return data, nil
}

// TestLegacy404Reinitializes: a 404 on a legacy session (the upstream forgot
// it) re-initializes once and retries the request once: the caller sees
// success and the tool runs once. A 404 that persists is retried once only.
func TestLegacy404Reinitializes(t *testing.T) {
	f := newFakeUpstream(t, "/mcp", nil)
	c := connectFake(t, f, ClientConfig{})
	f.dropSessions()
	ctx, cancel := contextWithTimeout(5 * time.Second)
	defer cancel()
	if _, err := c.CallToolRaw(ctx, "echo", nil, CallOptions{}); err != nil {
		t.Fatalf("call after the session was dropped: %v", err)
	}
	calls := f.requests(MethodToolsCall)
	if len(f.requests(MethodInitialize)) != 2 || len(calls) != 2 {
		t.Fatalf("%d initializes and %d tools/call sent, want 2 and 2 (one refused at the session check)", len(f.requests(MethodInitialize)), len(calls))
	}
	if calls[0].Header.Get("Mcp-Session-Id") == calls[1].Header.Get("Mcp-Session-Id") {
		t.Error("the retry reused the dropped session")
	}

	// Every session is refused: one re-initialize, one retry, then the error.
	f2 := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) {
		f.wrap = func(next http.Handler) http.Handler {
			return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				data, _ := readBody(r)
				if strings.Contains(string(data), `"tools/call"`) {
					next.ServeHTTP(&discard{ResponseWriter: w}, r) // recorded, not answered
					writeJSON(w, 404, NewErrorResponse(nil, -32001, "session not found"))
					return
				}
				next.ServeHTTP(w, r)
			})
		}
	})
	c2 := connectFake(t, f2, ClientConfig{})
	if _, err := c2.CallToolRaw(ctx, "echo", nil, CallOptions{}); !errors.Is(err, errSessionNotFound) {
		t.Fatalf("a 404 that persists: %v, want session not found", err)
	}
	if n := len(f2.requests(MethodToolsCall)); n != 2 {
		t.Errorf("%d tools/call sent, want the call and one retry", n)
	}
}

// discard records nothing a wrapped handler writes.
type discard struct{ http.ResponseWriter }

func (d *discard) Header() http.Header         { return http.Header{} }
func (d *discard) Write(b []byte) (int, error) { return len(b), nil }
func (d *discard) WriteHeader(int)             {}

// TestPaginationFollowsCursor: a listing follows nextCursor to the end, and
// stops at 50 pages (or 5,000 items), logged, on an upstream whose cursor
// never ends.
func TestPaginationFollowsCursor(t *testing.T) {
	f := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) { f.tools = fakeTools(120); f.pageSize = 50 })
	c := connectFake(t, f, ClientConfig{})
	if got := c.ToolCount(); got != 120 {
		t.Fatalf("tools = %d, want 120 from pages of 50", got)
	}
	if pages := len(f.requests(MethodToolsList)); pages != 3 {
		t.Errorf("%d tools/list pages, want 3", pages)
	}

	logs := captureSlog(t)
	endless := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) { f.tools = fakeTools(6000); f.pageSize = 10 })
	ec := connectFake(t, endless, ClientConfig{})
	if got, pages := ec.ToolCount(), len(endless.requests(MethodToolsList)); got != 500 || pages != 50 {
		t.Errorf("a 600-page listing: %d tools in %d pages, want 500 in 50", got, pages)
	}
	big := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) { f.tools = fakeTools(6000); f.pageSize = 2000 })
	if got := connectFake(t, big, ClientConfig{}).ToolCount(); got != 5000 {
		t.Errorf("a 6,000-item listing: %d tools, want the first 5,000", got)
	}
	capped := 0
	for _, r := range logs() {
		if r.msg == "mcp upstream list capped" {
			capped++
		}
	}
	if capped < 2 {
		t.Errorf("%d capped listings logged, want both", capped)
	}
}

// waitTools waits for the change event to report n tools.
func waitTools(t *testing.T, events <-chan []Tool, n int, within time.Duration) {
	t.Helper()
	timer := time.NewTimer(within)
	defer timer.Stop()
	for {
		select {
		case tools := <-events:
			if len(tools) == n {
				return
			}
		case <-timer.C:
			t.Fatalf("no change event with %d tools within %s", n, within)
		}
	}
}

func toolEvents(c *Client) <-chan []Tool {
	ch := make(chan []Tool, 16)
	c.SetOnToolsChanged(func(_ string, tools []Tool) { ch <- tools })
	return ch
}

// TestListChangedRefresh: an upstream list_changed (on a legacy session's
// GET stream, or 2026's subscriptions/listen) refreshes the list and emits
// the change event.
func TestListChangedRefresh(t *testing.T) {
	for name, era2026 := range map[string]bool{"legacy GET stream": false, "2026 subscriptions/listen": true} {
		t.Run(name, func(t *testing.T) {
			f := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) { f.era2026 = era2026 })
			c := connectFake(t, f, ClientConfig{ToolsCacheTTL: time.Hour})
			events := toolEvents(c)
			f.waitStreams(t, 1)
			f.set(func(f *fakeUpstream) { f.tools = fakeTools(4) })
			f.broadcast(MethodToolsListChanged)
			waitTools(t, events, 4, 2*time.Second)
			if era2026 {
				l := f.requests(MethodSubscriptionsListen)
				if len(l) == 0 || !strings.Contains(string(l[0].Params), MethodToolsListChanged) {
					t.Errorf("subscriptions/listen = %v", l)
				}
			}
		})
	}
}

// TestTTLRefresh: the lists are refreshed every tools_cache_ttl_seconds; a
// refresh that finds the same list emits nothing, a changed one the event.
func TestTTLRefresh(t *testing.T) {
	f := newFakeUpstream(t, "/mcp", nil)
	c := connectFake(t, f, ClientConfig{ToolsCacheTTL: 100 * time.Millisecond})
	events := toolEvents(c)
	time.Sleep(400 * time.Millisecond)
	if len(f.requests(MethodToolsList)) < 3 {
		t.Fatalf("%d tools/list in 400 ms with a 100 ms TTL", len(f.requests(MethodToolsList)))
	}
	select {
	case tools := <-events:
		t.Fatalf("a refresh with no change emitted %d tools", len(tools))
	default:
	}
	f.set(func(f *fakeUpstream) { f.tools = fakeTools(2) })
	waitTools(t, events, 2, 2*time.Second)
}

// TestPerCallAuthOverride: CallOptions.Auth replaces the server's configured
// credential for that call only; the next call is sent with the configured
// one again.
func TestPerCallAuthOverride(t *testing.T) {
	f := newFakeUpstream(t, "/mcp", nil)
	c := connectFake(t, f, ClientConfig{Auth: AuthConfig{Type: "api_key", Header: "X-Upstream-Key", Key: "configured-key"}})
	ctx, cancel := contextWithTimeout(5 * time.Second)
	defer cancel()
	user := &AuthOverride{Header: http.Header{"Authorization": {"Bearer user-token"}}}
	if _, err := c.CallToolRaw(ctx, "echo", nil, CallOptions{Auth: user}); err != nil {
		t.Fatal(err)
	}
	if _, err := c.CallToolRaw(ctx, "echo", nil, CallOptions{}); err != nil {
		t.Fatal(err)
	}
	calls := f.requests(MethodToolsCall)
	if got := calls[0].Header; got.Get("Authorization") != "Bearer user-token" || got.Get("X-Upstream-Key") != "" {
		t.Errorf("the overridden call sent Authorization %q and the configured key %q", got.Get("Authorization"), got.Get("X-Upstream-Key"))
	}
	if got := calls[1].Header; got.Get("X-Upstream-Key") != "configured-key" || got.Get("Authorization") != "" {
		t.Errorf("the next call sent Authorization %q, key %q; want the configured key only", got.Get("Authorization"), got.Get("X-Upstream-Key"))
	}
	if s := user.String(); strings.Contains(s, "user-token") {
		t.Errorf("AuthOverride prints its value: %s", s)
	}
}

// TestForwardHeadersAllowlistAndRefusedNames: only the inbound headers the
// server's forward_headers names are forwarded, and never a refused one,
// even when an org config listed it; Mcp-* headers are never copied.
func TestForwardHeadersAllowlistAndRefusedNames(t *testing.T) {
	f := newFakeUpstream(t, "/mcp", nil)
	refused := []string{"Authorization", "Cookie", "X-Api-Key", "Proxy-Authorization", "Mcp-Session-Id", "MCP-Protocol-Version", "X-Agentcc-Org-Id", "X-Omega-Agent"}
	c := connectFake(t, f, ClientConfig{Auth: AuthConfig{Type: "bearer", Token: "upstream-token"},
		Call: CallSettings{ForwardHeaders: append([]string{"x-tenant", "Mcp-Name"}, refused...)}})
	inbound := http.Header{}
	for _, h := range append(refused, "X-Tenant", "X-Other", "Mcp-Name") {
		inbound.Set(h, "client-value")
	}
	ctx, cancel := contextWithTimeout(5 * time.Second)
	defer cancel()
	if _, err := c.CallToolRaw(ctx, "echo", nil, CallOptions{ForwardHeaders: inbound}); err != nil {
		t.Fatal(err)
	}
	got := f.requests(MethodToolsCall)[0].Header
	if got.Get("X-Tenant") != "client-value" {
		t.Errorf("X-Tenant not forwarded: %v", got)
	}
	for _, h := range append(refused, "X-Other", "Mcp-Name") {
		if got.Get(h) == "client-value" {
			t.Errorf("%s forwarded", h)
		}
	}
	if got.Get("Authorization") != "Bearer upstream-token" {
		t.Errorf("Authorization = %q, want the configured credential", got.Get("Authorization"))
	}
}

// TestProgressTokenAndIdsRewrittenOnSharedClient: two callers sharing one
// client, both with downstream id 1 and progressToken "tok": the upstream
// sees the client's own distinct ids and a gateway token per call; each
// caller gets only its own progress, with "tok" restored, and a token the
// client never issued is dropped; a caller that gives up has its call
// cancelled upstream under the upstream id.
func TestProgressTokenAndIdsRewrittenOnSharedClient(t *testing.T) {
	var tokens sync.Map // gateway token -> the caller it belongs to ("for-<i>")
	var mu sync.Mutex
	started, sent := 0, 0
	both, bothSent := make(chan struct{}), make(chan struct{})
	f := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) {
		f.sse = true
		f.onCall = func(req fakeRequest, send func(any)) (any, *RPCError) {
			var p struct {
				Arguments struct {
					Caller string `json:"caller"`
				} `json:"arguments"`
			}
			_ = json.Unmarshal(req.Params, &p)
			tokens.Store(string(wire.MetaOf(req.Params)[wire.MetaProgressToken]), p.Arguments.Caller)
			mu.Lock()
			if started++; started == 2 {
				close(both)
			}
			mu.Unlock()
			<-both
			tokens.Range(func(tok, caller any) bool { // every call's progress on every stream
				send(map[string]any{"jsonrpc": "2.0", "method": MethodProgress, "params": map[string]any{
					"progressToken": json.RawMessage(tok.(string)), "progress": 1, "message": caller}})
				return true
			})
			send(map[string]any{"jsonrpc": "2.0", "method": MethodProgress, "params": map[string]any{"progressToken": "tok", "progress": 9}})
			mu.Lock()
			if sent++; sent == 2 {
				close(bothSent)
			}
			mu.Unlock()
			<-bothSent // both calls stay in flight until every progress is out
			return map[string]any{"content": []any{}}, nil
		}
	})
	c := connectFake(t, f, ClientConfig{})
	var got [2][]string
	var wg sync.WaitGroup
	for i := range 2 {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			var sinkMu sync.Mutex
			ctx := WithUpstreamNotifications(context.Background(), func(m *Message) {
				sinkMu.Lock()
				got[i] = append(got[i], string(m.Params))
				sinkMu.Unlock()
			})
			ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
			defer cancel()
			args := json.RawMessage(`{"caller":"for-` + string(rune('0'+i)) + `"}`)
			if _, err := c.CallToolRaw(ctx, "echo", args, CallOptions{ProgressToken: `"tok"`}); err != nil {
				t.Errorf("call %d: %v", i, err)
			}
		}(i)
	}
	wg.Wait()
	calls := f.requests(MethodToolsCall)
	if len(calls) != 2 || string(calls[0].ID) == string(calls[1].ID) {
		t.Fatalf("upstream ids %v: want two distinct client ids", calls)
	}
	seen := map[string]bool{}
	for _, call := range calls {
		tok := string(wire.MetaOf(call.Params)[wire.MetaProgressToken])
		if tok == `"tok"` || tok == "" || seen[tok] {
			t.Errorf("upstream progressToken %s: want a gateway token per call", tok)
		}
		seen[tok] = true
	}
	// The fake sent each call's progress on both streams, and "tok" itself,
	// which the client never issued. A caller gets its own (at least the copy
	// on its own stream, which precedes its answer), with "tok" restored, and
	// nothing of the other caller's or the unissued token's.
	for i, notes := range got {
		if len(notes) == 0 {
			t.Errorf("caller %d got no progress", i)
		}
		own := `"message":"for-` + string(rune('0'+i)) + `"`
		for _, n := range notes {
			if !strings.Contains(n, `"progressToken":"tok"`) || !strings.Contains(n, own) {
				t.Errorf("caller %d got %s, want only its own progress, with its token", i, n)
			}
		}
	}

	// A caller that gives up: the upstream is told to cancel its own id.
	f.set(func(f *fakeUpstream) { f.sse = false; f.onCall = slowCall(2 * time.Second) })
	ctx, cancel := contextWithTimeout(100 * time.Millisecond)
	defer cancel()
	_, _ = c.CallToolRaw(ctx, "echo", nil, CallOptions{})
	last := f.requests(MethodToolsCall)[2]
	for deadline := time.Now().Add(3 * time.Second); time.Now().Before(deadline); time.Sleep(20 * time.Millisecond) {
		for _, n := range f.requests(MethodCancelled) {
			var p struct {
				RequestID json.RawMessage `json:"requestId"`
			}
			_ = json.Unmarshal(n.Params, &p)
			if string(p.RequestID) == string(last.ID) {
				return
			}
		}
	}
	t.Errorf("no notifications/cancelled for upstream id %s", last.ID)
}

// TestResultCapEnforced: an answer over mcp.max_result_bytes fails the call
// (JSON and SSE alike); one under it passes.
func TestResultCapEnforced(t *testing.T) {
	for _, sse := range []bool{false, true} {
		f := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) {
			f.sse = sse
			f.onCall = func(req fakeRequest, _ func(any)) (any, *RPCError) {
				var p struct {
					Arguments struct {
						N int `json:"n"`
					} `json:"arguments"`
				}
				_ = json.Unmarshal(req.Params, &p)
				return map[string]any{"content": []any{map[string]any{"type": "text", "text": strings.Repeat("x", p.Arguments.N)}}}, nil
			}
		})
		c := connectFake(t, f, ClientConfig{Call: CallSettings{MaxResultBytes: 4096}})
		ctx, cancel := contextWithTimeout(5 * time.Second)
		if _, err := c.CallToolRaw(ctx, "echo", json.RawMessage(`{"n":8000}`), CallOptions{}); !errors.Is(err, ErrResultTooLarge) {
			t.Errorf("sse=%v: an 8 KB result at a 4 KiB cap: %v, want ErrResultTooLarge", sse, err)
		}
		if _, err := c.CallToolRaw(ctx, "echo", json.RawMessage(`{"n":1000}`), CallOptions{}); err != nil {
			t.Errorf("sse=%v: a 1 KB result: %v", sse, err)
		}
		cancel()
	}
}

// countingDialer counts the connections it opens.
type countingDialer struct {
	n atomic.Int64
	d net.Dialer
}

func (c *countingDialer) DialContext(ctx context.Context, network, addr string) (net.Conn, error) {
	c.n.Add(1)
	return c.d.DialContext(ctx, network, addr)
}

// TestDialerInjected: the client dials through the configured Dialer: M17's
// egress guard refuses the loopback fake until private_allow lists it, and a
// plain dialer sees every connection.
func TestDialerInjected(t *testing.T) {
	f := newFakeUpstream(t, "/mcp", nil)
	strict, _ := egress.New(egress.Options{})
	c := NewClient(ClientConfig{ServerID: "guarded", URL: f.URLPath(), Dialer: strict})
	defer c.Close()
	ctx, cancel := contextWithTimeout(5 * time.Second)
	defer cancel()
	err := c.Connect(ctx)
	var refused *egress.RefusedError
	if !errors.As(err, &refused) {
		t.Fatalf("connect to loopback through the guard without private_allow: %v, want an egress refusal", err)
	}
	if len(f.requests("")) != 0 {
		t.Fatal("the refused upstream was contacted")
	}
	host := strings.TrimPrefix(f.URL, "http://")
	allowed, _ := egress.New(egress.Options{PrivateAllow: []string{host}})
	connectFake(t, f, ClientConfig{Dialer: allowed})

	counting := &countingDialer{}
	connectFake(t, f, ClientConfig{Dialer: counting})
	if counting.n.Load() == 0 {
		t.Error("the configured dialer opened no connection")
	}
}

// ioNopCloser is an io.ReadCloser over data.
func ioNopCloser(data []byte) *nopBody { return &nopBody{r: strings.NewReader(string(data))} }

type nopBody struct{ r *strings.Reader }

func (b *nopBody) Read(p []byte) (int, error) { return b.r.Read(p) }
func (b *nopBody) Close() error               { return nil }

// base64Of is EncodeHeaderValue's Base64 form of v.
func base64Of(v string) string {
	return "=?base64?" + base64.StdEncoding.EncodeToString([]byte(v)) + "?="
}

// gatewayWith serves the fake as server id through a gateway Server with a
// legacy session, the way the static pool registers it.
func gatewayWith(t *testing.T, id string, f *fakeUpstream, cfg ClientConfig, toolTimeout time.Duration) (*Server, string) {
	t.Helper()
	s := NewServer(ServerConfig{MaxAgentDepth: 5, ToolCallTimeout: toolTimeout, SessionTTL: time.Minute, Separator: "_"})
	t.Cleanup(s.Close)
	cfg.ServerID = id
	s.RegisterClient(connectFake(t, f, cfg))
	return s, initializeSession(t, s)
}

// callGateway posts one tools/call to the gateway and decodes the answer.
func callGateway(t *testing.T, s *Server, session, name string, params string, headers map[string]string) *Message {
	t.Helper()
	h := map[string]string{"MCP-Session-Id": session}
	for k, v := range headers {
		h[k] = v
	}
	raw := json.RawMessage(`{"name":"` + name + `"` + params + `}`)
	return decodeResponse(t, postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodToolsCall, Params: raw}, h))
}

// denialOf decodes an in-band denial's result: its _meta and error object.
func denialOf(t *testing.T, resp *Message) (meta map[string]json.RawMessage, obj ErrorObject) {
	t.Helper()
	var res struct {
		IsError bool                       `json:"isError"`
		Meta    map[string]json.RawMessage `json:"_meta"`
	}
	if resp.Error != nil || json.Unmarshal(resp.Result, &res) != nil || !res.IsError {
		t.Fatalf("want an in-band isError result, got %s %+v", resp.Result, resp.Error)
	}
	_ = json.Unmarshal(res.Meta[MetaKeyError], &obj)
	return res.Meta, obj
}

// TestUpstreamErrorCodeKept: an upstream's JSON-RPC error answers the call
// in band as upstream_error, its code kept in metadata.upstream_code and in
// _meta["io.futureagi/upstream_error"] {code, message}, attributed upstream;
// a dead upstream is attributed network and a slow one timeout.
func TestUpstreamErrorCodeKept(t *testing.T) {
	f := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) {
		f.onCall = func(fakeRequest, func(any)) (any, *RPCError) {
			return nil, &RPCError{Code: -32099, Message: "quota exceeded for api_key=k-123456789"}
		}
	})
	s, session := gatewayWith(t, "up", f, ClientConfig{}, 5*time.Second)
	meta, obj := denialOf(t, callGateway(t, s, session, "up_echo", "", nil))
	var kept struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
	}
	if err := json.Unmarshal(meta[MetaKeyUpstreamError], &kept); err != nil || kept.Code != -32099 || !strings.Contains(kept.Message, "quota exceeded") {
		t.Errorf("_meta[%s] = %s, want the upstream's code and message", MetaKeyUpstreamError, meta[MetaKeyUpstreamError])
	}
	if strings.Contains(kept.Message, "k-123456789") || strings.Contains(obj.Message, "k-123456789") {
		t.Errorf("a credential in the upstream message reached the client: %q / %q", kept.Message, obj.Message)
	}
	md, _ := obj.Metadata.(map[string]any)
	if obj.Code != "upstream_error" || md["attribution"] != "upstream" || md["upstream_code"] != float64(-32099) {
		t.Errorf("error object = %+v, want upstream_error, attribution upstream, upstream_code -32099", obj)
	}

	slow := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) { f.onCall = slowCall(time.Second) })
	s2, session2 := gatewayWith(t, "slow", slow, ClientConfig{}, 100*time.Millisecond)
	if _, obj := denialOf(t, callGateway(t, s2, session2, "slow_echo", "", nil)); obj.Metadata.(map[string]any)["attribution"] != "timeout" {
		t.Errorf("a call past tool_call_timeout: %+v, want attribution timeout", obj)
	}

	gone := newFakeUpstream(t, "/mcp", nil)
	s3, session3 := gatewayWith(t, "gone", gone, ClientConfig{}, 5*time.Second)
	gone.closeStreams()
	gone.Server.Close()
	if _, obj := denialOf(t, callGateway(t, s3, session3, "gone_echo", "", nil)); obj.Metadata.(map[string]any)["attribution"] != "network" {
		t.Errorf("a call to a closed upstream: %+v, want attribution network", obj)
	}
}

// TestMcpHeadersRegeneratedFromRewrittenBody: for a 2026 upstream every Mcp-*
// header is regenerated from the body the gateway sends: Mcp-Name is the
// upstream tool name after the rewrite, Mcp-Param-* come from the arguments
// (the schema's x-mcp-header ones, and each one the client sent a header
// for, Base64 when unsafe); no client Mcp-* header or _meta is copied.
func TestMcpHeadersRegeneratedFromRewrittenBody(t *testing.T) {
	f := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) {
		f.era2026 = true
		f.tools = []json.RawMessage{json.RawMessage(`{"name":"lookup","inputSchema":{"type":"object","properties":{` +
			`"region":{"type":"string","x-mcp-header":"Region"},"city":{"type":"string"},"zone":{"type":"string"}}}}`)}
	})
	s, session := gatewayWith(t, "geo", f, ClientConfig{}, 5*time.Second)
	resp := callGateway(t, s, session, "geo_lookup",
		`,"arguments":{"region":"eu-west","city":"Zürich","zone":"z1"},"_meta":{"progressToken":7,"io.futureagi/agent":{"agent_id":"a1"}}`,
		map[string]string{"Mcp-Method": "tools/call", "Mcp-Name": "geo_lookup", "Mcp-Param-Region": "stale",
			"Mcp-Param-City": "Zürich", "Mcp-Param-Ghost": "x", "X-Other": "y"})
	if resp.Error != nil {
		t.Fatalf("call: %+v", resp.Error)
	}
	up := f.requests(MethodToolsCall)[0]
	h := up.Header
	want := map[string]string{"Mcp-Method": "tools/call", "Mcp-Name": "lookup", "Mcp-Param-Region": "eu-west", "Mcp-Param-City": base64Of("Zürich")}
	for k, v := range want {
		if got := h.Get(k); got != v {
			t.Errorf("%s = %q, want %q", k, got, v)
		}
	}
	for _, k := range []string{"Mcp-Param-Ghost", "Mcp-Param-Zone", "X-Other"} {
		if h.Get(k) != "" {
			t.Errorf("%s sent upstream: %q", k, h.Get(k))
		}
	}
	meta := wire.MetaOf(up.Params)
	if _, ok := meta["io.futureagi/agent"]; ok {
		t.Errorf("the client's io.futureagi/agent reached the upstream: %s", up.Params)
	}
	if tok := string(meta[wire.MetaProgressToken]); tok == "" || tok == "7" {
		t.Errorf("upstream progressToken %q: want a gateway token in place of the client's 7", tok)
	}
}

// TestBuildStaticPoolPassesCallSettings (M13 IR-1, at the W3b merge): the
// static YAML clients BuildStaticPool connects at route setup are built
// through StaticPoolClientConfig, which starts from StaticClientConfig, so
// each carries its server's timeout_ms, forward_headers, era and
// mcp.max_result_bytes from the start; nothing applies them after connect
// (M13's ApplyStaticCallSettings stopgap is gone).
func TestBuildStaticPoolPassesCallSettings(t *testing.T) {
	f := newFakeUpstream(t, "/mcp", nil)
	cfg := config.MCPConfig{MaxResultBytes: 4096, Servers: map[string]config.MCPServerConfig{
		"crm":   {URL: f.URLPath(), TimeoutMS: 120000, ForwardHeaders: []string{"X-Tenant"}, Era: "2025-06-18"},
		"plain": {URL: f.URLPath()},
	}}
	clients := BuildStaticPool(context.Background(), cfg, nil)
	byID := map[string]*Client{}
	for _, c := range clients {
		byID[c.ServerID()] = c
		t.Cleanup(func() { _ = c.Close() })
	}
	crm, plain := byID["crm"], byID["plain"]
	if crm == nil || plain == nil {
		t.Fatalf("BuildStaticPool connected %d clients, want crm and plain", len(clients))
	}
	got := crm.CallSettings()
	if got.Timeout != 120*time.Second || got.MaxResultBytes != 4096 || len(got.ForwardHeaders) != 1 || got.ForwardHeaders[0] != "X-Tenant" {
		t.Errorf("crm settings = %+v, want timeout_ms 120000, forward_headers [X-Tenant], max_result_bytes 4096", got)
	}
	if other := plain.CallSettings(); other.Timeout != 0 || other.MaxResultBytes != 4096 || len(other.ForwardHeaders) != 0 {
		t.Errorf("plain settings = %+v, want only mcp.max_result_bytes", other)
	}
	if cc := StaticClientConfig("crm", cfg.Servers["crm"], cfg); cc.Era != "2025-06-18" || cc.URL != f.URLPath() || cc.Call.Timeout != 120*time.Second {
		t.Errorf("StaticClientConfig = %+v", cc)
	}
	pc, err := StaticPoolClientConfig("crm", cfg.Servers["crm"], cfg, nil)
	if err != nil || pc.Era != "2025-06-18" || pc.Call.Timeout != 120*time.Second {
		t.Errorf("StaticPoolClientConfig = %+v, %v", pc, err)
	}
}
