package mcp

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/protocol"
)

func TestServerInitialize(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	params := InitializeParams{
		ProtocolVersion: ProtocolVersion,
		ClientInfo:      Implementation{Name: "test-agent", Version: "1.0"},
		Capabilities:    ClientCapabilities{},
	}
	paramsData, _ := json.Marshal(params)

	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  MethodInitialize,
		Params:  paramsData,
	}

	w := postMCP(t, s, msg, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	// Should have session ID in header.
	sessionID := w.Header().Get("MCP-Session-Id")
	if sessionID == "" {
		t.Fatal("expected MCP-Session-Id header")
	}

	// Parse result.
	var result InitializeResult
	if err := json.Unmarshal(resp.Result, &result); err != nil {
		t.Fatal(err)
	}
	if result.ProtocolVersion != ProtocolVersion {
		t.Fatalf("expected protocol %s, got %s", ProtocolVersion, result.ProtocolVersion)
	}
	if result.ServerInfo.Name != "agentcc-gateway" {
		t.Fatalf("expected server name 'agentcc-gateway', got %s", result.ServerInfo.Name)
	}
	if result.Capabilities.Tools == nil {
		t.Fatal("expected tools capability")
	}
}

func TestServerPing(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  MethodPing,
	}

	w := postMCP(t, s, msg, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}
}

func TestServerUnknownMethod(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  "unknown/method",
	}

	w := postMCP(t, s, msg, nil)
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for unknown method")
	}
	if resp.Error.Code != ErrCodeMethodNotFound {
		t.Fatalf("expected code %d, got %d", ErrCodeMethodNotFound, resp.Error.Code)
	}
}

func TestServerInvalidJSON(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	req := httptest.NewRequest("POST", "/mcp", bytes.NewReader([]byte("not json")))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	s.HandlePost(w, req)

	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected parse error")
	}
	if resp.Error.Code != ErrCodeParse {
		t.Fatalf("expected code %d, got %d", ErrCodeParse, resp.Error.Code)
	}
}

func TestServerNotifications(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	msg := &Message{
		JSONRPC: "2.0",
		Method:  MethodInitialized,
	}

	w := postMCP(t, s, msg, nil)
	if w.Code != http.StatusAccepted {
		t.Fatalf("expected 202, got %d", w.Code)
	}
}

// requestAPIKey must read a key exactly as middleware.KeyAuth does, since the
// gateway authenticates that key before the MCP server filters tools by it.
func TestRequestAPIKey(t *testing.T) {
	tests := []struct {
		auth, apiKey string
		expected     string
	}{
		{"Bearer sk-test-123", "", "sk-test-123"},
		{"Bearer sk-bearer", "sk-header", "sk-bearer"},
		{"", "sk-header", "sk-header"},
		{"Basic dXNlcjpwYXNz", "sk-header", "sk-header"},
		{"bearer sk-test-123", "", ""}, // KeyAuth's prefix match is case-sensitive
		{"Token sk-test-123", "", ""},
		{"Bearer ", "sk-header", ""},
		{"Bearer", "", ""},
		{"", "", ""},
	}

	for _, tt := range tests {
		req := httptest.NewRequest("GET", "/", nil)
		if tt.auth != "" {
			req.Header.Set("Authorization", tt.auth)
		}
		if tt.apiKey != "" {
			req.Header.Set("x-api-key", tt.apiKey)
		}
		if got := requestAPIKey(req); got != tt.expected {
			t.Errorf("requestAPIKey(auth=%q, x-api-key=%q) = %q, want %q", tt.auth, tt.apiKey, got, tt.expected)
		}
	}
}

// --- protocol core (2026-07-28 stateless serving, shared HTTP rules) ---

// m11ToolsList is the upstream's tools/list: a tool with fields the typed
// structs never knew (icons, an extension object, upstream _meta), and one
// whose region argument is mirrored as Mcp-Param-Region.
const m11ToolsList = `{"tools":[` +
	`{"name":"weather","title":"Weather","icons":[{"src":"https://up.example/i.png"}],"inputSchema":{"type":"object"},"_meta":{"vendor/x":1},"x-ext":{"a":1}},` +
	`{"name":"geo","inputSchema":{"type":"object","properties":{"region":{"type":"string","x-mcp-header":"Region"},"note":{"type":"string"}}}}]}`

// m11CallResult is the upstream's tools/call result: structured content, a
// resource link with every optional field, upstream _meta and an unknown
// extension member.
const m11CallResult = `{"content":[{"type":"text","text":"Sunny"},` +
	`{"type":"resource_link","uri":"file:///report.pdf","name":"report","title":"Report","description":"Full report","mimeType":"application/pdf","size":2048,"annotations":{"audience":["user"]},"_meta":{"v":1}}],` +
	`"structuredContent":{"sky":"sunny","celsius":21},"_meta":{"upstream/trace":"t1"},"x-apps":{"ui":"ui://w"}}`

// m11Upstream is an upstream MCP server for the protocol-core tests, reached through
// today's client. A held tools/call waits until release is closed or its
// request ends, and then closes gone.
type m11Upstream struct {
	*httptest.Server
	mu      sync.Mutex
	calls   []json.RawMessage
	release chan struct{}
	gone    chan struct{}
	// results replaces the fixed result of a method (setResult).
	results map[string]string
}

// setResult makes the upstream answer method with result from now on.
func (u *m11Upstream) setResult(method, result string) {
	u.mu.Lock()
	defer u.mu.Unlock()
	if u.results == nil {
		u.results = map[string]string{}
	}
	u.results[method] = result
}

func (u *m11Upstream) resultFor(method, fixed string) string {
	u.mu.Lock()
	defer u.mu.Unlock()
	if r, ok := u.results[method]; ok {
		return r
	}
	return fixed
}

func startM11Upstream(t *testing.T, hold bool) *m11Upstream {
	t.Helper()
	u := &m11Upstream{}
	if hold {
		u.release, u.gone = make(chan struct{}), make(chan struct{})
	}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		if json.NewDecoder(r.Body).Decode(&msg) != nil {
			http.Error(w, "bad json", http.StatusBadRequest)
			return
		}
		if msg.IsNotification() {
			w.WriteHeader(http.StatusAccepted)
			return
		}
		var result string
		switch msg.Method {
		case MethodInitialize:
			result = `{"protocolVersion":"2025-11-25","capabilities":{"tools":{},"resources":{}},"serverInfo":{"name":"up","version":"1"}}`
		case MethodToolsList:
			result = m11ToolsList
		case MethodResourcesList:
			result = `{"resources":[{"uri":"file:///a","name":"a","title":"A","_meta":{"k":1}}]}`
		case MethodResourcesRead:
			result = u.resultFor(msg.Method, `{"contents":[{"uri":"file:///a","text":"abc"}],"ttlMs":30000,"_meta":{"r":1}}`)
		case MethodPromptsList:
			result = `{"prompts":[]}`
		case MethodPing:
			result = `{}`
		case MethodToolsCall:
			u.mu.Lock()
			u.calls = append(u.calls, append(json.RawMessage(nil), msg.Params...))
			u.mu.Unlock()
			if u.release != nil {
				select {
				case <-u.release:
				case <-r.Context().Done():
					close(u.gone)
					return
				}
			}
			result = u.resultFor(msg.Method, m11CallResult)
		default:
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"jsonrpc":"2.0","id":` + string(msg.ID) + `,"error":{"code":-32601,"message":"unknown method"}}`))
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"jsonrpc":"2.0","id":` + string(msg.ID) + `,"result":` + result + `}`))
	}))
	t.Cleanup(u.Close)
	return u
}

func (u *m11Upstream) received() int {
	u.mu.Lock()
	defer u.mu.Unlock()
	return len(u.calls)
}

// m11Hooks records the chain hooks that ran, with the call context each saw.
type m11Hooks struct {
	mu   sync.Mutex
	seen []string
	ccs  map[string]*CallContext
}

func (h *m11Hooks) observe(hook string, cc *CallContext) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.seen = append(h.seen, hook)
	if h.ccs == nil {
		h.ccs = map[string]*CallContext{}
	}
	h.ccs[hook] = cc
}

func (h *m11Hooks) ran() []string {
	h.mu.Lock()
	defer h.mu.Unlock()
	return append([]string(nil), h.seen...)
}

func (h *m11Hooks) at(hook string) *CallContext {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.ccs[hook]
}

// newM11Server is an MCP server with up connected as server "up" (tools
// up_weather and up_geo, resource file:///a) and a hook recorder.
func newM11Server(t *testing.T, up *m11Upstream) (*Server, *m11Hooks) {
	t.Helper()
	s := NewServer(ServerConfig{MaxAgentDepth: 5, ToolCallTimeout: 10 * time.Second, SessionTTL: time.Minute, Separator: "_"})
	t.Cleanup(s.Close)
	if up != nil {
		client := NewClient(ClientConfig{ServerID: "up", URL: up.URL, TransportType: "http"})
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := client.Connect(ctx); err != nil {
			t.Fatalf("connect to the upstream: %v", err)
		}
		s.RegisterClient(client)
	}
	h := &m11Hooks{}
	s.hookObserver = h.observe
	return s, h
}

// m11Meta is a 2026-07-28 request's _meta.
func m11Meta() map[string]any {
	return map[string]any{
		protocol.MetaProtocolVersion:    protocol.Version2026,
		protocol.MetaClientCapabilities: map[string]any{},
		protocol.MetaClientInfo:         map[string]any{"name": "m11-client", "version": "1"},
	}
}

// m11Request builds a 2026-07-28 POST: params with _meta (unless params
// carries its own), and the mirrored headers a compliant client sends.
// hdr overrides them; an empty value removes a header.
func m11Request(t *testing.T, method string, params map[string]any, hdr map[string]string) *http.Request {
	t.Helper()
	if params == nil {
		params = map[string]any{}
	}
	if _, ok := params["_meta"]; !ok {
		params["_meta"] = m11Meta()
	}
	msg := map[string]any{"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
	body, err := json.Marshal(msg)
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	req.Header.Set("MCP-Protocol-Version", protocol.Version2026)
	req.Header.Set("Mcp-Method", method)
	if name, ok := params["name"].(string); ok {
		req.Header.Set("Mcp-Name", name)
	}
	if uri, ok := params["uri"].(string); ok && method == MethodResourcesRead {
		req.Header.Set("Mcp-Name", uri)
	}
	for k, v := range hdr {
		if v == "" {
			req.Header.Del(k)
		} else {
			req.Header.Set(k, v)
		}
	}
	return req
}

// m11MetaJSON is m11Meta as JSON text, for bodies written by hand.
const m11MetaJSON = `{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}`

// m11RawRequest is m11Request with params written as JSON text, so a body
// can say what a Go map cannot: a member twice, or in two spellings. The
// mirrored headers are the protocol version and the method; hdr adds the
// rest.
func m11RawRequest(t *testing.T, method, params string, hdr map[string]string) *http.Request {
	t.Helper()
	body := `{"jsonrpc":"2.0","id":1,"method":"` + method + `","params":` + params + `}`
	if !json.Valid([]byte(body)) {
		t.Fatalf("test body is not JSON: %s", body)
	}
	req := httptest.NewRequest(http.MethodPost, "/mcp", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	req.Header.Set("MCP-Protocol-Version", protocol.Version2026)
	req.Header.Set("Mcp-Method", method)
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	return req
}

func serveM11(s *Server, req *http.Request) *httptest.ResponseRecorder {
	w := httptest.NewRecorder()
	s.HandlePost(w, req)
	return w
}

// m11Result decodes a JSON-RPC answer's result object, failing on an error.
func m11Result(t *testing.T, w *httptest.ResponseRecorder) map[string]json.RawMessage {
	t.Helper()
	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("HTTP %d error %d %s", w.Code, resp.Error.Code, resp.Error.Message)
	}
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(resp.Result, &obj); err != nil {
		t.Fatalf("result %s: %v", resp.Result, err)
	}
	return obj
}

// m11Refused checks an answer is the given HTTP status and JSON-RPC code.
func m11Refused(t *testing.T, w *httptest.ResponseRecorder, status, code int) *Message {
	t.Helper()
	if w.Code != status {
		t.Fatalf("HTTP %d %s, want %d", w.Code, w.Body.String(), status)
	}
	resp := decodeResponse(t, w)
	if resp.Error == nil || resp.Error.Code != code {
		t.Fatalf("answer %+v, want JSON-RPC error %d", resp, code)
	}
	return resp
}

func jsonString(t *testing.T, raw json.RawMessage) string {
	t.Helper()
	var s string
	if err := json.Unmarshal(raw, &s); err != nil {
		t.Fatalf("%s is not a string: %v", raw, err)
	}
	return s
}

func TestEraDetection(t *testing.T) {
	up := startM11Upstream(t, false)
	s, hooks := newM11Server(t, up)

	// A 2026 request needs no session: a session id it sends is ignored, and
	// so is Last-Event-ID.
	req := m11Request(t, MethodToolsCall, map[string]any{"name": "up_weather", "arguments": map[string]any{}},
		map[string]string{"Mcp-Session-Id": "client-sid", "Last-Event-ID": "7"})
	w := serveM11(s, req)
	res := m11Result(t, w)
	if jsonString(t, res["resultType"]) != protocol.ResultComplete {
		t.Fatalf("2026 tools/call result = %v", res)
	}
	cc := hooks.at(hookCallUpstream)
	if cc == nil {
		t.Fatal("the 2026 call never reached the upstream hook")
	}
	if got := requestEra(cc.Request()); got != protocol.Version2026 || cc.ProtocolVersion != protocol.Version2026 {
		t.Fatalf("era %q, protocol version %q; want %s", got, cc.ProtocolVersion, protocol.Version2026)
	}
	if cc.SessionID == "client-sid" || cc.Request().Header.Get("Last-Event-ID") != "" {
		t.Fatalf("a 2026 request used the client's session id %q or Last-Event-ID", cc.SessionID)
	}
	if sess, ok := s.statelessSession(cc.Request()); !ok || sess.ID != "" || sess.ClientInfo.Name != "m11-client" {
		t.Fatalf("stateless session = %+v, %v; want a request-scoped one with the _meta client info", sess, ok)
	}
	if n := s.sessions.Count(); n != 0 {
		t.Fatalf("%d sessions left after a stateless request", n)
	}

	// initialize is legacy even when it carries _meta: it opens a session.
	init := m11Request(t, MethodInitialize, map[string]any{"protocolVersion": "2025-06-18", "capabilities": map[string]any{},
		"clientInfo": map[string]any{"name": "c", "version": "1"}}, nil)
	w = serveM11(s, init)
	if w.Code != http.StatusOK || w.Header().Get("MCP-Session-Id") == "" {
		t.Fatalf("initialize = %d %v, want a legacy session", w.Code, w.Header())
	}
	sid := w.Header().Get("MCP-Session-Id")

	// A request without the 2026 _meta is legacy: it needs its session.
	legacy := func(hdr map[string]string) *httptest.ResponseRecorder {
		return postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`2`), Method: MethodToolsList}, hdr)
	}
	if resp := decodeResponse(t, legacy(nil)); resp.Error == nil {
		t.Fatal("a legacy tools/list without a session was served")
	}
	before := len(hooks.ran())
	w = legacy(map[string]string{"MCP-Session-Id": sid, "MCP-Protocol-Version": "2025-06-18"})
	if res := m11Result(t, w); res["resultType"] != nil || res["ttlMs"] != nil {
		t.Fatalf("legacy tools/list carries 2026 fields: %v", res)
	}
	if cc := hooks.at(hookAssembleList); len(hooks.ran()) == before || requestEra(cc.Request()) != "2025-06-18" {
		t.Fatal("the legacy request was not served under its header's revision")
	}
	legacy(map[string]string{"MCP-Session-Id": sid})
	if got := requestEra(hooks.at(hookAssembleList).Request()); got != protocol.DefaultLegacyVersion {
		t.Fatalf("a legacy request without MCP-Protocol-Version is served under %q, want %s", got, protocol.DefaultLegacyVersion)
	}

	// Only io.modelcontextprotocol/protocolVersion (or a 2026 header) makes a
	// request 2026: a legacy client in its session that sends another
	// reserved key (tasks' related-task) is served under its session.
	params, _ := json.Marshal(map[string]any{"_meta": map[string]any{"io.modelcontextprotocol/related-task": map[string]any{"taskId": "t1"}}})
	w = postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`3`), Method: MethodToolsList, Params: params},
		map[string]string{"MCP-Session-Id": sid, "MCP-Protocol-Version": protocol.Version20251125})
	if res := m11Result(t, w); res["resultType"] != nil {
		t.Fatalf("a legacy request with a reserved _meta key was served as 2026: %v", res)
	}
	if cc := hooks.at(hookAssembleList); cc.SessionID != sid || requestEra(cc.Request()) != protocol.Version20251125 {
		t.Fatalf("legacy request with a reserved key: session %q era %q", cc.SessionID, requestEra(cc.Request()))
	}
}

// m11Served reports whether an answer is a JSON-RPC result (a session the
// server does not know is a bodiless 404).
func m11Served(w *httptest.ResponseRecorder) bool {
	var msg Message
	return w.Code == http.StatusOK && json.Unmarshal(w.Body.Bytes(), &msg) == nil && msg.Error == nil && msg.Result != nil
}

// TestStatelessSessionNeverStored: a 2026 request runs
// under a request-scoped session that validateSession returns first, which
// no store holds and no id names, so no other request can reach it; a legacy
// request still needs a valid session.
func TestStatelessSessionNeverStored(t *testing.T) {
	up := startM11Upstream(t, false)
	s, hooks := newM11Server(t, up)
	type seen struct {
		stored  int
		id      string
		session *Session
		other   int // the HTTP status a session-less legacy request got meanwhile
	}
	var mu sync.Mutex
	during := map[string]seen{}
	s.hookObserver = func(hook string, cc *CallContext) {
		hooks.observe(hook, cc)
		if hook != hookCallUpstream && hook != hookAssembleList {
			return
		}
		sess, _ := s.statelessSession(cc.Request())
		got := seen{stored: s.sessions.Count(), id: cc.SessionID, session: sess}
		if sess != nil {
			// Meanwhile, a legacy request naming no session, or one guessing
			// an id, finds none.
			for _, sid := range []string{"", "client-sid"} {
				hdr := map[string]string{}
				if sid != "" {
					hdr["MCP-Session-Id"] = sid
				}
				if w := postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`9`), Method: MethodToolsList}, hdr); m11Served(w) {
					got.other = w.Code
				}
			}
		}
		mu.Lock()
		during[hook] = got
		mu.Unlock()
	}

	call := serveM11(s, m11Request(t, MethodToolsCall, map[string]any{"name": "up_weather", "arguments": map[string]any{}},
		map[string]string{"Mcp-Session-Id": "client-sid"}))
	if res := m11Result(t, call); res["structuredContent"] == nil {
		t.Fatalf("2026 tools/call = %v", res)
	}
	list := serveM11(s, m11Request(t, MethodToolsList, nil, map[string]string{"Mcp-Session-Id": "client-sid"}))
	if res := m11Result(t, list); res["tools"] == nil {
		t.Fatalf("2026 tools/list = %v", res)
	}
	mu.Lock()
	c, l := during[hookCallUpstream], during[hookAssembleList]
	mu.Unlock()
	for hook, got := range map[string]seen{"tools/call": c, "tools/list": l} {
		if got.session == nil || got.session.ID != "" || got.stored != 0 || got.id != "" || got.other != 0 {
			t.Fatalf("%s inside the call: session %+v, %d stored sessions, cc.SessionID %q, a session-less request answered %d",
				hook, got.session, got.stored, got.id, got.other)
		}
	}
	if c.session == l.session {
		t.Fatal("two 2026 requests shared one session")
	}
	if call.Header().Get("Mcp-Session-Id") != "" || list.Header().Get("Mcp-Session-Id") != "" {
		t.Fatal("a 2026 answer named a session id")
	}

	// A legacy request still needs its session: none, or an unknown one, is
	// refused before the chain; a valid one is served.
	before := len(hooks.ran())
	for _, hdr := range []map[string]string{nil, {"MCP-Session-Id": "client-sid"}} {
		if w := postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`2`), Method: MethodToolsList}, hdr); m11Served(w) {
			t.Fatalf("legacy tools/list with %v was served: %s", hdr, w.Body.String())
		}
	}
	if len(hooks.ran()) != before {
		t.Fatal("a legacy request without a valid session reached the chain")
	}
	sid := initializeSession(t, s)
	w := postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`2`), Method: MethodToolsList}, map[string]string{"MCP-Session-Id": sid})
	if res := m11Result(t, w); res["tools"] == nil || hooks.at(hookAssembleList).SessionID != sid {
		t.Fatalf("legacy tools/list in its session = %v", res)
	}
}

func TestMissingMetaCapability32021(t *testing.T) {
	up := startM11Upstream(t, false)
	s, hooks := newM11Server(t, up)
	call := func(meta map[string]any) map[string]any {
		p := map[string]any{"name": "up_weather", "arguments": map[string]any{}}
		if meta != nil {
			p["_meta"] = meta
		}
		return p
	}
	cases := map[string]*http.Request{
		"no clientCapabilities": m11Request(t, MethodToolsCall, call(map[string]any{protocol.MetaProtocolVersion: protocol.Version2026}), nil),
		"no protocolVersion":    m11Request(t, MethodToolsCall, call(map[string]any{protocol.MetaClientCapabilities: map[string]any{}}), nil),
		"2026 header, no _meta": m11Request(t, MethodToolsCall, call(map[string]any{}), nil),
	}
	for name, req := range cases {
		m11Refused(t, serveM11(s, req), http.StatusBadRequest, protocol.CodeMissingClientCapability)
		if len(hooks.ran()) != 0 || up.received() != 0 {
			t.Fatalf("%s: refused request reached hooks %v / upstream %d", name, hooks.ran(), up.received())
		}
	}
}

func TestUnsupportedVersion32022(t *testing.T) {
	up := startM11Upstream(t, false)
	s, hooks := newM11Server(t, up)
	supported := func(resp *Message) []string {
		var d struct {
			Supported []string `json:"supported"`
		}
		if err := json.Unmarshal(resp.Error.Data, &d); err != nil {
			t.Fatalf("data %s: %v", resp.Error.Data, err)
		}
		return d.Supported
	}
	meta := m11Meta()
	meta[protocol.MetaProtocolVersion] = "2099-01-01"
	req := m11Request(t, MethodToolsList, map[string]any{"_meta": meta}, map[string]string{"MCP-Protocol-Version": "2099-01-01"})
	resp := m11Refused(t, serveM11(s, req), http.StatusBadRequest, protocol.CodeUnsupportedVersion)
	if got := supported(resp); !reflect.DeepEqual(got, protocol.AllVersions()) {
		t.Fatalf("supported = %v, want %v", got, protocol.AllVersions())
	}

	// A session revision named in a 2026 _meta is not a stateless revision.
	meta[protocol.MetaProtocolVersion] = protocol.Version20251125
	req = m11Request(t, MethodToolsList, map[string]any{"_meta": meta}, map[string]string{"MCP-Protocol-Version": protocol.Version20251125})
	m11Refused(t, serveM11(s, req), http.StatusBadRequest, protocol.CodeUnsupportedVersion)

	// A legacy request naming a revision the gateway does not serve.
	w := postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`3`), Method: MethodToolsList},
		map[string]string{"MCP-Protocol-Version": "2019-01-01"})
	m11Refused(t, w, http.StatusBadRequest, protocol.CodeUnsupportedVersion)

	// A legacy request without MCP-Protocol-Version is assumed 2025-03-26,
	// so it is refused when the operator stopped serving that revision.
	s.SetProtocol(protocol.Settings{Versions: []string{protocol.Version2026, protocol.Version20251125}})
	w = postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`3`), Method: MethodToolsList}, nil)
	resp = m11Refused(t, w, http.StatusBadRequest, protocol.CodeUnsupportedVersion)
	if got := supported(resp); !reflect.DeepEqual(got, []string{protocol.Version2026, protocol.Version20251125}) {
		t.Fatalf("headerless legacy request: supported = %v", got)
	}
	if w := postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`3`), Method: MethodToolsList},
		map[string]string{"MCP-Protocol-Version": protocol.Version20251125}); w.Code == http.StatusBadRequest {
		t.Fatalf("a served legacy revision was refused: %s", w.Body.String())
	}

	// With 2026-07-28 off (mcp.protocol.versions), a 2026 request is refused
	// with the revisions served.
	s.SetProtocol(protocol.Settings{Versions: []string{protocol.Version20251125, protocol.Version20250618}})
	resp = m11Refused(t, serveM11(s, m11Request(t, MethodToolsList, nil, nil)), http.StatusBadRequest, protocol.CodeUnsupportedVersion)
	if got := supported(resp); !reflect.DeepEqual(got, []string{protocol.Version20251125, protocol.Version20250618}) {
		t.Fatalf("supported = %v", got)
	}
	if len(hooks.ran()) != 0 {
		t.Fatalf("refused requests reached hooks %v", hooks.ran())
	}
}

func TestHeaderBodyMismatch32020(t *testing.T) {
	up := startM11Upstream(t, false)
	s, hooks := newM11Server(t, up)
	weather := func() map[string]any { return map[string]any{"name": "up_weather", "arguments": map[string]any{}} }
	cases := []struct {
		name   string
		method string
		params map[string]any
		hdr    map[string]string
	}{
		{"protocol version missing", MethodToolsCall, weather(), map[string]string{"MCP-Protocol-Version": ""}},
		{"method missing", MethodToolsCall, weather(), map[string]string{"Mcp-Method": ""}},
		{"method differs", MethodToolsCall, weather(), map[string]string{"Mcp-Method": "tools/list"}},
		{"name missing", MethodToolsCall, weather(), map[string]string{"Mcp-Name": ""}},
		{"name differs", MethodToolsCall, weather(), map[string]string{"Mcp-Name": "up_geo"}},
		{"resource uri differs", MethodResourcesRead, map[string]any{"uri": "file:///a"}, map[string]string{"Mcp-Name": "file:///b"}},
		{"prompt name differs", MethodPromptsGet, map[string]any{"name": "p1"}, map[string]string{"Mcp-Name": "p2"}},
	}
	for _, tc := range cases {
		m11Refused(t, serveM11(s, m11Request(t, tc.method, tc.params, tc.hdr)), http.StatusBadRequest, protocol.CodeHeaderMismatch)
		// Checked before routing, policy and metering: no hook ran.
		if len(hooks.ran()) != 0 || up.received() != 0 {
			t.Fatalf("%s: the request reached hooks %v / upstream %d", tc.name, hooks.ran(), up.received())
		}
	}

	// A member a header mirrors can be read two ways when it is sent twice or
	// in other letter case: the method handlers' decoder matches names
	// case-insensitively and keeps the last match, an exact-name reader keeps
	// "name", and the upstream's parser may keep the first. Each such body is
	// refused before anything runs (Mcp-Name up_weather with
	// "NAME":"up_geo" ran up_geo, with no Mcp-Param-Region for its region).
	// An exact duplicate ("name twice", "argument twice") is refused earlier,
	// by the content bounds as the body is read, and is answered the same way:
	// HTTP 400 with -32020 (TestRefusedRequestBody400On2026And200OnLegacy).
	for _, tc := range []struct {
		name, method, params string
		hdr                  map[string]string
	}{
		{"name then NAME", MethodToolsCall, `"name":"up_weather","NAME":"up_geo","arguments":{"region":"us"}`, map[string]string{"Mcp-Name": "up_weather"}},
		{"NAME then name", MethodToolsCall, `"NAME":"up_geo","name":"up_weather","arguments":{"region":"us"}`, map[string]string{"Mcp-Name": "up_weather"}},
		{"name twice", MethodToolsCall, `"name":"up_weather","name":"up_geo","arguments":{}`, map[string]string{"Mcp-Name": "up_geo"}},
		{"only a case variant", MethodToolsCall, `"Name":"up_weather","arguments":{}`, map[string]string{"Mcp-Name": "up_weather"}},
		{"arguments then ARGUMENTS", MethodToolsCall, `"name":"up_geo","arguments":{"region":"eu"},"ARGUMENTS":{"region":"us"}`,
			map[string]string{"Mcp-Name": "up_geo", "Mcp-Param-Region": "eu"}},
		{"arguments by a long s", MethodToolsCall, `"name":"up_geo","argumentſ":{"region":"us"}`, map[string]string{"Mcp-Name": "up_geo"}},
		{"argument in other case", MethodToolsCall, `"name":"up_geo","arguments":{"region":"eu","REGION":"us"}`,
			map[string]string{"Mcp-Name": "up_geo", "Mcp-Param-Region": "eu"}},
		{"argument twice", MethodToolsCall, `"name":"up_geo","arguments":{"region":"eu","region":"us"}`,
			map[string]string{"Mcp-Name": "up_geo", "Mcp-Param-Region": "us"}},
		{"uri twice", MethodResourcesRead, `"uri":"file:///a","URI":"file:///b"`, map[string]string{"Mcp-Name": "file:///a"}},
		{"prompt name twice", MethodPromptsGet, `"name":"p1","Name":"p2"`, map[string]string{"Mcp-Name": "p1"}},
		{"_meta in other case", MethodToolsCall, `"name":"up_weather","arguments":{},"_META":` + m11MetaJSON, map[string]string{"Mcp-Name": "up_weather"}},
	} {
		params := `{` + tc.params + `,"_meta":` + m11MetaJSON + `}`
		if tc.name == "_meta in other case" {
			params = `{` + tc.params + `}`
		}
		m11Refused(t, serveM11(s, m11RawRequest(t, tc.method, params, tc.hdr)), http.StatusBadRequest, protocol.CodeHeaderMismatch)
		if len(hooks.ran()) != 0 || up.received() != 0 {
			t.Fatalf("%s: the request reached hooks %v / upstream %d", tc.name, hooks.ran(), up.received())
		}
	}
	// The same call with matching headers is served.
	if res := m11Result(t, serveM11(s, m11Request(t, MethodToolsCall, weather(), nil))); res["content"] == nil || up.received() != 1 {
		t.Fatalf("a matching call was not served: %v", res)
	}
}

// TestRefusedRequestBody400On2026And200OnLegacy: a body the content bounds
// refuse as it is read is answered by the era its headers name, before
// routing, policy and metering. On a 2026-07-28 request that is HTTP 400, as
// the transport's Server Validation requires: -32020 (HeaderMismatch) when the
// member sent twice is one a header mirrors (TestHeaderBodyMismatch32020's
// "name twice" and "argument twice" are two), else -32700. A legacy request,
// whose header names a session revision or none, is answered as before:
// HTTP 200 with -32700.
func TestRefusedRequestBody400On2026And200OnLegacy(t *testing.T) {
	up := startM11Upstream(t, false)
	s, hooks := newM11Server(t, up)
	post := func(version, method, body string, hdr map[string]string) *httptest.ResponseRecorder {
		t.Helper()
		if !json.Valid([]byte(body)) {
			t.Fatalf("test body is not JSON: %s", body)
		}
		req := httptest.NewRequest(http.MethodPost, "/mcp", strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/event-stream")
		if version != "" {
			req.Header.Set("MCP-Protocol-Version", version)
		}
		if version == protocol.Version2026 {
			req.Header.Set("Mcp-Method", method)
		}
		for k, v := range hdr {
			req.Header.Set(k, v)
		}
		return serveM11(s, req)
	}
	envelope := func(method, params string) string {
		return `{"jsonrpc":"2.0","id":1,"method":"` + method + `","params":{` + params + `}}`
	}
	refused := func(name string, w *httptest.ResponseRecorder, status, code int) {
		t.Helper()
		var resp Message
		if w.Code != status || json.Unmarshal(w.Body.Bytes(), &resp) != nil || resp.Error == nil || resp.Error.Code != code {
			t.Fatalf("%s: HTTP %d %s, want %d with JSON-RPC error %d", name, w.Code, w.Body.String(), status, code)
		}
		// Refused before routing, policy and metering: no hook ran.
		if len(hooks.ran()) != 0 || up.received() != 0 {
			t.Fatalf("%s: the request reached hooks %v / upstream %d", name, hooks.ran(), up.received())
		}
	}
	meta := `"_meta":` + m11MetaJSON
	pv := `"` + protocol.MetaProtocolVersion + `":"` + protocol.Version2026 + `"`
	caps := `"` + protocol.MetaClientCapabilities + `":{}`
	deep := `"name":"up_weather","arguments":{"deep":` + strings.Repeat("[", 130) + strings.Repeat("]", 130) + `}`
	weather := map[string]string{"Mcp-Name": "up_weather"}
	geo := map[string]string{"Mcp-Name": "up_geo", "Mcp-Param-Region": "us"}

	// A legacy request is answered as before, its header naming a session
	// revision or none: HTTP 200 with -32700.
	for _, tc := range []struct{ name, body string }{
		{"name twice", envelope(MethodToolsCall, `"name":"up_weather","name":"up_geo","arguments":{}`)},
		{"argument twice", envelope(MethodToolsCall, `"name":"up_geo","arguments":{"region":"eu","region":"us"}`)},
		{"method twice", `{"jsonrpc":"2.0","id":1,"method":"tools/list","method":"tools/list"}`},
		{"a params member no header mirrors, twice", envelope(MethodToolsCall, `"name":"up_weather","arguments":{},"extra":1,"extra":2`)},
		{"nesting past the bound", envelope(MethodToolsCall, deep)},
		{"a batch, not an object", `[{"jsonrpc":"2.0","id":1,"method":"tools/list"}]`},
	} {
		for _, version := range []string{protocol.Version20251125, ""} {
			refused(tc.name+" (legacy "+version+")", post(version, MethodToolsCall, tc.body, nil), http.StatusOK, ErrCodeParse)
		}
	}

	// A 2026-07-28 request: HTTP 400.
	for _, tc := range []struct {
		name, method, body string
		hdr                map[string]string
		code               int
	}{
		// A member a header mirrors, sent twice: -32020.
		{"method twice", MethodToolsList, `{"jsonrpc":"2.0","id":1,"method":"tools/list","method":"tools/list","params":{` + meta + `}}`,
			nil, protocol.CodeHeaderMismatch},
		{"params twice", MethodToolsList, `{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{` + meta + `},"params":{` + meta + `}}`,
			nil, protocol.CodeHeaderMismatch},
		{"_meta twice", MethodToolsCall, envelope(MethodToolsCall, `"name":"up_weather","arguments":{},`+meta+`,`+meta),
			weather, protocol.CodeHeaderMismatch},
		{"protocol version twice", MethodToolsCall, envelope(MethodToolsCall, `"name":"up_weather","arguments":{},"_meta":{`+pv+`,`+pv+`,`+caps+`}`),
			weather, protocol.CodeHeaderMismatch},
		{"uri twice", MethodResourcesRead, envelope(MethodResourcesRead, `"uri":"file:///a","uri":"file:///b",`+meta),
			map[string]string{"Mcp-Name": "file:///b"}, protocol.CodeHeaderMismatch},
		{"prompt name twice", MethodPromptsGet, envelope(MethodPromptsGet, `"name":"p1","name":"p2",`+meta),
			map[string]string{"Mcp-Name": "p2"}, protocol.CodeHeaderMismatch},
		{"arguments twice", MethodToolsCall, envelope(MethodToolsCall, `"name":"up_geo","arguments":{"region":"us"},"arguments":{"region":"us"},`+meta),
			geo, protocol.CodeHeaderMismatch},
		{"argument twice after nested values", MethodToolsCall,
			envelope(MethodToolsCall, `"name":"up_geo","arguments":{"note":[1,{"b":2}],"region":"eu","region":"us"},`+meta), geo, protocol.CodeHeaderMismatch},
		{"argument twice, once escaped", MethodToolsCall, envelope(MethodToolsCall, `"name":"up_geo","arguments":{"region":"eu","\u0072egion":"us"},`+meta),
			geo, protocol.CodeHeaderMismatch},
		// Which arguments a tool mirrors is in its inputSchema, which is not
		// resolved for a body the gateway could not read: a member within
		// arguments, reached through objects, counts as one a header mirrors.
		{"argument member twice", MethodToolsCall, envelope(MethodToolsCall, `"name":"up_geo","arguments":{"region":"us","filter":{"k":1,"k":2}},`+meta),
			geo, protocol.CodeHeaderMismatch},

		// Refused for any other reason: -32700.
		{"id twice", MethodToolsList, `{"jsonrpc":"2.0","id":1,"id":2,"method":"tools/list","params":{` + meta + `}}`,
			nil, ErrCodeParse},
		{"a params member no header mirrors, twice", MethodToolsCall, envelope(MethodToolsCall, `"name":"up_weather","arguments":{},"extra":1,"extra":2,`+meta),
			weather, ErrCodeParse},
		{"a _meta member no header mirrors, twice", MethodToolsCall, envelope(MethodToolsCall, `"name":"up_weather","arguments":{},"_meta":{`+pv+`,`+caps+`,`+caps+`}`),
			weather, ErrCodeParse},
		{"prompt arguments twice", MethodPromptsGet, envelope(MethodPromptsGet, `"name":"p1","arguments":{"a":"x","a":"y"},`+meta),
			map[string]string{"Mcp-Name": "p1"}, ErrCodeParse},
		{"argument member twice inside an array", MethodToolsCall, envelope(MethodToolsCall, `"name":"up_geo","arguments":{"region":"us","list":[{"k":1,"k":2}]},`+meta),
			geo, ErrCodeParse},
		{"nesting past the bound", MethodToolsCall, envelope(MethodToolsCall, deep+`,`+meta), weather, ErrCodeParse},
		{"a batch, not an object", MethodToolsList, `[` + envelope(MethodToolsList, meta) + `]`, nil, ErrCodeParse},
	} {
		refused(tc.name, post(protocol.Version2026, tc.method, tc.body, tc.hdr), http.StatusBadRequest, tc.code)
	}
}

func m11Base64(v string) string {
	return "=?base64?" + base64.StdEncoding.EncodeToString([]byte(v)) + "?="
}

func TestBase64ParamHeader(t *testing.T) {
	up := startM11Upstream(t, false)
	s, _ := newM11Server(t, up)
	region := "eu-west 🌍"
	geo := map[string]any{"name": "up_geo", "arguments": map[string]any{"region": region}}
	req := m11Request(t, MethodToolsCall, geo, map[string]string{
		"Mcp-Name":         m11Base64("up_geo"),
		"Mcp-Param-Region": m11Base64(region),
	})
	if res := m11Result(t, serveM11(s, req)); res["content"] == nil || up.received() != 1 {
		t.Fatalf("Base64 headers that match the body were refused: %v", res)
	}
	for name, hdr := range map[string]map[string]string{
		"bad base64":        {"Mcp-Param-Region": "=?base64?***?="},
		"other value":       {"Mcp-Param-Region": m11Base64("us-east")},
		"plain value":       {"Mcp-Param-Region": "eu-west"},
		"bad base64 name":   {"Mcp-Name": "=?base64?%%?=", "Mcp-Param-Region": m11Base64(region)},
		"other base64 name": {"Mcp-Name": m11Base64("up_weather"), "Mcp-Param-Region": m11Base64(region)},
	} {
		w := serveM11(s, m11Request(t, MethodToolsCall, map[string]any{"name": "up_geo", "arguments": map[string]any{"region": region}}, hdr))
		if w.Code != http.StatusBadRequest {
			t.Fatalf("%s: HTTP %d %s, want 400 -32020", name, w.Code, w.Body.String())
		}
		m11Refused(t, w, http.StatusBadRequest, protocol.CodeHeaderMismatch)
	}
	if up.received() != 1 {
		t.Fatalf("mismatched calls reached the upstream (%d calls)", up.received())
	}
	if got, err := protocol.DecodeHeaderValue(protocol.EncodeHeaderValue(region)); err != nil || got != region {
		t.Fatalf("Base64 round trip = %q, %v", got, err)
	}
}

func TestValidatedParamHeadersHandedToUpstream(t *testing.T) {
	up := startM11Upstream(t, false)
	s, hooks := newM11Server(t, up)
	call := func(args map[string]any, hdr map[string]string) *httptest.ResponseRecorder {
		return serveM11(s, m11Request(t, MethodToolsCall, map[string]any{"name": "up_geo", "arguments": args}, hdr))
	}
	w := call(map[string]any{"region": "eu", "note": "n"}, map[string]string{"Mcp-Param-Region": "eu", "Mcp-Param-Trace": "abc"})
	if res := m11Result(t, w); res["content"] == nil {
		t.Fatalf("validated call not served: %v", res)
	}
	cc := hooks.at(hookCallUpstream)
	if cc == nil {
		t.Fatal("the call never reached the upstream hook")
	}
	// The hand-off to the upstream client is the inbound request: the validated header and an
	// unrecognised one (carried on by value) are on it.
	got := protocol.ParamHeaders(cc.Request().Header)
	if got["Region"] != "eu" || got["Trace"] != "abc" {
		t.Fatalf("Mcp-Param headers on the chain's request = %v", got)
	}

	for name, tc := range map[string]struct {
		args map[string]any
		hdr  map[string]string
	}{
		"value differs":    {map[string]any{"region": "eu"}, map[string]string{"Mcp-Param-Region": "us"}},
		"header missing":   {map[string]any{"region": "eu"}, nil},
		"no such argument": {map[string]any{"note": "n"}, map[string]string{"Mcp-Param-Region": "eu"}},
	} {
		before := len(hooks.ran())
		resp := m11Refused(t, call(tc.args, tc.hdr), http.StatusBadRequest, protocol.CodeHeaderMismatch)
		if len(hooks.ran()) != before {
			t.Fatalf("%s: a mismatched Mcp-Param header reached the chain", name)
		}
		// The refusal names only what the client sent, never the tool's
		// parameter or a header the client did not send: a caller who may not
		// use the tool learns nothing of its schema from it.
		if msg := resp.Error.Message; strings.Contains(msg, `"region"`) || strings.Contains(msg, `"eu"`) ||
			(tc.hdr == nil && strings.Contains(strings.ToLower(msg), "region")) {
			t.Fatalf("%s: the refusal names the tool's schema: %s", name, msg)
		}
	}
	if up.received() != 1 {
		t.Fatalf("upstream got %d calls, want only the validated one", up.received())
	}
}

// m11Keys is a per-key authenticator: each key's allow and deny lists, in
// the KeyGrant shape, named like
// hook_acl_test.go's testKeyAuth.
type m11Keys map[string][2][]string

func (k m11Keys) AuthenticateKey(raw string) (KeyGrant, bool) {
	lists, ok := k[raw]
	if !ok {
		return KeyGrant{}, false
	}
	return KeyGrant{KeyID: "id-" + raw, AllowedTools: lists[0], DeniedTools: lists[1]}, true
}

func TestDiscoverAuthenticatedAndPrivate(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	s.SetKeyAuth(m11Keys{"sk-good": {}})
	discover := func(hdr map[string]string) *httptest.ResponseRecorder {
		req := m11Request(t, MethodServerDiscover, nil, hdr)
		w := httptest.NewRecorder()
		s.HandlePost(w, req)
		return w
	}

	// Anonymous: 401 with the challenge, as every method.
	w := discover(nil)
	if w.Code != http.StatusUnauthorized || !strings.HasPrefix(w.Header().Get("WWW-Authenticate"), "Bearer") {
		t.Fatalf("anonymous discover = %d %v, want 401 with a Bearer challenge", w.Code, w.Header())
	}

	w = discover(map[string]string{"Authorization": "Bearer sk-good"})
	if w.Code != http.StatusOK {
		t.Fatalf("discover = %d %s", w.Code, w.Body.String())
	}
	res := m11Result(t, w)
	var got struct {
		SupportedVersions []string                  `json:"supportedVersions"`
		Capabilities      ServerCapabilities        `json:"capabilities"`
		Meta              map[string]Implementation `json:"_meta"`
		ResultType        string                    `json:"resultType"`
		TTLMs             int64                     `json:"ttlMs"`
		CacheScope        string                    `json:"cacheScope"`
	}
	raw, _ := json.Marshal(res)
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	// listChanged stays undeclared: subscriptions/listen, the only route of a
	// 2026 change notification, is not served yet.
	if !reflect.DeepEqual(got.SupportedVersions, protocol.AllVersions()) || got.Capabilities.Tools == nil || got.Capabilities.Tools.ListChanged ||
		got.Meta[protocol.MetaServerInfo].Name != GatewayServerName || res["serverInfo"] != nil || got.ResultType != protocol.ResultComplete || got.TTLMs <= 0 ||
		got.CacheScope != protocol.CacheScopePrivate || got.Capabilities.Extensions == nil {
		t.Fatalf("discover result = %s", raw)
	}

	// server/discover is a 2026-07-28 method: a legacy request (no 2026 _meta,
	// no 2026 header), with or without a session, gets the unknown-method
	// answer, HTTP 200 on legacy. A 2026 header without the _meta is -32021.
	auth := map[string]string{"Authorization": "Bearer sk-good"}
	legacy := postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`4`), Method: MethodServerDiscover}, auth)
	if resp := m11Refused(t, legacy, http.StatusOK, ErrCodeMethodNotFound); resp.Error.Message != "unknown method: "+MethodServerDiscover {
		t.Fatalf("legacy discover = %+v", resp.Error)
	}
	sid := initializeSessionWith(t, s, auth)
	inSession := postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`5`), Method: MethodServerDiscover},
		map[string]string{"Authorization": "Bearer sk-good", "MCP-Session-Id": sid, "MCP-Protocol-Version": protocol.Version20251125})
	m11Refused(t, inSession, http.StatusOK, ErrCodeMethodNotFound)
	noMeta := discover(map[string]string{"Authorization": "Bearer sk-good"})
	if res := m11Result(t, noMeta); res["supportedVersions"] == nil {
		t.Fatal("a 2026 discover was not served")
	}
	headerOnly := serveM11(s, m11Request(t, MethodServerDiscover, map[string]any{"_meta": map[string]any{}}, auth))
	m11Refused(t, headerOnly, http.StatusBadRequest, protocol.CodeMissingClientCapability)

	// Private even with nothing that filters: capabilities may vary.
	open := newTestServer(t)
	defer open.Close()
	w = httptest.NewRecorder()
	open.HandlePost(w, m11Request(t, MethodServerDiscover, nil, nil))
	if res := m11Result(t, w); jsonString(t, res["cacheScope"]) != protocol.CacheScopePrivate {
		t.Fatalf("discover without auth: cacheScope %s", res["cacheScope"])
	}
}

func TestResultTypeAndCacheFields(t *testing.T) {
	up := startM11Upstream(t, false)
	s, _ := newM11Server(t, up)
	info := func(res map[string]json.RawMessage) string {
		var meta map[string]Implementation
		_ = json.Unmarshal(res["_meta"], &meta)
		if res["serverInfo"] != nil {
			return "a top-level serverInfo"
		}
		return meta[protocol.MetaServerInfo].Name
	}

	list := m11Result(t, serveM11(s, m11Request(t, MethodToolsList, nil, nil)))
	if jsonString(t, list["resultType"]) != protocol.ResultComplete || info(list) != GatewayServerName ||
		string(list["ttlMs"]) != "60000" || list["cacheScope"] == nil {
		t.Fatalf("2026 tools/list = %v", list)
	}
	call := m11Result(t, serveM11(s, m11Request(t, MethodToolsCall, map[string]any{"name": "up_weather", "arguments": map[string]any{}}, nil)))
	if jsonString(t, call["resultType"]) != protocol.ResultComplete || info(call) != GatewayServerName || call["ttlMs"] != nil || call["cacheScope"] != nil {
		t.Fatalf("2026 tools/call = %v", call)
	}
	// A read keeps the upstream's shorter ttlMs.
	read := m11Result(t, serveM11(s, m11Request(t, MethodResourcesRead, map[string]any{"uri": "file:///a"}, nil)))
	if jsonString(t, read["resultType"]) != protocol.ResultComplete || string(read["ttlMs"]) != "30000" || read["cacheScope"] == nil {
		t.Fatalf("2026 resources/read = %v", read)
	}
	// An in-band denial is a complete result too.
	replaceStep(t, toolsCallChain, hookAuthorizeTool, func(_ *Server, cc *CallContext) *Denial {
		return &Denial{Type: "permission_error", Code: "tool_denied", Message: "denied by the test"}
	})
	denied := m11Result(t, serveM11(s, m11Request(t, MethodToolsCall, map[string]any{"name": "up_weather", "arguments": map[string]any{}}, nil)))
	if string(denied["isError"]) != "true" || jsonString(t, denied["resultType"]) != protocol.ResultComplete {
		t.Fatalf("2026 in-band denial = %v", denied)
	}

	// A legacy answer carries none of these.
	sid := initializeSession(t, s)
	w := postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`4`), Method: MethodToolsList}, map[string]string{"MCP-Session-Id": sid})
	legacy := m11Result(t, w)
	for _, k := range []string{"resultType", "ttlMs", "cacheScope", "serverInfo"} {
		if legacy[k] != nil {
			t.Fatalf("legacy tools/list carries %s: %v", k, legacy)
		}
	}
	if info(legacy) != "" {
		t.Fatalf("legacy tools/list carries the 2026 serverInfo: %v", legacy)
	}
}

func TestPrivateCacheScopeWhenFiltered(t *testing.T) {
	up := startM11Upstream(t, false)
	scope := func(s *Server, hdr map[string]string) string {
		res := m11Result(t, serveM11(s, m11Request(t, MethodToolsList, nil, hdr)))
		return jsonString(t, res["cacheScope"])
	}
	// Nothing can vary by caller: no per-key auth, no principal.
	open, _ := newM11Server(t, up)
	if got := scope(open, nil); got != protocol.CacheScopePublic {
		t.Fatalf("unfiltered tools/list cacheScope = %s, want public", got)
	}
	// Per-key auth: every answer is per key, filtered by its lists or not.
	keyed, _ := newM11Server(t, up)
	keyed.SetKeyAuth(m11Keys{"sk-limited": {{"up_weather"}, nil}, "sk-all": {}})
	if got := scope(keyed, map[string]string{"Authorization": "Bearer sk-limited"}); got != protocol.CacheScopePrivate {
		t.Fatalf("filtered tools/list cacheScope = %s, want private", got)
	}
	if got := scope(keyed, map[string]string{"Authorization": "Bearer sk-all"}); got != protocol.CacheScopePrivate {
		t.Fatalf("keyed tools/list cacheScope = %s, want private", got)
	}
	// A principal or virtual server set by the chain makes it private too.
	replaceStep(t, toolsListChain, hookResolveVirtualServer, func(_ *Server, cc *CallContext) *Denial {
		cc.VirtualServer = "vs_test"
		return nil
	})
	if got := scope(open, nil); got != protocol.CacheScopePrivate {
		t.Fatalf("virtual-server tools/list cacheScope = %s, want private", got)
	}
}

func TestRawPassThroughStructuredContentResourceLink(t *testing.T) {
	up := startM11Upstream(t, false)
	s := newTestServer(t)
	defer s.Close()
	client := NewClient(ClientConfig{ServerID: "up", URL: up.URL, TransportType: "http"})
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := client.Connect(ctx); err != nil {
		t.Fatal(err)
	}
	s.RegisterClient(client)

	var want map[string]any
	if err := json.Unmarshal([]byte(m11CallResult), &want); err != nil {
		t.Fatal(err)
	}
	check := func(era string, result json.RawMessage) {
		t.Helper()
		var got map[string]any
		if err := json.Unmarshal(result, &got); err != nil {
			t.Fatalf("%s result %s: %v", era, result, err)
		}
		if meta, ok := got["_meta"].(map[string]any); ok {
			if era == "2026" {
				if meta[protocol.MetaServerInfo] == nil {
					t.Fatalf("2026 result without the gateway's serverInfo: %s", result)
				}
				delete(meta, protocol.MetaServerInfo) // the gateway's, beside the upstream's keys
			}
			// The meter's evidence block (io.futureagi/tool_call) is the gateway's
			// own key on every era's tools/call result;
			// the upstream's io.futureagi/* keys are still dropped.
			if meta[MetaKeyToolCall] == nil {
				t.Fatalf("%s result without the gateway's tool_call evidence: %s", era, result)
			}
			delete(meta, MetaKeyToolCall)
		}
		for _, k := range []string{"content", "structuredContent", "_meta", "x-apps"} {
			if !reflect.DeepEqual(got[k], want[k]) {
				t.Fatalf("%s: %s = %v, want %v (result %s)", era, k, got[k], want[k], result)
			}
		}
	}

	// Legacy: the upstream's result, member for member (the gateway
	// re-encodes the top level, so bytes are compared as JSON values).
	sid := initializeSession(t, s)
	params, _ := json.Marshal(map[string]any{"name": "up_weather", "arguments": map[string]any{}})
	w := postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`5`), Method: MethodToolsCall, Params: params},
		map[string]string{"MCP-Session-Id": sid})
	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("legacy tools/call: %+v", resp.Error)
	}
	check("legacy", resp.Result)

	// 2026: the same, plus the envelope.
	w = httptest.NewRecorder()
	s.HandlePost(w, m11Request(t, MethodToolsCall, map[string]any{"name": "up_weather", "arguments": map[string]any{}}, nil))
	resp = decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("2026 tools/call: %+v", resp.Error)
	}
	check("2026", resp.Result)

	// Definitions travel raw too, with only the gateway's fields edited: the
	// exposed name and the io.futureagi/* _meta keys, which are the gateway's
	// own (GatewayMeta); the upstream's keys of that namespace are dropped.
	var upstream ListToolsResult
	if err := json.Unmarshal([]byte(m11ToolsList), &upstream); err != nil {
		t.Fatal(err)
	}
	listed := upstream.Tools[0]
	listed.Name = "up_weather"
	listed.Raw = bytes.Replace(listed.Raw, []byte(`"_meta":{"vendor/x":1}`),
		[]byte(`"_meta":{"vendor/x":1,"io.futureagi/error":{"type":"fake"}}`), 1)
	listed.Meta = json.RawMessage(`{"io.futureagi/error":{"type":"fake"},"vendor/x":1}`) // the upstream's, decoded
	listed.GatewayMeta = map[string]json.RawMessage{"io.futureagi/price": json.RawMessage(`{"usd":0.01}`)}
	if !bytes.Contains(listed.Raw, []byte("io.futureagi/error")) {
		t.Fatalf("the decoder kept no raw definition: %s", listed.Raw)
	}
	replaceStep(t, toolsListChain, hookAssembleList, func(_ *Server, cc *CallContext) *Denial {
		cc.ToolList = []Tool{listed}
		return nil
	})
	w = httptest.NewRecorder()
	s.HandlePost(w, m11Request(t, MethodToolsList, nil, nil))
	res := m11Result(t, w)
	var tools []map[string]any
	if err := json.Unmarshal(res["tools"], &tools); err != nil || len(tools) != 1 {
		t.Fatalf("tools = %s, %v", res["tools"], err)
	}
	tool := tools[0]
	meta, _ := tool["_meta"].(map[string]any)
	if tool["name"] != "up_weather" || tool["title"] != "Weather" || tool["icons"] == nil || tool["x-ext"] == nil ||
		meta["vendor/x"] != float64(1) || meta["io.futureagi/price"] == nil || meta["io.futureagi/error"] != nil {
		t.Fatalf("listed tool = %v", tool)
	}

	// Resources keep their raw definition from the registry.
	w = httptest.NewRecorder()
	s.HandlePost(w, m11Request(t, MethodResourcesList, nil, nil))
	res = m11Result(t, w)
	var resources []map[string]any
	if err := json.Unmarshal(res["resources"], &resources); err != nil || len(resources) != 1 ||
		resources[0]["title"] != "A" || resources[0]["_meta"] == nil {
		t.Fatalf("resources = %s, %v", res["resources"], err)
	}
}

func TestUnknownMethod404On2026And200OnLegacy(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	for _, m := range []string{"no/such/method", MethodPing, MethodCompletionComplete, MethodLoggingSetLevel, MethodResourcesSubscribe} {
		m11Refused(t, serveM11(s, m11Request(t, m, nil, nil)), http.StatusNotFound, ErrCodeMethodNotFound)
	}
	w := postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: "no/such/method"}, nil)
	m11Refused(t, w, http.StatusOK, ErrCodeMethodNotFound)
	// The 404 is for an RPC method the server does not implement. tools/call,
	// resources/read and prompts/get are served: their chain's refusal of an
	// unknown tool, resource or prompt is answered with HTTP 200. On the
	// legacy view (no org MCP config) an unknown tool or prompt keeps today's
	// -32601; an unknown resource answers by era since virtual servers (-32602 on a
	// 2026-07-28 request, with CallContext.Era from the request).
	for _, c := range []struct {
		method string
		params map[string]any
		code   int
		msg    string
	}{
		{MethodToolsCall, map[string]any{"name": "no_such_tool", "arguments": map[string]any{}}, ErrCodeMethodNotFound, "tool not found: no_such_tool"},
		{MethodResourcesRead, map[string]any{"uri": "file:///none"}, ErrCodeInvalidParams, "Resource not found"},
		{MethodPromptsGet, map[string]any{"name": "no_such_prompt"}, ErrCodeMethodNotFound, "prompt not found: no_such_prompt"},
	} {
		resp := m11Refused(t, serveM11(s, m11Request(t, c.method, c.params, nil)), http.StatusOK, c.code)
		if resp.Error.Message != c.msg {
			t.Fatalf("%s denial = %+v", c.method, resp.Error)
		}
	}
	// Ping stays on legacy requests.
	if w := postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`2`), Method: MethodPing}, nil); w.Code != http.StatusOK {
		t.Fatalf("legacy ping = %d", w.Code)
	} else if resp := decodeResponse(t, w); resp.Error != nil {
		t.Fatalf("legacy ping = %+v", resp.Error)
	}
}

func TestNotification202(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	empty202 := func(name string, w *httptest.ResponseRecorder) {
		t.Helper()
		if w.Code != http.StatusAccepted || w.Body.Len() != 0 {
			t.Fatalf("%s = %d %q, want an empty 202", name, w.Code, w.Body.String())
		}
	}
	for _, m := range []string{MethodInitialized, MethodCancelled, MethodProgress, MethodRootsListChanged, "notifications/vendor/x"} {
		empty202("legacy "+m, postMCP(t, s, &Message{JSONRPC: "2.0", Method: m}, nil))
		req := m11Request(t, m, nil, nil)
		var body map[string]any
		raw, _ := io.ReadAll(req.Body)
		_ = json.Unmarshal(raw, &body)
		delete(body, "id")
		raw, _ = json.Marshal(body)
		req.Body = io.NopCloser(bytes.NewReader(raw))
		empty202("2026 "+m, serveM11(s, req))
	}
	// A client's response to a server request.
	empty202("response", postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`9`), Result: json.RawMessage(`{}`)}, nil))
	empty202("error response", postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`9`), Error: &RPCError{Code: -1, Message: "no"}}, nil))
	// A request method without an id cannot be answered.
	m11Refused(t, postMCP(t, s, &Message{JSONRPC: "2.0", Method: MethodToolsList}, nil), http.StatusBadRequest, ErrCodeInvalidRequest)
}

func TestAccept406ContentType415(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	for accept, served := range map[string][2]bool{ // 2026, legacy
		"application/json":                        {false, false},
		"text/event-stream":                       {false, false},
		"application/json, text/event-stream;q=0": {false, false},
		"text/html":                               {false, false},
		"application/json, text/event-stream":     {true, true},
		"*/*":                                     {true, true},
		"application/*, text/*":                   {true, true},
		// No Accept: a 2026 client MUST send it (406); a legacy one is served,
		// as RFC 9110 reads an absent Accept as any type.
		"": {false, true},
	} {
		for i, era := range []string{"2026", "legacy"} {
			ok := served[i]
			var w *httptest.ResponseRecorder
			if era == "2026" {
				w = serveM11(s, m11Request(t, MethodToolsList, nil, map[string]string{"Accept": accept}))
			} else {
				hdr := map[string]string{}
				if accept != "" {
					hdr["Accept"] = accept
				}
				w = postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPing}, hdr)
			}
			if ok && w.Code == http.StatusNotAcceptable || !ok && w.Code != http.StatusNotAcceptable {
				t.Fatalf("%s Accept %q = %d %s", era, accept, w.Code, w.Body.String())
			}
		}
	}
	for ct, ok := range map[string]bool{
		"application/json":                true,
		"application/json; charset=utf-8": true,
		"text/plain":                      false,
		"":                                false,
	} {
		w := serveM11(s, m11Request(t, MethodToolsList, nil, map[string]string{"Content-Type": ct}))
		if ok && w.Code == http.StatusUnsupportedMediaType || !ok && w.Code != http.StatusUnsupportedMediaType {
			t.Fatalf("Content-Type %q = %d %s", ct, w.Code, w.Body.String())
		}
	}
}

func TestBodyCap413(t *testing.T) {
	// The default cap is mcp.max_body_bytes' default, 4 MiB.
	s := newTestServer(t)
	defer s.Close()
	big := `{"jsonrpc":"2.0","id":1,"method":"ping","params":{"pad":"` + strings.Repeat("x", 5<<20) + `"}}`
	req := httptest.NewRequest(http.MethodPost, "/mcp", strings.NewReader(big))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	s.HandlePost(w, req)
	if w.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("5 MiB body = %d, want 413", w.Code)
	}

	// The configured cap.
	s.SetProtocol(protocol.Settings{MaxBodyBytes: 1024})
	pad := func(n int) string {
		return `{"jsonrpc":"2.0","id":1,"method":"ping","params":{"pad":"` + strings.Repeat("x", n) + `"}}`
	}
	for n, want := range map[int]int{2048: http.StatusRequestEntityTooLarge, 100: http.StatusOK} {
		req := httptest.NewRequest(http.MethodPost, "/mcp", strings.NewReader(pad(n)))
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		s.HandlePost(w, req)
		if w.Code != want {
			t.Fatalf("%d-byte pad with a 1 KiB cap = %d, want %d", n, w.Code, want)
		}
	}
}

func TestOriginAllowlist403(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	s.SetProtocol(protocol.Settings{AllowedOrigins: []string{"https://app.example"}, OwnHosts: []string{"gw.example"}})
	for _, tc := range []struct {
		origin, host string
		want         int
	}{
		{"https://evil.example", "", http.StatusForbidden},
		{"null", "", http.StatusForbidden},
		{"https://app.example", "", http.StatusOK},
		{"https://gw.example", "", http.StatusOK},                                   // mcp.public_url's host
		{"http://127.0.0.1:8080", "127.0.0.1:8080", http.StatusOK},                  // same origin, an IP literal
		{"http://127.0.0.1:9090", "127.0.0.1:8080", http.StatusForbidden},           // another port
		{"http://rebind.example:8080", "rebind.example:8080", http.StatusForbidden}, // DNS rebinding
		{"", "", http.StatusOK},
	} {
		hdr := map[string]string{}
		if tc.origin != "" {
			hdr["Origin"] = tc.origin
		}
		body, _ := json.Marshal(&Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPing})
		req := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		if tc.host != "" {
			req.Host = tc.host
		}
		for k, v := range hdr {
			req.Header.Set(k, v)
		}
		w := serveM11(s, req)
		if w.Code != tc.want {
			t.Fatalf("Origin %q at Host %q = %d %s, want %d", tc.origin, tc.host, w.Code, w.Body.String(), tc.want)
		}
		if tc.want == http.StatusForbidden {
			if resp := decodeResponse(t, w); resp.Error == nil || !strings.Contains(string(resp.Error.Data), "origin_not_allowed") {
				t.Fatalf("Origin %q refusal = %s", tc.origin, w.Body.String())
			}
		}
	}
	// 2026 requests follow the same rule.
	w := serveM11(s, m11Request(t, MethodToolsList, nil, map[string]string{"Origin": "https://evil.example"}))
	if w.Code != http.StatusForbidden {
		t.Fatalf("2026 request from evil origin = %d", w.Code)
	}
}

// m11ClientRequest is m11Request as a client request to a live gateway.
func m11ClientRequest(t *testing.T, ctx context.Context, url, method string, params map[string]any, hdr map[string]string) *http.Request {
	t.Helper()
	r := m11Request(t, method, params, hdr)
	body, _ := io.ReadAll(r.Body)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header = r.Header.Clone()
	return req
}

// sseLines reads an SSE body line by line onto a channel.
func sseLines(body io.Reader) <-chan string {
	ch := make(chan string, 64)
	go func() {
		defer close(ch)
		sc := bufio.NewScanner(body)
		for sc.Scan() {
			ch <- sc.Text()
		}
	}()
	return ch
}

// awaitLine returns the first line with prefix, failing after d.
func awaitLine(t *testing.T, lines <-chan string, prefix string, d time.Duration) string {
	t.Helper()
	deadline := time.After(d)
	for {
		select {
		case l, ok := <-lines:
			if !ok {
				t.Fatalf("stream ended before a %q line", prefix)
			}
			if strings.HasPrefix(l, prefix) {
				return l
			}
		case <-deadline:
			t.Fatalf("no %q line within %s", prefix, d)
		}
	}
}

func TestSSEResponseCancelOnDisconnect(t *testing.T) {
	weather := map[string]any{"name": "up_weather", "arguments": map[string]any{}}
	streamed := func(t *testing.T) (*m11Upstream, string, *Server) {
		up := startM11Upstream(t, true)
		s, _ := newM11Server(t, up)
		s.SetProtocol(protocol.Settings{StreamAfter: 50 * time.Millisecond, KeepAlive: 20 * time.Millisecond})
		gw := httptest.NewServer(http.HandlerFunc(s.HandlePost))
		t.Cleanup(gw.Close)
		return up, gw.URL + "/mcp", s
	}

	t.Run("completes", func(t *testing.T) {
		up, url, _ := streamed(t)
		resp, err := http.DefaultClient.Do(m11ClientRequest(t, context.Background(), url, MethodToolsCall, weather, nil))
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK || resp.Header.Get("Content-Type") != protocol.MediaSSE {
			t.Fatalf("long call answered %d %s, want an SSE stream", resp.StatusCode, resp.Header.Get("Content-Type"))
		}
		lines := sseLines(resp.Body)
		awaitLine(t, lines, ": keep-alive", 5*time.Second)
		close(up.release)
		data := strings.TrimPrefix(awaitLine(t, lines, "data: ", 5*time.Second), "data: ")
		var msg Message
		if err := json.Unmarshal([]byte(data), &msg); err != nil || msg.Error != nil {
			t.Fatalf("final event %s: %v", data, err)
		}
		var res map[string]json.RawMessage
		_ = json.Unmarshal(msg.Result, &res)
		if res["structuredContent"] == nil || jsonString(t, res["resultType"]) != protocol.ResultComplete {
			t.Fatalf("streamed result = %s", msg.Result)
		}
	})

	t.Run("client goes away", func(t *testing.T) {
		up, url, _ := streamed(t)
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		resp, err := http.DefaultClient.Do(m11ClientRequest(t, ctx, url, MethodToolsCall, weather, nil))
		if err != nil {
			t.Fatal(err)
		}
		lines := sseLines(resp.Body)
		awaitLine(t, lines, ": keep-alive", 5*time.Second)
		cancel() // the client closes the stream
		resp.Body.Close()
		select {
		case <-up.gone:
		case <-time.After(5 * time.Second):
			t.Fatal("closing the response stream did not cancel the upstream call")
		}
	})

	t.Run("no SSE in Accept stays JSON", func(t *testing.T) {
		// Only a legacy request can leave SSE out of Accept: a 2026 one must
		// list both types (406, below). A legacy POST in its session without
		// an Accept header is served, and a call that runs past StreamAfter
		// still answers one complete JSON object.
		up, url, s := streamed(t)
		sid := initializeSession(t, s)
		const hold = 200 * time.Millisecond
		go func() { time.Sleep(hold); close(up.release) }()
		body, _ := json.Marshal(&Message{JSONRPC: "2.0", ID: json.RawMessage(`7`), Method: MethodToolsCall,
			Params: json.RawMessage(`{"name":"up_weather","arguments":{}}`)})
		req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("MCP-Session-Id", sid)
		start := time.Now()
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		raw, _ := io.ReadAll(resp.Body)
		if took := time.Since(start); took < hold-50*time.Millisecond {
			t.Fatalf("the call took %s, not past StreamAfter", took)
		}
		if resp.StatusCode != http.StatusOK || resp.Header.Get("Content-Type") != "application/json" {
			t.Fatalf("a legacy call without Accept answered %d %s: %s", resp.StatusCode, resp.Header.Get("Content-Type"), raw)
		}
		var msg Message
		if err := json.Unmarshal(raw, &msg); err != nil || msg.Error != nil || up.received() != 1 {
			t.Fatalf("legacy answer %s (%v), upstream calls %d", raw, err, up.received())
		}
		var res map[string]json.RawMessage
		if json.Unmarshal(msg.Result, &res) != nil || res["structuredContent"] == nil || res["content"] == nil {
			t.Fatalf("legacy result = %s", msg.Result)
		}

		// A 2026 request without Accept is refused before anything runs.
		refused, err := http.DefaultClient.Do(m11ClientRequest(t, context.Background(), url, MethodToolsCall, weather, map[string]string{"Accept": ""}))
		if err != nil {
			t.Fatal(err)
		}
		defer refused.Body.Close()
		if refused.StatusCode != http.StatusNotAcceptable || up.received() != 1 {
			t.Fatalf("a 2026 call without Accept = %d, upstream calls %d; want 406 and none", refused.StatusCode, up.received())
		}
	})
}

func TestSealedCursorTamperRejected(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	s.Registry().RegisterServer("srv", []Tool{
		{Name: "c", InputSchema: json.RawMessage(`{"type":"object"}`)},
		{Name: "a", InputSchema: json.RawMessage(`{"type":"object"}`)},
		{Name: "b", InputSchema: json.RawMessage(`{"type":"object"}`)},
	})
	codec, err := NewStateCodec([]string{strings.Repeat("k", 32)}, false)
	if err != nil {
		t.Fatal(err)
	}
	s.SetProtocol(protocol.Settings{PageSize: 1, Cursors: codec})
	list := func(method, cursor string) *httptest.ResponseRecorder {
		params := map[string]any{}
		if cursor != "" {
			params["cursor"] = cursor
		}
		return serveM11(s, m11Request(t, method, params, nil))
	}
	page := func(cursor string) ([]string, string) {
		t.Helper()
		res := m11Result(t, list(MethodToolsList, cursor))
		var tools []Tool
		_ = json.Unmarshal(res["tools"], &tools)
		var next string
		if res["nextCursor"] != nil {
			next = jsonString(t, res["nextCursor"])
		}
		names := []string{}
		for _, tl := range tools {
			names = append(names, tl.Name)
		}
		return names, next
	}

	var all []string
	names, next := page("")
	all = append(all, names...)
	first := next
	for next != "" {
		if strings.Contains(next, "srv_") || strings.Contains(next, `"o"`) {
			t.Fatalf("cursor %q is not opaque", next)
		}
		names, next = page(next)
		all = append(all, names...)
	}
	if !reflect.DeepEqual(all, []string{"srv_a", "srv_b", "srv_c"}) {
		t.Fatalf("pages = %v", all)
	}

	tampered := []byte(first)
	tampered[len(tampered)/2] ^= 0x01
	other, _ := NewStateCodec([]string{strings.Repeat("o", 32)}, false)
	foreign, _ := protocol.SealCursor(other, protocol.Cursor{Method: MethodToolsList, Offset: 1, Binding: cursorBinding(&CallContext{})})
	for name, w := range map[string]*httptest.ResponseRecorder{
		"tampered":       list(MethodToolsList, string(tampered)),
		"garbage":        list(MethodToolsList, "not-a-cursor"),
		"another method": list(MethodResourcesList, first),
		"another key":    list(MethodToolsList, foreign),
	} {
		m11Refused(t, w, http.StatusOK, ErrCodeInvalidParams)
		_ = name
	}

	codec.now = func() time.Time { return time.Now().Add(protocol.CursorTTL + time.Minute) }
	m11Refused(t, list(MethodToolsList, first), http.StatusOK, ErrCodeInvalidParams)
}

// m11Log is an upstream log notification at level.
func m11Log(level string) *Message {
	return &Message{JSONRPC: "2.0", Method: MethodLogMessage, Params: json.RawMessage(`{"level":"` + level + `","data":"x"}`)}
}

// sseData returns the data lines of an SSE body.
func sseData(body string) []string {
	var out []string
	for _, l := range strings.Split(body, "\n") {
		if strings.HasPrefix(l, "data: ") {
			out = append(out, strings.TrimPrefix(l, "data: "))
		}
	}
	return out
}

func TestLogMessagesDroppedWithoutLogLevel(t *testing.T) {
	up := startM11Upstream(t, false)
	s, _ := newM11Server(t, up)
	var relayed []bool
	notes := []*Message{m11Log("info"), m11Log("error")}
	replaceStep(t, toolsCallChain, hookScanArgs, func(_ *Server, cc *CallContext) *Denial {
		relayed = relayed[:0]
		for _, n := range notes {
			relayed = append(relayed, relayNotification(cc.Context(), n))
		}
		return nil
	})
	weather := func() map[string]any { return map[string]any{"name": "up_weather", "arguments": map[string]any{}} }

	// No logLevel in _meta: nothing is relayed, and the answer stays JSON.
	w := serveM11(s, m11Request(t, MethodToolsCall, weather(), nil))
	if !reflect.DeepEqual(relayed, []bool{false, false}) || w.Header().Get("Content-Type") != "application/json" {
		t.Fatalf("without logLevel: relayed %v, answer %s", relayed, w.Header().Get("Content-Type"))
	}

	// logLevel warning: the error goes out on the call's stream, the info
	// message does not; the result is the stream's last event.
	params := weather()
	meta := m11Meta()
	meta[protocol.MetaLogLevel] = "warning"
	params["_meta"] = meta
	w = serveM11(s, m11Request(t, MethodToolsCall, params, nil))
	data := sseData(w.Body.String())
	if !reflect.DeepEqual(relayed, []bool{false, true}) || w.Header().Get("Content-Type") != protocol.MediaSSE || len(data) != 2 ||
		!strings.Contains(data[0], `"level":"error"`) || !strings.Contains(data[1], `"structuredContent"`) {
		t.Fatalf("logLevel warning: relayed %v, body %q", relayed, w.Body.String())
	}

	// Legacy: log messages stay dropped (logging/setLevel is not served);
	// progress still reaches a client that accepts SSE.
	notes = []*Message{m11Log("emergency"), {JSONRPC: "2.0", Method: MethodProgress, Params: json.RawMessage(`{"progressToken":1,"progress":1}`)}}
	sid := initializeSession(t, s)
	raw, _ := json.Marshal(weather())
	w = postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`8`), Method: MethodToolsCall, Params: raw},
		map[string]string{"MCP-Session-Id": sid, "Accept": "application/json, text/event-stream"})
	data = sseData(w.Body.String())
	if !reflect.DeepEqual(relayed, []bool{false, true}) || len(data) != 2 || !strings.Contains(data[0], MethodProgress) {
		t.Fatalf("legacy: relayed %v, body %q", relayed, w.Body.String())
	}
}

// m11Rewriter is a scanner that rewrites every result.
type m11Rewriter struct{}

const m11Rewritten = `{"content":[{"type":"text","text":"[redacted by scanner]"}],"structuredContent":{"redacted":true}}`

func (m11Rewriter) ScanArgs(*CallContext) *Denial { return nil }
func (m11Rewriter) ScanResult(cc *CallContext) *Denial {
	cc.Result = json.RawMessage(m11Rewritten)
	return nil
}
func (m11Rewriter) ScanList(_ *CallContext, tools []Tool) []Tool { return tools }

func TestScannerRewriteReachesClient(t *testing.T) {
	up := startM11Upstream(t, false)
	s, _ := newM11Server(t, up)
	s.SetDeps(Deps{Scanner: m11Rewriter{}})
	var want map[string]any
	_ = json.Unmarshal([]byte(m11Rewritten), &want)
	check := func(era string, result json.RawMessage) {
		t.Helper()
		var got map[string]any
		_ = json.Unmarshal(result, &got)
		if !reflect.DeepEqual(got["content"], want["content"]) || !reflect.DeepEqual(got["structuredContent"], want["structuredContent"]) {
			t.Fatalf("%s: client got %s, want the scanner's rewrite", era, result)
		}
	}
	sid := initializeSession(t, s)
	raw, _ := json.Marshal(map[string]any{"name": "up_weather", "arguments": map[string]any{}})
	resp := decodeResponse(t, postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`6`), Method: MethodToolsCall, Params: raw},
		map[string]string{"MCP-Session-Id": sid}))
	check("legacy", resp.Result)
	resp = decodeResponse(t, serveM11(s, m11Request(t, MethodToolsCall, map[string]any{"name": "up_weather", "arguments": map[string]any{}}, nil)))
	check("2026", resp.Result)
}

// m11Guard blocks a tool result any text part of which contains "EVIL".
type m11Guard struct{}

func (m11Guard) CheckPre(string, string, map[string]interface{}) string { return "" }
func (m11Guard) CheckPost(_, _ string, r *ToolCallResult) string {
	for _, p := range r.Content {
		if p.Type == "text" && strings.Contains(p.Text, "EVIL") {
			return "blocked by the test guard"
		}
	}
	return ""
}

// TestGuardSeesWhatClientReceives: with an output guard set, no string the
// guard would refuse reaches the client from anywhere in the result, though
// the default scanner checks only the typed decode's text parts.
func TestGuardSeesWhatClientReceives(t *testing.T) {
	up := startM11Upstream(t, false)
	s, hooks := newM11Server(t, up)
	s.SetGuard(m11Guard{})
	sid := initializeSession(t, s)
	call := func(era string) *httptest.ResponseRecorder {
		if era == "2026" {
			return serveM11(s, m11Request(t, MethodToolsCall, map[string]any{"name": "up_weather", "arguments": map[string]any{}}, nil))
		}
		raw, _ := json.Marshal(map[string]any{"name": "up_weather", "arguments": map[string]any{}})
		return postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`7`), Method: MethodToolsCall, Params: raw},
			map[string]string{"MCP-Session-Id": sid})
	}
	for name, result := range map[string]string{
		// The typed decode keeps the last case-insensitive match ("benign");
		// a client's parser reads "text".
		"case-variant duplicate key": `{"content":[{"type":"text","text":"EVIL payload","TEXT":"benign"}]}`,
		"structuredContent":          `{"content":[{"type":"text","text":"benign"}],"structuredContent":{"note":"EVIL payload"}}`,
		"structuredContent key":      `{"content":[{"type":"text","text":"benign"}],"structuredContent":{"EVIL payload":1}}`,
		"embedded resource":          `{"content":[{"type":"resource","resource":{"uri":"file:///x","text":"EVIL payload"}}]}`,
		"resource link":              `{"content":[{"type":"resource_link","uri":"file:///r","name":"r","description":"EVIL payload"}]}`,
		"upstream _meta":             `{"content":[{"type":"text","text":"benign"}],"_meta":{"vendor/note":"EVIL payload"}}`,
	} {
		up.setResult(MethodToolsCall, result)
		for _, era := range []string{"2026", "legacy"} {
			w := call(era)
			if strings.Contains(w.Body.String(), "EVIL") {
				t.Fatalf("%s, %s: the client received what the guard refuses: %s", name, era, w.Body.String())
			}
			resp := decodeResponse(t, w)
			if resp.Error == nil || resp.Error.Code != ErrCodeInvalidRequest || !strings.Contains(resp.Error.Message, "guardrail") {
				t.Fatalf("%s, %s: answer %s, want the tool_result_blocked error", name, era, w.Body.String())
			}
			if cc := hooks.at(hookCallUpstream); cc.Decision == nil || cc.Decision.Code != "tool_result_blocked" {
				t.Fatalf("%s, %s: the call's record does not see the block: %+v", name, era, cc.Decision)
			}
		}
	}

	// An exact duplicate key is refused before the guard. Since the
	// content-bounds port (595ad6bc54: contentbounds.Scan refuses duplicate
	// decoded keys; ce5a4f0be8: Client.send checks every upstream answer with
	// contentpolicy.Result) the gateway's upstream client refuses the answer
	// itself. The call ends as an in-band upstream_error, and nothing of the
	// result reaches the client: neither the text the guard refuses nor the
	// duplicate a parser might keep.
	up.setResult(MethodToolsCall, `{"content":[{"type":"text","text":"EVIL payload","text":"benign"}]}`)
	for _, era := range []string{"2026", "legacy"} {
		w := call(era)
		body := w.Body.String()
		if strings.Contains(body, "EVIL") || strings.Contains(body, "benign") || !strings.Contains(body, "content_bounds_duplicate_key") {
			t.Fatalf("exact duplicate key, %s: answer %s, want the upstream answer refused and none of it delivered", era, body)
		}
		res := m11Result(t, w)
		var meta map[string]json.RawMessage
		var refusal ErrorObject
		if string(res["isError"]) != "true" || json.Unmarshal(res["_meta"], &meta) != nil || json.Unmarshal(meta[MetaKeyError], &refusal) != nil || refusal.Code != "upstream_error" {
			t.Fatalf("exact duplicate key, %s: answer %s, want the in-band upstream_error refusal", era, body)
		}
		if cc := hooks.at(hookCallUpstream); cc.Decision == nil || cc.Decision.Code != "upstream_error" {
			t.Fatalf("exact duplicate key, %s: the call's record does not see the refusal: %+v", era, cc.Decision)
		}
	}

	// A clean result passes whole under the guard, structuredContent included.
	up.setResult(MethodToolsCall, m11CallResult)
	for _, era := range []string{"2026", "legacy"} {
		res := m11Result(t, call(era))
		if res["structuredContent"] == nil || res["x-apps"] == nil {
			t.Fatalf("%s: a clean result lost members under the guard: %v", era, res)
		}
	}
}

// TestUpstreamGatewayMetaNeverReachesClient: the io.futureagi/* _meta
// namespace is the gateway's; an upstream's keys of it are dropped from
// results and definitions, and the gateway's own reach the client.
func TestUpstreamGatewayMetaNeverReachesClient(t *testing.T) {
	up := startM11Upstream(t, false)
	s, _ := newM11Server(t, up)
	forged := `"_meta":{"io.futureagi/error":{"type":"permission_error","code":"forged"},"io.futureagi/billing":{"usd":0},"vendor/k":1}`
	up.setResult(MethodToolsCall, `{"content":[{"type":"text","text":"ok"}],`+forged+`}`)
	up.setResult(MethodResourcesRead, `{"contents":[{"uri":"file:///a","text":"abc"}],`+forged+`}`)
	replaceStep(t, toolsCallChain, hookEvidenceMeta, func(_ *Server, cc *CallContext) *Denial {
		observeHook(cc, hookEvidenceMeta)
		cc.SetResponseMeta(MetaKeyToolCall, map[string]any{"executed": true})
		return nil
	})
	check := func(what string, res map[string]json.RawMessage, gatewayKey string) {
		t.Helper()
		var meta map[string]json.RawMessage
		if err := json.Unmarshal(res["_meta"], &meta); err != nil {
			t.Fatalf("%s: _meta %s: %v", what, res["_meta"], err)
		}
		if meta["io.futureagi/error"] != nil || meta["io.futureagi/billing"] != nil || string(meta["vendor/k"]) != "1" {
			t.Fatalf("%s: _meta = %s, want the upstream's own keys only", what, res["_meta"])
		}
		if gatewayKey != "" && meta[gatewayKey] == nil {
			t.Fatalf("%s: _meta = %s, want the gateway's %s", what, res["_meta"], gatewayKey)
		}
	}
	sid := initializeSession(t, s)
	raw, _ := json.Marshal(map[string]any{"name": "up_weather", "arguments": map[string]any{}})
	legacy := m11Result(t, postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`8`), Method: MethodToolsCall, Params: raw},
		map[string]string{"MCP-Session-Id": sid}))
	check("legacy tools/call", legacy, MetaKeyToolCall)
	check("2026 tools/call", m11Result(t, serveM11(s, m11Request(t, MethodToolsCall,
		map[string]any{"name": "up_weather", "arguments": map[string]any{}}, nil))), MetaKeyToolCall)
	check("2026 resources/read", m11Result(t, serveM11(s, m11Request(t, MethodResourcesRead,
		map[string]any{"uri": "file:///a"}, nil))), protocol.MetaServerInfo)

	// Definitions: an upstream's io.futureagi/* key on a listed tool, raw or
	// typed, is dropped; the gateway's own (GatewayMeta) is kept.
	replaceStep(t, toolsListChain, hookAssembleList, func(_ *Server, cc *CallContext) *Denial {
		cc.ToolList = []Tool{
			{Name: "up_raw", Raw: json.RawMessage(`{"name":"raw","inputSchema":{"type":"object"},` + forged + `}`)},
			{Name: "up_typed", InputSchema: json.RawMessage(`{"type":"object"}`), Meta: json.RawMessage(`{"io.futureagi/error":{},"vendor/k":1}`),
				GatewayMeta: map[string]json.RawMessage{"io.futureagi/price": json.RawMessage(`{"usd":1}`)}},
		}
		return nil
	})
	res := m11Result(t, serveM11(s, m11Request(t, MethodToolsList, nil, nil)))
	var tools []map[string]json.RawMessage
	if err := json.Unmarshal(res["tools"], &tools); err != nil || len(tools) != 2 {
		t.Fatalf("tools = %s, %v", res["tools"], err)
	}
	check("raw definition", tools[0], "")
	check("typed definition", tools[1], "io.futureagi/price")
}

func TestRefuseStatelessStream405(t *testing.T) {
	for _, method := range []string{http.MethodGet, http.MethodDelete} {
		w := httptest.NewRecorder()
		if !refuseStatelessStream(w, httptest.NewRequest(method, "/mcp", nil)) || w.Code != http.StatusMethodNotAllowed || w.Header().Get("Allow") != http.MethodPost {
			t.Fatalf("%s without a session = %d %v, want 405 Allow: POST", method, w.Code, w.Header())
		}
		req := httptest.NewRequest(method, "/mcp", nil)
		req.Header.Set("Mcp-Session-Id", "s1")
		if refuseStatelessStream(httptest.NewRecorder(), req) {
			t.Fatalf("%s on a legacy session was refused", method)
		}
	}
}
