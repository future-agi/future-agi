package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/oklog/ulid/v2"

	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/mcp/meter"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The meter test harness: an MCP server with one upstream "up" whose tools
// answer by name, a recording event sink, a counting ToolStats, a test
// meter, and a per-test hook observer that sets the call-context fields the
// same-wave lanes fill (principal, agent, org config, terms).

const meterToolTimeout = 300 * time.Millisecond

// startMeterUpstream serves tools ok (a result with its own _meta), fail (an
// isError result), slow (answers after twice the gateway's tool timeout),
// broken (HTTP 500), leaky (an HTTP 401 that echoes a bearer), pii (an HTTP
// 404 whose text names a person and echoes a bearer) and big (a 40 KiB
// result).
func startMeterUpstream(t *testing.T) *httptest.Server {
	t.Helper()
	tools := `[{"name":"ok","inputSchema":{"type":"object"}},{"name":"fail","inputSchema":{"type":"object"}},` +
		`{"name":"slow","inputSchema":{"type":"object"}},{"name":"broken","inputSchema":{"type":"object"}},` +
		`{"name":"big","inputSchema":{"type":"object"}},{"name":"leaky","inputSchema":{"type":"object"}},` +
		`{"name":"pii","inputSchema":{"type":"object"}}]`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var msg Message
		if json.Unmarshal(body, &msg) != nil {
			http.Error(w, "bad json", http.StatusBadRequest)
			return
		}
		if msg.IsNotification() {
			w.WriteHeader(http.StatusAccepted)
			return
		}
		result := `{}`
		switch msg.Method {
		case MethodInitialize:
			result = `{"protocolVersion":"2025-11-25","capabilities":{"tools":{}},"serverInfo":{"name":"up","version":"1"}}`
		case MethodToolsList:
			result = `{"tools":` + tools + `}`
		case MethodToolsCall:
			var p struct {
				Name string `json:"name"`
			}
			_ = json.Unmarshal(msg.Params, &p)
			switch p.Name {
			case "ok":
				result = `{"content":[{"type":"text","text":"done"}],"_meta":{"up":1}}`
			case "fail":
				result = `{"content":[{"type":"text","text":"the tool failed"}],"isError":true}`
			case "slow":
				select {
				case <-time.After(2 * meterToolTimeout):
				case <-r.Context().Done():
					return
				}
				result = `{"content":[{"type":"text","text":"late"}]}`
			case "broken":
				http.Error(w, "upstream exploded", http.StatusInternalServerError)
				return
			// The two error tools answer a JSON-RPC error whose message is the
			// text: the upstream client's client keeps an upstream's JSON-RPC error message
			// (credential-scrubbed) and drops an HTTP error's body.
			case "leaky": // an upstream error that echoes the caller's credential
				meterRPCError(w, msg.ID, "rejected Authorization: Bearer sk-fake-test-value-0000")
				return
			case "pii": // an upstream error naming a person, and echoing a credential
				meterRPCError(w, msg.ID, "user bob@corp.example not found (Authorization: Bearer sk-fake-test-value-0001)")
				return
			case "big":
				result = `{"content":[{"type":"text","text":"` + strings.Repeat("z", 40<<10) + `"}]}`
			}
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"jsonrpc":"2.0","id":` + string(msg.ID) + `,"result":` + result + `}`))
	}))
	t.Cleanup(srv.Close)
	return srv
}

// meterRPCError answers a JSON-RPC error carrying message.
func meterRPCError(w http.ResponseWriter, id json.RawMessage, message string) {
	b, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": id, "error": map[string]any{"code": -32000, "message": message}})
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write(b)
}

// recordSink is a Deps.Events that keeps every event.
type recordSink struct {
	mu     sync.Mutex
	kinds  []string
	events []any
}

func (s *recordSink) add(kind string, payload any) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.kinds = append(s.kinds, kind)
	s.events = append(s.events, payload)
}

// records returns the tool_call events as records, failing on any other.
func (s *recordSink) records(t *testing.T) []ToolCallRecord {
	t.Helper()
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]ToolCallRecord, 0, len(s.events))
	for i, e := range s.events {
		rec, ok := e.(ToolCallRecord)
		if s.kinds[i] != EventKindToolCall || !ok {
			t.Fatalf("event %d is %q %T, want a tool_call ToolCallRecord", i, s.kinds[i], e)
		}
		out = append(out, rec)
	}
	return out
}

// only returns the one record, failing unless exactly one was emitted.
func (s *recordSink) only(t *testing.T) ToolCallRecord {
	t.Helper()
	recs := s.records(t)
	if len(recs) != 1 {
		t.Fatalf("%d records emitted, want exactly one: %+v", len(recs), recs)
	}
	return recs[0]
}

func (s *recordSink) reset() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.kinds, s.events = nil, nil
}

// wireRecord is rec as the log webhook carries it: its JSON, with the kind
// the flusher's EnqueueEvent adds.
func wireRecord(t *testing.T, rec ToolCallRecord) map[string]any {
	t.Helper()
	raw, err := json.Marshal(rec)
	if err != nil {
		t.Fatalf("marshal record: %v", err)
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("record JSON is not an object: %v (%s)", err, raw)
	}
	m["kind"] = EventKindToolCall
	return m
}

// assertGolden fails when rec does not validate against mcp-tool-call-v1.
func assertGolden(t *testing.T, rec ToolCallRecord) {
	t.Helper()
	if errs := schematest.Validate(t, "mcp-tool-call-v1.schema.json", wireRecord(t, rec)); len(errs) > 0 {
		t.Fatalf("record %s does not match mcp-tool-call-v1:\n%s", rec.Outcome, strings.Join(errs, "\n"))
	}
}

// countingStats is a Deps.ToolStats that keeps every call context it sees.
type countingStats struct {
	mu  sync.Mutex
	got []*CallContext
}

func (c *countingStats) Observe(cc *CallContext) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.got = append(c.got, cc)
}

func (c *countingStats) seen() []*CallContext {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]*CallContext(nil), c.got...)
}

// keyLists is a KeyAuthenticator: key "k-deny" may not call up_fail, any
// other non-empty key is valid with no lists.
type keyLists struct{}

func (keyLists) AuthenticateKey(raw string) (KeyGrant, bool) {
	// The KeyGrant shape, named like
	// hook_acl_test.go's testKeyAuth.
	switch raw {
	case "":
		return KeyGrant{}, false
	case "k-deny":
		return KeyGrant{KeyID: "id-" + raw, DeniedTools: []string{"up_fail"}}, true
	default:
		return KeyGrant{KeyID: "id-" + raw}, true
	}
}

type meterHarness struct {
	s       *Server
	sink    *recordSink
	stats   *countingStats
	session string
	headers map[string]string
	// sessions are the sessions opened for another Authorization than the
	// harness's: The session manager binds a legacy session to the key that opened it, so a call with another key needs its own session.
	sessions map[string]string

	mu    sync.Mutex
	setup func(hook string, cc *CallContext)
	// keyModes is the test meter's key log_content, by key id or raw key.
	keyModes map[string]string
}

// newMeterHarness builds the server with per-key auth (keyLists) and opens a
// session with key k-ok.
func newMeterHarness(t *testing.T) *meterHarness {
	t.Helper()
	up := startMeterUpstream(t)
	s := NewServer(ServerConfig{MaxAgentDepth: 5, ToolCallTimeout: meterToolTimeout, SessionTTL: time.Minute, Separator: "_"})
	t.Cleanup(s.Close)
	client := NewClient(ClientConfig{ServerID: "up", URL: up.URL, TransportType: "http"})
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := client.Connect(ctx); err != nil {
		t.Fatalf("connect to the upstream: %v", err)
	}
	s.RegisterClient(client)
	s.SetKeyAuth(keyLists{})
	// The harness's principal is org-a's (attribute), so org-a is the static
	// org whose principals the YAML server serves (the Go-test rule,
	// as m8BindStaticOrg).
	m8BindStaticOrg(s, "org-a")

	h := &meterHarness{s: s, sink: &recordSink{}, stats: &countingStats{}, keyModes: map[string]string{}}
	s.SetDeps(Deps{Events: h.sink.add, ToolStats: h.stats})
	t.Cleanup(meter.Install(meter.New(meter.Options{KeyLogContent: func(keyID, raw string) string {
		h.mu.Lock()
		defer h.mu.Unlock()
		if keyID != "" {
			return h.keyModes[keyID]
		}
		return h.keyModes[raw]
	}})))
	s.hookObserver = func(hook string, cc *CallContext) {
		h.mu.Lock()
		f := h.setup
		h.mu.Unlock()
		if f == nil {
			return
		}
		switch hook {
		case hookResolvePrincipal, hookParseAgent:
			// MCP auth's hook bodies run after their observer call and set the
			// principal and agent context, so replay these at hook 2a.
			return
		case hookRegisterRecord:
			f(hookResolvePrincipal, cc)
			f(hookParseAgent, cc)
		}
		f(hook, cc)
	}
	h.headers = map[string]string{"Authorization": "Bearer k-ok"}
	h.session = initializeSessionWith(t, s, h.headers)
	return h
}

// onHook sets the per-test field injection.
func (h *meterHarness) onHook(f func(hook string, cc *CallContext)) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.setup = f
}

// call posts one tools/call with the harness key and returns the decoded
// answer.
func (h *meterHarness) call(t *testing.T, tool string, args any, extra ...string) *Message {
	t.Helper()
	return decodeResponse(t, h.post(t, context.Background(), tool, args, extra...))
}

// sessionFor is the session of the key auth names: the harness's own, or one
// opened for that key on first use.
func (h *meterHarness) sessionFor(t *testing.T, auth string) string {
	t.Helper()
	if auth == h.headers["Authorization"] {
		return h.session
	}
	h.mu.Lock()
	sid, ok := h.sessions[auth]
	h.mu.Unlock()
	if ok {
		return sid
	}
	sid = initializeSessionWith(t, h.s, map[string]string{"Authorization": auth})
	h.mu.Lock()
	if h.sessions == nil {
		h.sessions = map[string]string{}
	}
	h.sessions[auth] = sid
	h.mu.Unlock()
	return sid
}

// post sends one tools/call; extra is "Name: value" headers, which replace
// the harness ones.
func (h *meterHarness) post(t *testing.T, ctx context.Context, tool string, args any, extra ...string) *httptest.ResponseRecorder {
	t.Helper()
	params, err := json.Marshal(ToolCallParams{Name: tool, Arguments: asArgs(args)})
	if err != nil {
		t.Fatal(err)
	}
	body, _ := json.Marshal(&Message{JSONRPC: "2.0", ID: json.RawMessage(`7`), Method: MethodToolsCall, Params: params})
	// The gateway's RequestID middleware sets the request id in front of MCP.
	ctx = models.WithRequestID(ctx, "req_"+ulid.Make().String())
	req := httptest.NewRequest("POST", "/mcp", bytes.NewReader(body)).WithContext(ctx)
	req.Header.Set("Content-Type", "application/json")
	auth := h.headers["Authorization"]
	for _, kv := range extra {
		if name, value, _ := strings.Cut(kv, ":"); strings.EqualFold(strings.TrimSpace(name), "Authorization") {
			auth = strings.TrimSpace(value)
		}
	}
	req.Header.Set("MCP-Session-Id", h.sessionFor(t, auth))
	for k, v := range h.headers {
		req.Header.Set(k, v)
	}
	for _, kv := range extra {
		name, value, _ := strings.Cut(kv, ":")
		req.Header.Set(strings.TrimSpace(name), strings.TrimSpace(value))
	}
	w := httptest.NewRecorder()
	h.s.HandlePost(w, req)
	return w
}

func asArgs(args any) map[string]interface{} {
	if args == nil {
		return map[string]interface{}{}
	}
	if m, ok := args.(map[string]interface{}); ok {
		return m
	}
	raw, _ := json.Marshal(args)
	var m map[string]interface{}
	_ = json.Unmarshal(raw, &m)
	return m
}

// toolCallMetaOf returns the answer's _meta["io.futureagi/tool_call"], or nil
// when the answer has none (a JSON-RPC error, or no block).
func toolCallMetaOf(t *testing.T, resp *Message) map[string]any {
	t.Helper()
	if len(resp.Result) == 0 {
		return nil
	}
	var res struct {
		Meta map[string]json.RawMessage `json:"_meta"`
	}
	if err := json.Unmarshal(resp.Result, &res); err != nil {
		t.Fatalf("result is not an object: %v (%s)", err, resp.Result)
	}
	raw, ok := res.Meta[MetaKeyToolCall]
	if !ok {
		return nil
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("tool_call meta is not an object: %v", err)
	}
	return m
}

// attribute fills what MCP auth, tenancy and the protocol core fill: a key principal of
// org-a, an agent context, the era and an org config with content mode
// orgMode ("" for none loaded).
func attribute(orgMode string) func(string, *CallContext) {
	return func(hook string, cc *CallContext) {
		switch hook {
		case hookResolvePrincipal:
			cc.Principal = Principal{Kind: PrincipalKey, KeyID: "key_7f3a", OrgID: "org-a"}
		case hookParseAgent:
			turn := 4
			cc.Agent.Framework, cc.Agent.AgentID, cc.Agent.RunID, cc.Agent.Step = "omega", "researcher", "run_R1", "tool"
			cc.Agent.Turn, cc.Agent.IdentitySource = &turn, "header"
			cc.Era, cc.ProtocolVersion = "2025-11-25", "2025-11-25"
		case hookResolveTenant:
			if orgMode != "" {
				cc.OrgConfig = orgWithContentMode(orgMode)
			}
		}
	}
}
