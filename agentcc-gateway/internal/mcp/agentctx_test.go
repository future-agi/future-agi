package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/oklog/ulid/v2"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// callSnap is what the chain held after hooks 1 and 2, read at step 3.
type callSnap struct {
	principal    Principal
	agent        models.AgentContext
	requestID    string
	toolCallID   string
	generationID string
	traceParent  string
	header       http.Header
	params       json.RawMessage
}

// snapCalls records a callSnap of every chained call s serves from now on.
func snapCalls(t *testing.T, s *Server) func() []callSnap {
	t.Helper()
	var mu sync.Mutex
	var snaps []callSnap
	s.hookObserver = func(hook string, cc *CallContext) {
		if hook != hookResolveTenant {
			return
		}
		sn := callSnap{
			principal: cc.Principal, agent: cc.Agent.Clone(), requestID: cc.RequestID, toolCallID: cc.ToolCallID,
			generationID: cc.GenerationID, traceParent: cc.TraceParent,
		}
		if r := cc.Request(); r != nil {
			sn.header = r.Header.Clone()
		}
		if m := cc.Message(); m != nil {
			sn.params = append(json.RawMessage(nil), m.Params...)
		}
		mu.Lock()
		snaps = append(snaps, sn)
		mu.Unlock()
	}
	t.Cleanup(func() { s.hookObserver = nil })
	return func() []callSnap {
		mu.Lock()
		defer mu.Unlock()
		return append([]callSnap(nil), snaps...)
	}
}

// agentServer is an identity server with an open session for the shared key,
// and one more for each other credential a test sends.
type agentServer struct {
	*Server
	session string
	// sessions holds the session each credential opened, by the header it
	// rode in: a session serves only the principal that opened it (M12).
	sessions map[string]string
	snaps    func() []callSnap
}

func newAgentServer(t *testing.T) *agentServer {
	t.Helper()
	s, _ := newIdentityServer(t, false)
	a := &agentServer{Server: s, sessions: map[string]string{}}
	a.session = a.sessionFor(t, bearer(m8KeyShared))
	a.snaps = snapCalls(t, s)
	return a
}

// sessionFor is the session the credential in cred opened, opened on its
// first use (with whatever verifier the server holds then).
func (a *agentServer) sessionFor(t *testing.T, cred map[string]string) string {
	t.Helper()
	var key string
	for name, value := range cred {
		key += strings.ToLower(name) + ": " + value + "\n"
	}
	if id, ok := a.sessions[key]; ok {
		return id
	}
	id := initializeSessionWith(t, a.Server, cred)
	a.sessions[key] = id
	return id
}

// post sends method with params, with the shared key and headers given as
// name, value pairs, on the session of the credential the request carries,
// and returns the answer and the call's snapshot (zero when the chain did not
// reach step 3).
func (a *agentServer) post(t *testing.T, method, params string, headers ...string) (*httptest.ResponseRecorder, callSnap) {
	t.Helper()
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`5`), Method: method}
	if params != "" {
		msg.Params = json.RawMessage(params)
	}
	body, err := json.Marshal(msg)
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+m8KeyShared)
	// The first value given for a header replaces the default (the key); a
	// later one for the same name is added after it.
	given := map[string]bool{}
	for i := 0; i+1 < len(headers); i += 2 {
		if given[headers[i]] {
			req.Header.Add(headers[i], headers[i+1])
		} else {
			req.Header.Set(headers[i], headers[i+1])
		}
		given[headers[i]] = true
	}
	// The session of the credential authenticateHTTP reads (requestAPIKey's
	// rule: the bearer, else x-api-key), opened before the snapshot count.
	cred := map[string]string{"x-api-key": req.Header.Get("x-api-key")}
	if auth := req.Header.Get("Authorization"); strings.HasPrefix(auth, "Bearer ") {
		cred = map[string]string{"Authorization": auth}
	}
	req.Header.Set("MCP-Session-Id", a.sessionFor(t, cred))
	before := len(a.snaps())
	w := httptest.NewRecorder()
	a.HandlePost(w, req)
	snaps := a.snaps()
	if len(snaps) > before {
		return w, snaps[len(snaps)-1]
	}
	return w, callSnap{}
}

// list is a tools/list; its params carry _meta when meta is not empty.
func (a *agentServer) list(t *testing.T, meta string, headers ...string) (*httptest.ResponseRecorder, callSnap) {
	t.Helper()
	params := ""
	if meta != "" {
		params = `{"_meta":` + meta + `}`
	}
	return a.post(t, MethodToolsList, params, headers...)
}

// mustAgent fails unless the call reached step 3.
func mustAgent(t *testing.T, w *httptest.ResponseRecorder, sn callSnap) models.AgentContext {
	t.Helper()
	if w.Code != http.StatusOK || sn.requestID == "" {
		t.Fatalf("status %d, body %s: the call did not reach the tenant step", w.Code, w.Body.String())
	}
	return sn.agent
}

// requireAgentError checks a protocol-level agent refusal: HTTP 400, JSON-RPC
// -32602, data.code and data.param as given.
func requireAgentError(t *testing.T, w *httptest.ResponseRecorder, code, param string) map[string]any {
	t.Helper()
	if w.Code != http.StatusBadRequest {
		t.Fatalf("status %d, want 400; body %s", w.Code, w.Body.String())
	}
	var msg Message
	if err := json.Unmarshal(w.Body.Bytes(), &msg); err != nil || msg.Error == nil {
		t.Fatalf("not a JSON-RPC error: %v %s", err, w.Body.String())
	}
	if msg.Error.Code != ErrCodeInvalidParams {
		t.Fatalf("JSON-RPC code %d, want %d", msg.Error.Code, ErrCodeInvalidParams)
	}
	var data map[string]any
	if err := json.Unmarshal(msg.Error.Data, &data); err != nil {
		t.Fatalf("data %s: %v", msg.Error.Data, err)
	}
	if data["code"] != code || data["param"] != param {
		t.Fatalf("data %v, want code %q param %q", data, code, param)
	}
	return data
}

const testTraceID = "4bf92f3577b34da6a3ce929d0e0e4736"

func TestAgentContextHeadersMetaPrecedence(t *testing.T) {
	a := newAgentServer(t)

	for _, ns := range []string{"x-agentcc-", "x-omega-"} {
		t.Run("every field from "+ns+"*", func(t *testing.T) {
			w, sn := a.list(t, "",
				ns+"agent-id", "writer", ns+"agent-version", "v3", ns+"run-id", "run-7",
				ns+"parent-run-id", "run-1", ns+"task-id", "task-2", ns+"trace-id", "trace-9",
				ns+"workflow", "triage", ns+"step", "verify", ns+"turn", "4",
				ns+"budget-scope", "ledger-a", ns+"budget-usd", "2.5", ns+"budget-remaining-usd", "1.25",
				ns+"max-cost-usd", "0.05", ns+"attempt-id", "att-1", ns+"deadline-ms", "30000",
				ns+"route-hint", "quality", ns+"require", "tools,vision", ns+"prev-outcome", "retried",
				ns+"env", "staging", ns+"user-id", "u-1", ns+"workspace-id", "ws-1", ns+"session-id", "sess-1")
			ag := mustAgent(t, w, sn)
			if ag.AgentID != "writer" || ag.AgentVersion != "v3" || ag.RunID != "run-7" || ag.ParentRunID != "run-1" ||
				ag.TaskID != "task-2" || ag.TraceID != "trace-9" || ag.Workflow != "triage" || ag.Step != models.StepVerify ||
				ag.StepSource != "header" || ag.BudgetScope != "ledger-a" || ag.AttemptID != "att-1" || ag.DeadlineMs != 30000 ||
				ag.RouteHint != "quality" || ag.PrevOutcome != "retried" || ag.Env != "staging" || ag.UserID != "u-1" ||
				ag.WorkspaceID != "ws-1" || ag.SessionID != "sess-1" || ag.IdentitySource != models.IdentitySourceHeader {
				t.Errorf("agent context = %+v", ag)
			}
			if ag.Turn == nil || *ag.Turn != 4 || ag.BudgetUSD == nil || *ag.BudgetUSD != 2.5 ||
				ag.BudgetRemainingUSD == nil || *ag.BudgetRemainingUSD != 1.25 || ag.MaxCostUSD == nil || *ag.MaxCostUSD != 0.05 ||
				strings.Join(ag.Require, ",") != "tools,vision" {
				t.Errorf("turn, USD or require fields = %v %v %v %v %v", ag.Turn, ag.BudgetUSD, ag.BudgetRemainingUSD, ag.MaxCostUSD, ag.Require)
			}
			want := ""
			if ns == "x-omega-" {
				want = "omega"
			}
			if ag.Framework != want {
				t.Errorf("framework %q, want %q", ag.Framework, want)
			}
		})
	}

	t.Run("headers win over _meta, which fills what they leave", func(t *testing.T) {
		w, sn := a.list(t, `{"io.futureagi/agent":{"run_id":"run-meta","agent_id":"planner","turn":2,"budget_usd":0.5,"ledger_id":"led-1","require":["tools"]},"progressToken":7}`,
			"x-omega-run-id", "R1")
		ag := mustAgent(t, w, sn)
		if ag.RunID != "R1" || ag.AgentID != "planner" || ag.Turn == nil || *ag.Turn != 2 || ag.BudgetUSD == nil || *ag.BudgetUSD != 0.5 ||
			ag.BudgetScope != "led-1" || strings.Join(ag.Require, ",") != "tools" {
			t.Errorf("agent = %+v", ag)
		}
		if ag.Framework != "omega" || ag.IdentitySource != models.IdentitySourceHeader {
			t.Errorf("framework %q identity %q", ag.Framework, ag.IdentitySource)
		}
	})

	t.Run("_meta alone", func(t *testing.T) {
		w, sn := a.list(t, `{"io.futureagi/agent":{"run_id":"r-m","step":"plan","framework":"langgraph"}}`)
		ag := mustAgent(t, w, sn)
		if ag.RunID != "r-m" || ag.Step != models.StepPlan || ag.StepSource != "body" || ag.Framework != "langgraph" ||
			ag.IdentitySource != models.IdentitySourceBody {
			t.Errorf("agent = %+v", ag)
		}
	})

	t.Run("framework aliases rank above _meta and never refuse", func(t *testing.T) {
		w, sn := a.list(t, `{"io.futureagi/agent":{"task_id":"task-meta","user_id":"u-meta"}}`,
			"x-a2a-context-id", "ctx-1", "x-a2a-task-id", "task-a2a", "x-resource-id", "bad id with spaces")
		ag := mustAgent(t, w, sn)
		if ag.SessionID != "ctx-1" || ag.TaskID != "task-a2a" || ag.UserID != "u-meta" || ag.Framework != "a2a" ||
			ag.IdentitySource != models.IdentitySourceAlias("a2a") {
			t.Errorf("agent = %+v", ag)
		}
		w, sn = a.list(t, "", "x-thread-id", "thr-1", "x-resource-id", "user-9")
		if ag := mustAgent(t, w, sn); ag.SessionID != "thr-1" || ag.UserID != "user-9" || ag.Framework != "mastra" {
			t.Errorf("mastra: %+v", ag)
		}
	})

	t.Run("session precedence", func(t *testing.T) {
		w, sn := a.list(t, "", "x-agentcc-session-id", "canon", "x-claude-code-session-id", "cc", "x-session-id", "generic")
		if ag := mustAgent(t, w, sn); ag.SessionID != "canon" {
			t.Errorf("session %q, want the canonical header's", ag.SessionID)
		}
		w, sn = a.list(t, "", "x-claude-code-session-id", "cc", "x-session-id", "generic")
		if ag := mustAgent(t, w, sn); ag.SessionID != "cc" || ag.Framework != "claude-code" {
			t.Errorf("session %q framework %q, want the alias's", ag.SessionID, ag.Framework)
		}
		w, sn = a.list(t, "", "x-session-id", "generic")
		if ag := mustAgent(t, w, sn); ag.SessionID != "generic" || ag.IdentitySource != "" {
			t.Errorf("generic session: %+v", ag)
		}
	})

	t.Run("traceparent: header, then _meta; an invalid gateway trace id is ignored", func(t *testing.T) {
		tp := "00-" + testTraceID + "-00f067aa0ba902b7-01"
		w, sn := a.list(t, `{"traceparent":"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"}`, "traceparent", tp)
		ag := mustAgent(t, w, sn)
		if sn.traceParent != tp || ag.TraceID != testTraceID || ag.IdentitySource != "" {
			t.Errorf("traceparent %q trace %q identity %q", sn.traceParent, ag.TraceID, ag.IdentitySource)
		}
		w, sn = a.list(t, `{"traceparent":"`+tp+`"}`)
		if mustAgent(t, w, sn); sn.traceParent != tp {
			t.Errorf("_meta traceparent %q", sn.traceParent)
		}
		w, sn = a.list(t, "", "x-agentcc-trace-id", "not a valid id", "traceparent", "garbage")
		if ag := mustAgent(t, w, sn); ag.TraceID != "" || sn.traceParent != "" {
			t.Errorf("invalid trace sources were kept: trace %q traceparent %q", ag.TraceID, sn.traceParent)
		}
	})

	t.Run("generation id: the header wins over _meta", func(t *testing.T) {
		w, sn := a.list(t, `{"io.futureagi/generation_id":"gen-meta"}`, "x-agentcc-generation-id", "gen-header")
		if mustAgent(t, w, sn); sn.generationID != "gen-header" {
			t.Errorf("generation id %q", sn.generationID)
		}
		w, sn = a.list(t, `{"io.futureagi/generation_id":"gen-meta"}`)
		if mustAgent(t, w, sn); sn.generationID != "gen-meta" {
			t.Errorf("generation id %q", sn.generationID)
		}
	})

	t.Run("runtime claims win over headers and _meta", func(t *testing.T) {
		budget := 3.0
		v := &fakeVerifier{
			accept: map[string]*Principal{"rt-1": {Kind: PrincipalRuntime, OrgID: m8Org, TokenID: "jti-1"}},
			claims: map[string]*models.RuntimeIdentity{"rt-1": {
				OrgID: m8Org, DeploymentID: "dep-1", AgentID: "agent-rt", WorkspaceID: "ws-rt", Env: "prod",
				Framework: "omega", RootRunID: "root-1", SessionID: "sess-rt", BudgetUSD: &budget,
			}},
		}
		a.SetDeps(Deps{BearerVerifier: v})
		defer a.SetDeps(Deps{})
		w, sn := a.post(t, MethodToolsList, `{"_meta":{"io.futureagi/agent":{"env":"dev","budget_usd":"5"}}}`,
			"Authorization", "Bearer rt-1", "x-agentcc-workspace-id", "ws-header", "x-agentcc-session-id", "sess-header")
		ag := mustAgent(t, w, sn)
		if ag.AgentID != "agent-rt" || ag.WorkspaceID != "ws-rt" || ag.Env != "prod" || ag.RunID != "root-1" ||
			ag.RootRunID != "root-1" || ag.SessionID != "sess-rt" || ag.BudgetUSD == nil || *ag.BudgetUSD != 3 ||
			ag.IdentitySource != models.IdentitySourceRuntimeToken {
			t.Errorf("agent = %+v", ag)
		}
	})
}

func TestAgentHeaderConflict400(t *testing.T) {
	a := newAgentServer(t)
	cases := []struct {
		name    string
		meta    string
		headers []string
		param   string
	}{
		{"run id in both namespaces", "", []string{"x-agentcc-run-id", "a", "x-omega-run-id", "b"}, "x-omega-run-id"},
		{"session in both namespaces", "", []string{"x-agentcc-session-id", "a", "x-omega-session-id", "b"}, "x-omega-session-id"},
		{"budget-scope and its ledger-id alias", "", []string{"x-agentcc-budget-scope", "a", "x-agentcc-ledger-id", "b"}, "x-agentcc-ledger-id"},
		{"step in both namespaces", "", []string{"x-agentcc-step", "act", "x-omega-step", "plan"}, "x-omega-step"},
		{"_meta budget_scope against ledger_id", `{"io.futureagi/agent":{"budget_scope":"a","ledger_id":"b"}}`, nil, "_meta.io.futureagi/agent.ledger_id"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			for _, method := range []string{MethodToolsList, MethodToolsCall} {
				params := `{"name":"gh_t1","arguments":{}`
				if tc.meta != "" {
					params += `,"_meta":` + tc.meta
				}
				params += "}"
				w, sn := a.post(t, method, params, tc.headers...)
				requireAgentError(t, w, "agent_header_conflict", tc.param)
				if sn.requestID != "" {
					t.Fatalf("%s: a refused call reached the tenant step", method)
				}
			}
		})
	}

	// Values are compared after trimming, so a stray space is no conflict.
	w, sn := a.list(t, "", "x-agentcc-run-id", "a ", "x-omega-run-id", " a")
	if ag := mustAgent(t, w, sn); ag.RunID != "a" {
		t.Errorf("run = %q", ag.RunID)
	}

	t.Run("a header against a runtime claim", func(t *testing.T) {
		v := &fakeVerifier{
			accept: map[string]*Principal{"rt-1": {Kind: PrincipalRuntime, OrgID: m8Org}},
			claims: map[string]*models.RuntimeIdentity{"rt-1": {OrgID: m8Org, AgentID: "agent-rt", RootRunID: "root-1"}},
		}
		a.SetDeps(Deps{BearerVerifier: v})
		defer a.SetDeps(Deps{})
		w, _ := a.post(t, MethodToolsList, "", "Authorization", "Bearer rt-1", "x-omega-agent-id", "someone-else")
		data := requireAgentError(t, w, "agent_header_conflict", "x-omega-agent-id")
		if meta, _ := data["metadata"].(map[string]any); meta["source"] != models.IdentitySourceRuntimeToken || meta["field"] != "agent_id" {
			t.Errorf("metadata %v", data["metadata"])
		}
		w, _ = a.post(t, MethodToolsList, "", "Authorization", "Bearer rt-1", "x-agentcc-run-id", "elsewhere")
		data = requireAgentError(t, w, "agent_header_conflict", "x-agentcc-run-id")
		if meta, _ := data["metadata"].(map[string]any); meta["reason"] != models.RuntimeReasonRunOutsideRoot {
			t.Errorf("metadata %v", data["metadata"])
		}
		w, _ = a.post(t, MethodToolsList, "", "Authorization", "Bearer rt-1", "x-agentcc-org-id", "org-other")
		requireAgentError(t, w, "agent_header_conflict", "x-agentcc-org-id")
	})
}

// TestAgentHeaderInvalid400 runs G2's own table of invalid values (the model
// path's TestAgentHeaderInvalid) and the _meta equivalents: each is HTTP 400,
// JSON-RPC -32602, data.code agent_header_invalid naming the header or key.
func TestAgentHeaderInvalid400(t *testing.T) {
	a := newAgentServer(t)
	bad := []struct{ header, value string }{
		{"x-agentcc-run-id", "run 1"}, {"x-omega-agent-id", strings.Repeat("a", 129)},
		{"x-agentcc-step", "thinking"}, {"x-omega-turn", "100001"}, {"x-agentcc-turn", "-1"},
		{"x-agentcc-budget-usd", "-1"}, {"x-agentcc-max-cost-usd", "2e3"}, {"x-agentcc-budget-remaining-usd", "1000001"},
		{"x-agentcc-deadline-ms", "0"}, {"x-agentcc-deadline-ms", "3600001"},
		{"x-agentcc-require", "tools,telepathy"}, {"x-agentcc-route-hint", "fast"},
		{"x-agentcc-prev-outcome", "great"}, {"x-agentcc-workflow", "a b"}, {"x-agentcc-env", strings.Repeat("e", 40)},
		{"x-omega-session-id", strings.Repeat("s", 256)},
		{"x-agentcc-generation-id", "gen 1"},
	}
	for _, b := range bad {
		t.Run(b.header+"="+b.value, func(t *testing.T) {
			w, _ := a.list(t, "", b.header, b.value)
			requireAgentError(t, w, "agent_header_invalid", b.header)
		})
	}
	metaBad := []struct{ meta, param string }{
		{`{"io.futureagi/agent":{"turn":-2}}`, "_meta.io.futureagi/agent.turn"},
		{`{"io.futureagi/agent":{"run_id":{"x":1}}}`, "_meta.io.futureagi/agent.run_id"},
		{`{"io.futureagi/agent":{"require":["tools",1]}}`, "_meta.io.futureagi/agent.require"},
		{`{"io.futureagi/agent":{"require":["telepathy"]}}`, "_meta.io.futureagi/agent.require"},
		{`{"io.futureagi/agent":"support-bot"}`, "_meta.io.futureagi/agent"},
		{`{"io.futureagi/generation_id":42}`, "_meta.io.futureagi/generation_id"},
		{`{"io.futureagi/generation_id":"has space"}`, "_meta.io.futureagi/generation_id"},
	}
	for _, b := range metaBad {
		t.Run(b.meta, func(t *testing.T) {
			w, _ := a.list(t, b.meta)
			requireAgentError(t, w, "agent_header_invalid", b.param)
		})
	}
}

func TestStepForcedTool(t *testing.T) {
	a := newAgentServer(t)
	call := `{"name":"gh_t1","arguments":{},"_meta":{"io.futureagi/agent":{"step":"verify","run_id":"r1"}}}`
	_, sn := a.post(t, MethodToolsCall, call, "x-agentcc-step", "plan")
	if sn.agent.Step != models.StepTool || sn.agent.StepSource != "method" || sn.agent.RunID != "r1" {
		t.Fatalf("tools/call agent %+v, want step tool from the method", sn.agent)
	}
	_, sn = a.post(t, MethodToolsCall, `{"name":"gh_t1","arguments":{}}`)
	if sn.agent.Step != models.StepTool {
		t.Fatalf("a bare tools/call: step %q, want tool", sn.agent.Step)
	}
	w, sn := a.list(t, "", "x-agentcc-step", "plan")
	if ag := mustAgent(t, w, sn); ag.Step != models.StepPlan || ag.StepSource != "header" {
		t.Fatalf("tools/list keeps the sent step: %+v", ag)
	}
}

func TestToolCallIdsAssigned(t *testing.T) {
	a := newAgentServer(t)
	parse := func(id, prefix string) {
		t.Helper()
		rest, ok := strings.CutPrefix(id, prefix)
		if !ok {
			t.Fatalf("id %q has no %q prefix", id, prefix)
		}
		if _, err := ulid.ParseStrict(rest); err != nil {
			t.Fatalf("id %q: %v", id, err)
		}
	}
	seen := map[string]bool{}
	for i := 0; i < 3; i++ {
		w, sn := a.post(t, MethodToolsCall, `{"name":"gh_t1","arguments":{},"_meta":{"io.futureagi/generation_id":"gen-1"}}`)
		parse(sn.toolCallID, "tc_")
		parse(sn.requestID, "mcp_")
		if got := w.Header().Get("x-agentcc-request-id"); got != sn.requestID {
			t.Fatalf("response header x-agentcc-request-id %q, want the call's %q", got, sn.requestID)
		}
		if seen[sn.toolCallID] || seen[sn.requestID] {
			t.Fatalf("an id repeated: %q %q", sn.toolCallID, sn.requestID)
		}
		seen[sn.toolCallID], seen[sn.requestID] = true, true
		if sn.generationID != "gen-1" {
			t.Fatalf("generation id %q", sn.generationID)
		}
	}
	w, sn := a.list(t, "")
	mustAgent(t, w, sn)
	parse(sn.requestID, "mcp_")
	if sn.toolCallID != "" {
		t.Fatalf("tools/list got tool_call_id %q", sn.toolCallID)
	}
	// A method outside the chain answers with a request id too.
	w = postMCP(t, a.Server, &Message{JSONRPC: "2.0", ID: json.RawMessage(`9`), Method: MethodPing}, withHeaders(bearer(m8KeyShared), "MCP-Session-Id", a.session))
	parse(w.Header().Get("x-agentcc-request-id"), "mcp_")
}

// m8RecordingUpstream is an MCP upstream that records every request's headers
// and body, and serves one tool, echo.
type m8RecordingUpstream struct {
	*httptest.Server
	mu   sync.Mutex
	reqs []m8RecordedRequest
}

type m8RecordedRequest struct {
	header http.Header
	body   []byte
}

func startM8RecordingUpstream(t *testing.T) *m8RecordingUpstream {
	t.Helper()
	u := &m8RecordingUpstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		u.mu.Lock()
		u.reqs = append(u.reqs, m8RecordedRequest{header: r.Header.Clone(), body: body})
		u.mu.Unlock()
		var msg Message
		if json.Unmarshal(body, &msg) != nil || msg.IsNotification() {
			w.WriteHeader(http.StatusAccepted)
			return
		}
		result := `{}`
		switch msg.Method {
		case MethodInitialize:
			result = `{"protocolVersion":"2025-06-18","capabilities":{"tools":{}},"serverInfo":{"name":"rec","version":"1"}}`
		case MethodToolsList:
			result = `{"tools":[{"name":"echo","inputSchema":{"type":"object"}}]}`
		case MethodToolsCall:
			result = `{"content":[{"type":"text","text":"ok"}]}`
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"jsonrpc":"2.0","id":` + string(msg.ID) + `,"result":` + result + `}`))
	}))
	t.Cleanup(u.Close)
	return u
}

func (u *m8RecordingUpstream) calls() []m8RecordedRequest {
	u.mu.Lock()
	defer u.mu.Unlock()
	var out []m8RecordedRequest
	for _, r := range u.reqs {
		if bytes.Contains(r.body, []byte(`"tools/call"`)) {
			out = append(out, r)
		}
	}
	return out
}

// connectUpstream registers u on s as server "up", reached with auth.
func connectUpstream(t *testing.T, s *Server, u *m8RecordingUpstream, auth AuthConfig) {
	t.Helper()
	client := NewClient(ClientConfig{ServerID: "up", URL: u.URL, TransportType: "http", Auth: auth})
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := client.Connect(ctx); err != nil {
		t.Fatalf("connect: %v", err)
	}
	s.RegisterClient(client)
}

// naiveForward replaces step 17 for the test with an upstream step that
// forwards every inbound header the call context still holds and the call's
// whole params, _meta included: what an upstream client that trusted the
// call context would send. Whatever hook 2 leaves in the call context reaches
// the upstream this way.
func naiveForward(t *testing.T, u *m8RecordingUpstream) {
	t.Helper()
	replaceStep(t, toolsCallChain, hookCallUpstream, func(_ *Server, cc *CallContext) *Denial {
		observeHook(cc, hookCallUpstream)
		body, _ := json.Marshal(&Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodToolsCall, Params: cc.Message().Params})
		req, _ := http.NewRequestWithContext(cc.Context(), http.MethodPost, u.URL, bytes.NewReader(body))
		for k, vs := range cc.Request().Header {
			for _, v := range vs {
				req.Header.Add(k, v)
			}
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			return upstreamError(err.Error(), "network")
		}
		raw, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		var m Message
		_ = json.Unmarshal(raw, &m)
		cc.Result, cc.Executed = m.Result, true
		return nil
	})
}

// agentHeaderSet is every agent, alias and Omega header a client may send.
func agentHeaderSet() []string {
	h := []string{}
	for i, s := range models.AgentFieldSuffixes {
		v := "v" + fmt.Sprint(i)
		switch s {
		case "step":
			v = "plan"
		case "turn", "deadline-ms":
			v = "3"
		case "budget-usd", "budget-remaining-usd", "max-cost-usd":
			v = "1.5"
		case "route-hint":
			v = "cost"
		case "require":
			v = "tools"
		case "prev-outcome":
			v = "ok"
		}
		h = append(h, "x-agentcc-"+s, v, "x-omega-"+s, v)
	}
	return append(h,
		"x-agentcc-session-id", "s1", "x-omega-session-id", "s1", "x-agentcc-ledger-id", "v9", "x-omega-ledger-id", "v9",
		"x-agentcc-generation-id", "gen-1", "x-omega-anything", "o",
		"x-thread-id", "thr", "x-resource-id", "res", "x-claude-code-session-id", "s1", "x-a2a-context-id", "ctx",
		"x-a2a-task-id", "task", "traceparent", "00-"+testTraceID+"-00f067aa0ba902b7-01", "x-session-id", "gen-s",
		HeaderAgentDepth, "1")
}

// agentHeaderNamesForTest is every x-agentcc-* agent header of section 6.2
// (and 6.25's generation id), lower-case.
var agentHeaderNamesForTest = func() map[string]bool {
	m := map[string]bool{"x-agentcc-generation-id": true}
	for _, s := range append(append([]string(nil), models.AgentFieldSuffixes[:]...), "ledger-id", "session-id", "org-id", "deployment-id") {
		m["x-agentcc-"+s] = true
	}
	return m
}()

// leakedAgentHeaders returns the agent, alias and Omega headers h holds. The
// other x-agentcc-* headers (the depth header among them) are not agent
// context; config.ForwardHeaderRefused keeps every x-agentcc-* header from
// being configured as a forwarded one.
func leakedAgentHeaders(h http.Header) []string {
	var leaked []string
	for k := range h {
		n := strings.ToLower(k)
		switch {
		case strings.HasPrefix(n, "x-omega-"), agentHeaderNamesForTest[n]:
			leaked = append(leaked, k)
		case n == "x-thread-id", n == "x-resource-id", n == "x-claude-code-session-id", n == "x-a2a-context-id",
			n == "x-a2a-task-id", n == "traceparent", n == "x-session-id":
			leaked = append(leaked, k)
		}
	}
	return leaked
}

// metaKeys returns the keys of params' _meta when it is an object.
func metaKeys(params json.RawMessage) []string {
	var p struct {
		Meta json.RawMessage `json:"_meta"`
	}
	var meta map[string]json.RawMessage
	if json.Unmarshal(params, &p) != nil || json.Unmarshal(p.Meta, &meta) != nil {
		return nil
	}
	keys := make([]string, 0, len(meta))
	for k := range meta {
		keys = append(keys, k)
	}
	return keys
}

// metaOf returns params' _meta object.
func metaOf(t *testing.T, params json.RawMessage) map[string]json.RawMessage {
	t.Helper()
	var p struct {
		Meta map[string]json.RawMessage `json:"_meta"`
	}
	if len(params) > 0 {
		if err := json.Unmarshal(params, &p); err != nil {
			t.Fatalf("params %s: %v", params, err)
		}
	}
	return p.Meta
}

func TestAgentKeysNeverForwarded(t *testing.T) {
	a := newAgentServer(t)
	up := startM8RecordingUpstream(t)
	connectUpstream(t, a.Server, up, AuthConfig{Type: "bearer", Token: "m8-upstream-token"})
	const call = `{"name":"up_echo","arguments":{"text":"hi","_meta":{"io.futureagi/agent":"customer data"}},` +
		`"_meta":{"progressToken":"p1","io.futureagi/agent":{"run_id":"r1","step":"plan"},"io.futureagi/generation_id":"gen-1",` +
		`"io.futureagi/anything":true,"traceparent":"00-` + testTraceID + `-00f067aa0ba902b7-01"}}`

	// What hook 2 leaves in the call context for the hooks after it.
	_, sn := a.post(t, MethodToolsCall, call, agentHeaderSet()...)
	if leaked := leakedAgentHeaders(sn.header); len(leaked) > 0 {
		t.Fatalf("the call context still holds agent headers after hook 2: %v", leaked)
	}
	if sn.header.Get(HeaderAgentDepth) != "1" {
		t.Fatalf("the inbound depth header (M19's check reads it) was dropped")
	}
	meta := metaOf(t, sn.params)
	for k := range meta {
		if strings.HasPrefix(k, "io.futureagi/") || k == "traceparent" {
			t.Fatalf("_meta key %q survived hook 2: %s", k, sn.params)
		}
	}
	if string(meta["progressToken"]) != `"p1"` {
		t.Fatalf("the client's own _meta keys must stay: %s", sn.params)
	}
	if !strings.Contains(string(sn.params), `"arguments":{"_meta":{"io.futureagi/agent":"customer data"},"text":"hi"}`) &&
		!strings.Contains(string(sn.params), `"arguments":{"text":"hi","_meta":{"io.futureagi/agent":"customer data"}}`) {
		t.Fatalf("the tool's arguments are the customer's and must not change: %s", sn.params)
	}
	if sn.agent.RunID != "v2" || sn.generationID != "gen-1" || sn.traceParent == "" {
		t.Fatalf("the stripped values were not kept in the call context: run %q gen %q traceparent %q", sn.agent.RunID, sn.generationID, sn.traceParent)
	}

	// That call went through today's upstream client; the next one goes
	// through an upstream step that forwards whatever the call context holds.
	naiveForward(t, up)
	w, _ := a.post(t, MethodToolsCall, call, agentHeaderSet()...)
	if resp := decodeResponse(t, w); resp.Error != nil {
		t.Fatalf("tools/call through the forwarding step: %+v", resp.Error)
	}

	calls := up.calls()
	if len(calls) != 2 {
		t.Fatalf("upstream saw %d tools/call, want 2", len(calls))
	}
	for i, c := range calls {
		if leaked := leakedAgentHeaders(c.header); len(leaked) > 0 {
			t.Errorf("call %d: agent headers reached the upstream: %v", i, leaked)
		}
		var msg Message
		if err := json.Unmarshal(c.body, &msg); err != nil {
			t.Fatal(err)
		}
		for k := range metaOf(t, msg.Params) {
			if strings.HasPrefix(k, "io.futureagi/") || k == "traceparent" {
				t.Errorf("call %d: _meta key %q reached the upstream: %s", i, k, c.body)
			}
		}
	}
}

func TestInboundAuthorizationNeverForwarded(t *testing.T) {
	a := newAgentServer(t)
	up := startM8RecordingUpstream(t)
	connectUpstream(t, a.Server, up, AuthConfig{Type: "bearer", Token: "m8-upstream-token"})
	call := `{"name":"up_echo","arguments":{"text":"hi"}}`
	creds := []string{"x-api-key", m8KeyPersonal, "Cookie", "session=abc", "Proxy-Authorization", "Basic eDp5"}

	w, sn := a.post(t, MethodToolsCall, call, creds...)
	if resp := decodeResponse(t, w); resp.Error != nil {
		t.Fatalf("tools/call: %+v", resp.Error)
	}
	for _, h := range []string{"Authorization", "X-Api-Key", "Cookie", "Proxy-Authorization"} {
		if v := sn.header.Get(h); v != "" {
			t.Fatalf("the call context still holds %s after hook 2", h)
		}
	}
	if sn.principal.Kind != PrincipalKey || sn.principal.OrgID != m8Org {
		t.Fatalf("principal %+v", sn.principal)
	}

	naiveForward(t, up)
	w, _ = a.post(t, MethodToolsCall, call, creds...)
	if resp := decodeResponse(t, w); resp.Error != nil {
		t.Fatalf("tools/call through the forwarding step: %+v", resp.Error)
	}
	calls := up.calls()
	if len(calls) != 2 {
		t.Fatalf("upstream saw %d tools/call, want 2", len(calls))
	}
	// Today's client sends the server's own credential; the forwarding step
	// has nothing to send. Neither sends the client's.
	if got := calls[0].header.Get("Authorization"); got != "Bearer m8-upstream-token" {
		t.Errorf("today's client sent Authorization %q, want the server's own", got)
	}
	for i, c := range calls {
		for _, secret := range []string{m8KeyShared, m8KeyPersonal, "session=abc", "Basic eDp5"} {
			for k, vs := range c.header {
				for _, v := range vs {
					if strings.Contains(v, secret) {
						t.Errorf("call %d: inbound credential reached the upstream in %s", i, k)
					}
				}
			}
		}
		if c.header.Get("X-Api-Key") != "" || c.header.Get("Cookie") != "" || c.header.Get("Proxy-Authorization") != "" {
			t.Errorf("call %d: inbound credential headers reached the upstream: %v", i, c.header)
		}
	}
}

// TestFuzzMissingAndFabricatedHeaders sends seeded random mixes of agent,
// alias, principal-looking and unknown headers and _meta values. Every answer
// is a 200 or a 400 agent refusal (never a 5xx, a 401 for the valid key, or a
// panic); a 200 holds only valid agent values, the key's own principal
// whatever the headers claim, and no agent header or io.futureagi/* key.
func TestFuzzMissingAndFabricatedHeaders(t *testing.T) {
	a := newAgentServer(t)
	rng := rand.New(rand.NewSource(20261018))
	names := []string{
		"x-agentcc-run-id", "x-omega-run-id", "x-agentcc-agent-id", "x-omega-agent-id", "x-agentcc-step", "x-omega-step",
		"x-agentcc-turn", "x-agentcc-budget-usd", "x-agentcc-ledger-id", "x-agentcc-budget-scope", "x-agentcc-require",
		"x-agentcc-session-id", "x-omega-session-id", "x-agentcc-deadline-ms", "x-agentcc-framework", "x-agentcc-env",
		"x-agentcc-generation-id", "x-agentcc-trace-id", "traceparent", "x-thread-id", "x-resource-id", "x-a2a-task-id",
		"x-claude-code-session-id", "x-session-id", "x-agentcc-org-id", "x-agentcc-deployment-id", "x-agentcc-key-id",
		"x-agentcc-user-id", "x-agentcc-principal", "x-omega-org", "x-unrelated", "X-AGENTCC-RUN-ID", "X-Omega-Step",
	}
	values := []string{
		"", " ", "ok-1", "plan", "tool", "7", "-1", "1e3", "0.25", "tools,vision", "a b", "été", strings.Repeat("z", 300),
		"00-" + testTraceID + "-00f067aa0ba902b7-01", "org-evil", "null", `{"x":1}`, "../../etc", "\t padded \t",
	}
	metas := []string{
		"", `{}`, `{"io.futureagi/agent":{}}`, `{"io.futureagi/agent":{"run_id":"m1","turn":3}}`, `{"io.futureagi/agent":null}`,
		`{"io.futureagi/agent":{"run_id":7}}`, `{"io.futureagi/agent":{"require":["tools"],"unknown":"x"}}`,
		`{"io.futureagi/generation_id":"g-1"}`, `{"io.futureagi/generation_id":{}}`, `{"traceparent":"garbage"}`,
		`{"io.futureagi/other":"x","progressToken":1}`, `[]`, `"str"`,
	}
	for i := 0; i < 400; i++ {
		var headers []string
		for n := rng.Intn(6); n > 0; n-- {
			headers = append(headers, names[rng.Intn(len(names))], values[rng.Intn(len(values))])
		}
		meta := metas[rng.Intn(len(metas))]
		method := MethodToolsList
		params := ""
		if meta != "" {
			params = `{"_meta":` + meta + `}`
		}
		if rng.Intn(3) == 0 {
			method = MethodToolsCall
			params = `{"name":"gh_t1","arguments":{}`
			if meta != "" {
				params += `,"_meta":` + meta
			}
			params += "}"
		}
		what := fmt.Sprintf("case %d: %s %v _meta %s", i, method, headers, meta)
		w, sn := a.post(t, method, params, headers...)
		switch w.Code {
		case http.StatusOK:
		case http.StatusBadRequest:
			var msg Message
			if err := json.Unmarshal(w.Body.Bytes(), &msg); err != nil || msg.Error == nil || msg.Error.Code != ErrCodeInvalidParams {
				t.Fatalf("%s: a 400 that is not a JSON-RPC -32602: %s", what, w.Body.String())
			}
			var data struct{ Code string }
			_ = json.Unmarshal(msg.Error.Data, &data)
			if data.Code != "agent_header_invalid" && data.Code != "agent_header_conflict" {
				t.Fatalf("%s: 400 with data.code %q", what, data.Code)
			}
			continue
		default:
			t.Fatalf("%s: status %d, body %s", what, w.Code, w.Body.String())
		}
		if sn.requestID == "" {
			t.Fatalf("%s: a 200 that did not run the chain", what)
		}
		if sn.principal.Kind != PrincipalKey || sn.principal.OrgID != m8Org || sn.principal.UserID != "" {
			t.Fatalf("%s: headers changed the principal: %+v", what, sn.principal)
		}
		ag := sn.agent
		for _, id := range []string{ag.AgentID, ag.AgentVersion, ag.RunID, ag.ParentRunID, ag.TaskID, ag.TraceID, ag.Workflow,
			ag.BudgetScope, ag.AttemptID, ag.UserID, ag.WorkspaceID, sn.generationID} {
			if id != "" && !models.ValidAgentID(id) {
				t.Fatalf("%s: an invalid id %q was kept: %+v", what, id, ag)
			}
		}
		if ag.Step != "" && !ag.Step.Valid() {
			t.Fatalf("%s: invalid step %q", what, ag.Step)
		}
		if method == MethodToolsCall && ag.Step != models.StepTool {
			t.Fatalf("%s: tools/call step %q", what, ag.Step)
		}
		if len(ag.SessionID) > 255 {
			t.Fatalf("%s: a %d-byte session was kept", what, len(ag.SessionID))
		}
		if leaked := leakedAgentHeaders(sn.header); len(leaked) > 0 {
			t.Fatalf("%s: agent headers left in the call context: %v", what, leaked)
		}
		for _, k := range metaKeys(sn.params) {
			if strings.HasPrefix(k, "io.futureagi/") || k == "traceparent" {
				t.Fatalf("%s: _meta key %q left in the call", what, k)
			}
		}
	}

	// No agent input at all: no agent context, no refusal.
	w, sn := a.list(t, "")
	if ag := mustAgent(t, w, sn); ag.IdentitySource != "" || ag.Framework != "" || ag.RunID != "" || ag.SessionID != "" {
		t.Fatalf("a request with no agent input got %+v", ag)
	}
}
