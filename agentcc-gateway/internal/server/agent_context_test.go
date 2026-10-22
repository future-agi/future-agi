package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// agentCall is one chat request taken through the two agent hooks directly: the
// parse step (call-order step 3) and the apply step (step 7).
type agentCall struct {
	h       *Handlers
	rc      *models.RequestContext
	req     *models.ChatCompletionRequest
	r       *http.Request
	orgCfg  *tenant.OrgConfig
	runtime *models.RuntimeIdentity
}

const agentBody = `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`

// newAgentCall builds the request; headers are name, value pairs.
func newAgentCall(t *testing.T, body string, headers ...string) *agentCall {
	t.Helper()
	r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	for i := 0; i+1 < len(headers); i += 2 {
		r.Header.Add(headers[i], headers[i+1])
	}
	var req models.ChatCompletionRequest
	if err := json.Unmarshal([]byte(body), &req); err != nil {
		t.Fatalf("body: %v", err)
	}
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.Request, rc.Model, rc.UserID = &req, req.Model, req.User
	return &agentCall{h: &Handlers{}, rc: rc, req: &req, r: r}
}

// withOrg puts the request in an org whose config the hooks read.
func (c *agentCall) withOrg(cfg *tenant.OrgConfig) *agentCall {
	store := tenant.NewStore()
	store.Set("org-agent", cfg)
	c.h.tenantStore = store
	c.rc.Metadata["key_org_id"] = "org-agent"
	c.rc.Metadata["org_id"] = "org-agent"
	c.orgCfg = cfg
	return c
}

func (c *agentCall) parse() *models.APIError {
	c.rc.Runtime = c.runtime
	return c.h.parseAgentContext(c.rc, c.r, c.req)
}

// run takes the request through both hooks and fails the test on an error.
func (c *agentCall) run(t *testing.T) *models.AgentContext {
	t.Helper()
	if err := c.parse(); err != nil {
		t.Fatalf("parseAgentContext: %v", err)
	}
	if err := c.h.applyAgentContext(c.rc, c.orgCfg, c.req); err != nil {
		t.Fatalf("applyAgentContext: %v", err)
	}
	return &c.rc.Agent
}

// wantAPIError checks an error's status, code and param.
func wantAPIError(t *testing.T, err *models.APIError, status int, code, param string) {
	t.Helper()
	if err == nil {
		t.Fatalf("no error; want %d %s (param %q)", status, code, param)
	}
	gotParam := ""
	if err.Param != nil {
		gotParam = *err.Param
	}
	if err.Status != status || err.Code != code || gotParam != param {
		t.Fatalf("error = %d %s param %q (%s); want %d %s param %q", err.Status, err.Code, gotParam, err.Message, status, code, param)
	}
}

func errMeta(err *models.APIError) map[string]string {
	m, _ := err.Metadata.(map[string]string)
	return m
}

func TestAgentHeaders(t *testing.T) {
	for _, ns := range []string{"x-agentcc-", "x-omega-"} {
		t.Run(ns, func(t *testing.T) {
			c := newAgentCall(t, agentBody,
				ns+"agent-id", "writer", ns+"agent-version", "v3", ns+"run-id", "run-7",
				ns+"parent-run-id", "run-1", ns+"task-id", "task-2", ns+"trace-id", "trace-9",
				ns+"workflow", "triage", ns+"step", "verify", ns+"turn", "4",
				ns+"budget-scope", "ledger-a", ns+"budget-usd", "2.5", ns+"budget-remaining-usd", "1.25",
				ns+"max-cost-usd", "0.05", ns+"attempt-id", "att-1", ns+"deadline-ms", "30000",
				ns+"route-hint", "quality", ns+"require", "tools,vision", ns+"prev-outcome", "retried",
				ns+"env", "staging", ns+"user-id", "u-1", ns+"workspace-id", "ws-1",
			)
			a := c.run(t)
			want := models.AgentContext{
				AgentID: "writer", AgentVersion: "v3", RunID: "run-7", ParentRunID: "run-1", TaskID: "task-2",
				TraceID: "trace-9", Workflow: "triage", Step: models.StepVerify, StepSource: "header",
				BudgetScope: "ledger-a", AttemptID: "att-1", DeadlineMs: 30000, RouteHint: "quality",
				PrevOutcome: "retried", Env: "staging", UserID: "u-1", WorkspaceID: "ws-1",
				IdentitySource: models.IdentitySourceHeader,
			}
			if a.AgentID != want.AgentID || a.AgentVersion != want.AgentVersion || a.RunID != want.RunID ||
				a.ParentRunID != want.ParentRunID || a.TaskID != want.TaskID || a.TraceID != want.TraceID ||
				a.Workflow != want.Workflow || a.Step != want.Step || a.StepSource != want.StepSource ||
				a.BudgetScope != want.BudgetScope || a.AttemptID != want.AttemptID || a.DeadlineMs != want.DeadlineMs ||
				a.RouteHint != want.RouteHint || a.PrevOutcome != want.PrevOutcome || a.Env != want.Env ||
				a.UserID != want.UserID || a.WorkspaceID != want.WorkspaceID || a.IdentitySource != want.IdentitySource {
				t.Errorf("agent context = %+v\nwant %+v", *a, want)
			}
			if a.Turn == nil || *a.Turn != 4 || a.BudgetUSD == nil || *a.BudgetUSD != 2.5 ||
				a.BudgetRemainingUSD == nil || *a.BudgetRemainingUSD != 1.25 || a.MaxCostUSD == nil || *a.MaxCostUSD != 0.05 {
				t.Errorf("turn or USD fields = %v %v %v %v", a.Turn, a.BudgetUSD, a.BudgetRemainingUSD, a.MaxCostUSD)
			}
			if strings.Join(a.Require, ",") != "tools,vision" {
				t.Errorf("require = %v", a.Require)
			}
			wantFramework := ""
			if ns == "x-omega-" {
				wantFramework = "omega"
			}
			if a.Framework != wantFramework {
				t.Errorf("framework = %q, want %q", a.Framework, wantFramework)
			}
			if c.rc.Metadata["agent_tagged"] != "true" {
				t.Error("a request with agent fields must be agent_tagged")
			}
		})
	}

	t.Run("canonical framework, and the same value in both namespaces", func(t *testing.T) {
		c := newAgentCall(t, agentBody, "x-agentcc-framework", "langgraph", "x-agentcc-run-id", "r1")
		if a := c.run(t); a.Framework != "langgraph" {
			t.Errorf("framework = %q, want langgraph", a.Framework)
		}
		c = newAgentCall(t, agentBody, "x-agentcc-run-id", "r1", "x-omega-run-id", "r1", "x-agentcc-framework", "langgraph")
		if a := c.run(t); a.RunID != "r1" || a.Framework != "omega" {
			t.Errorf("run %q framework %q; an x-omega-* header forces omega", a.RunID, a.Framework)
		}
	})

	t.Run("no agent context leaves the request untagged", func(t *testing.T) {
		c := newAgentCall(t, agentBody, "x-agentcc-session-id", "s1", "x-agentcc-metadata", `{"team":"x"}`)
		a := c.run(t)
		if a.IdentitySource != "" || a.Step != "" || a.Framework != "" || c.rc.Metadata["agent_tagged"] != "" {
			t.Errorf("an untagged request got agent context %+v, agent_tagged %q", *a, c.rc.Metadata["agent_tagged"])
		}
		if a.SessionID != "s1" {
			t.Errorf("session = %q", a.SessionID)
		}
	})
}

func TestAgentHeaderConflict(t *testing.T) {
	cases := []struct {
		name    string
		body    string
		headers []string
		param   string
	}{
		{"run id in both namespaces", agentBody, []string{"x-agentcc-run-id", "a", "x-omega-run-id", "b"}, "x-omega-run-id"},
		{"session in both namespaces", agentBody, []string{"x-agentcc-session-id", "a", "x-omega-session-id", "b"}, "x-omega-session-id"},
		{"budget-scope and its ledger-id alias", agentBody, []string{"x-agentcc-budget-scope", "a", "x-agentcc-ledger-id", "b"}, "x-agentcc-ledger-id"},
		{"step in both namespaces", agentBody, []string{"x-agentcc-step", "act", "x-omega-step", "plan"}, "x-omega-step"},
		{"metadata.agent against metadata.omega",
			`{"model":"m","messages":[{"role":"user","content":"hi"}],"metadata":{"agent":{"run_id":"a"},"omega":{"run_id":"b"}}}`,
			nil, "metadata.omega.run_id"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := newAgentCall(t, tc.body, tc.headers...).parse()
			wantAPIError(t, err, http.StatusBadRequest, "agent_header_conflict", tc.param)
		})
	}
	// Values are compared after trimming, so a stray space is no conflict.
	c := newAgentCall(t, agentBody, "x-agentcc-run-id", "a ", "x-omega-run-id", " a")
	if a := c.run(t); a.RunID != "a" {
		t.Errorf("run = %q", a.RunID)
	}
}

func TestAgentHeaderInvalid(t *testing.T) {
	bad := []struct{ header, value string }{
		{"x-agentcc-run-id", "run 1"}, {"x-omega-agent-id", strings.Repeat("a", 129)},
		{"x-agentcc-step", "thinking"}, {"x-omega-turn", "100001"}, {"x-agentcc-turn", "-1"},
		{"x-agentcc-budget-usd", "-1"}, {"x-agentcc-max-cost-usd", "2e3"}, {"x-agentcc-budget-remaining-usd", "1000001"},
		{"x-agentcc-deadline-ms", "0"}, {"x-agentcc-deadline-ms", "3600001"},
		{"x-agentcc-require", "tools,telepathy"}, {"x-agentcc-route-hint", "fast"},
		{"x-agentcc-prev-outcome", "great"}, {"x-agentcc-workflow", "a b"}, {"x-agentcc-env", strings.Repeat("e", 40)},
		{"x-omega-session-id", strings.Repeat("s", 256)},
	}
	for _, b := range bad {
		t.Run(b.header+"="+b.value, func(t *testing.T) {
			err := newAgentCall(t, agentBody, b.header, b.value).parse()
			wantAPIError(t, err, http.StatusBadRequest, "agent_header_invalid", b.header)
		})
	}
	t.Run("body value", func(t *testing.T) {
		body := `{"model":"m","messages":[{"role":"user","content":"hi"}],"metadata":{"agent":{"turn":-2}}}`
		wantAPIError(t, newAgentCall(t, body).parse(), http.StatusBadRequest, "agent_header_invalid", "metadata.agent.turn")
		body = `{"model":"m","messages":[{"role":"user","content":"hi"}],"metadata":{"omega":{"run_id":{"x":1}}}}`
		wantAPIError(t, newAgentCall(t, body).parse(), http.StatusBadRequest, "agent_header_invalid", "metadata.omega.run_id")
		body = `{"model":"m","messages":[{"role":"user","content":"hi"}],"metadata":{"omega":{"require":["tools",1]}}}`
		wantAPIError(t, newAgentCall(t, body).parse(), http.StatusBadRequest, "agent_header_invalid", "metadata.omega.require")
		body = `{"model":"m","messages":[{"role":"user","content":"hi"}],"metadata":{"omega":{"require":["telepathy"]}}}`
		wantAPIError(t, newAgentCall(t, body).parse(), http.StatusBadRequest, "agent_header_invalid", "metadata.omega.require")
	})
	// Only an object under metadata.agent or metadata.omega is agent context.
	// Any other value there is the caller's own metadata, accepted and sent
	// upstream unchanged (TestBodyMetadataOmegaFallback follows it there).
	for _, v := range []string{`"support-bot"`, `7`, `["a"]`, `null`, `true`} {
		body := `{"model":"m","messages":[{"role":"user","content":"hi"}],"metadata":{"agent":` + v + `,"omega":` + v + `}}`
		c := newAgentCall(t, body)
		if a := c.run(t); a.IdentitySource != "" || a.Framework != "" || c.rc.Metadata["agent_tagged"] != "" {
			t.Errorf("metadata.agent = %s: agent = %+v tagged %q", v, *a, c.rc.Metadata["agent_tagged"])
		}
		if string(c.req.Extra["metadata"]) != `{"agent":`+v+`,"omega":`+v+`}` {
			t.Errorf("metadata.agent = %s: body metadata became %s", v, c.req.Extra["metadata"])
		}
	}
}

func TestBodyMetadataOmegaFallback(t *testing.T) {
	body := `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}],
		"metadata":{"team":"core","omega":{"run_id":"run-b","agent_id":"planner","step":"plan","turn":0,
		"budget_usd":0.5,"ledger_id":"led-1","require":"tools"}}}`
	c := newAgentCall(t, body)
	a := c.run(t)
	if a.RunID != "run-b" || a.AgentID != "planner" || a.Step != models.StepPlan || a.StepSource != "body" ||
		a.Turn == nil || *a.Turn != 0 || a.BudgetUSD == nil || *a.BudgetUSD != 0.5 || a.BudgetScope != "led-1" ||
		strings.Join(a.Require, ",") != "tools" {
		t.Errorf("body agent context = %+v", *a)
	}
	if a.Framework != "omega" || a.IdentitySource != models.IdentitySourceBody {
		t.Errorf("framework %q identity %q; metadata.omega is Omega's body channel", a.Framework, a.IdentitySource)
	}
	// The body's agent object never goes upstream; the caller's other
	// metadata does.
	var meta map[string]json.RawMessage
	if err := json.Unmarshal(c.req.Extra["metadata"], &meta); err != nil {
		t.Fatalf("metadata after apply: %v (%s)", err, c.req.Extra["metadata"])
	}
	if _, ok := meta["omega"]; ok || string(meta["team"]) != `"core"` {
		t.Errorf("metadata after apply = %s; want team kept and omega removed", c.req.Extra["metadata"])
	}

	t.Run("headers win over the body", func(t *testing.T) {
		body := `{"model":"m","messages":[{"role":"user","content":"hi"}],"metadata":{"agent":{"run_id":"body-run","task_id":"body-task"}}}`
		c := newAgentCall(t, body, "x-agentcc-run-id", "header-run")
		a := c.run(t)
		if a.RunID != "header-run" || a.TaskID != "body-task" || a.IdentitySource != models.IdentitySourceHeader {
			t.Errorf("run %q task %q identity %q", a.RunID, a.TaskID, a.IdentitySource)
		}
		if _, ok := c.req.Extra["metadata"]; ok {
			t.Errorf("metadata holding only the agent object must be removed, got %s", c.req.Extra["metadata"])
		}
	})

	t.Run("metadata.agent framework", func(t *testing.T) {
		body := `{"model":"m","messages":[{"role":"user","content":"hi"}],"metadata":{"agent":{"framework":"crewai","run_id":"r"}}}`
		if a := newAgentCall(t, body).run(t); a.Framework != "crewai" {
			t.Errorf("framework = %q", a.Framework)
		}
	})

	t.Run("require as an array of strings", func(t *testing.T) {
		body := `{"model":"m","messages":[{"role":"user","content":"hi"}],"metadata":{"omega":{"run_id":"r","require":["tools","vision"]}}}`
		if a := newAgentCall(t, body).run(t); strings.Join(a.Require, ",") != "tools,vision" {
			t.Errorf("require = %v", a.Require)
		}
	})

	// A string under metadata.agent is an ordinary caller dimension: it is
	// accepted, kept as caller metadata and sent upstream unchanged.
	t.Run("a non-object metadata.agent is the caller's own", func(t *testing.T) {
		up := startHookUpstream(t, 0)
		ht := newHookOrderTest(t, up.URL, hookPlain, false)
		body := `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}],"metadata":{"agent":"support-bot","omega":7,"team":"x"}}`
		if w := ht.chat(t, body); w.Code != http.StatusOK {
			t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
		}
		snap := <-ht.gens
		if snap.Metadata["agent"] != "support-bot" || snap.Metadata["omega"] != "7" || snap.Metadata["team"] != "x" ||
			!slices.Contains(snap.CustomMetadataKeys, "agent") {
			t.Errorf("caller metadata = %v (custom keys %v)", snap.Metadata, snap.CustomMetadataKeys)
		}
		if snap.Agent.IdentitySource != "" || snap.Metadata["agent_tagged"] != "" {
			t.Errorf("a string metadata.agent tagged the call: %+v", snap.Agent)
		}
		bodies := up.received()
		if len(bodies) != 1 {
			t.Fatalf("upstream calls = %d", len(bodies))
		}
		meta, _ := bodies[0]["metadata"].(map[string]any)
		if meta["agent"] != "support-bot" || meta["omega"] != float64(7) || meta["team"] != "x" {
			t.Errorf("upstream metadata = %v; want the caller's metadata unchanged", meta)
		}
	})
}

func TestSessionPrecedence(t *testing.T) {
	withBody := func(extra string) string {
		return `{"model":"m","messages":[{"role":"user","content":"hi"}]` + extra + `}`
	}
	cases := []struct {
		name    string
		body    string
		runtime string
		headers []string
		want    string
	}{
		{"runtime claim first", agentBody, "claim-s", []string{"x-agentcc-session-id", "hdr"}, "claim-s"},
		{"x-agentcc over x-omega aliases", agentBody, "", []string{"x-agentcc-session-id", "canon", "x-thread-id", "t"}, "canon"},
		{"x-omega over aliases", agentBody, "", []string{"x-omega-session-id", "om", "x-claude-code-session-id", "cc"}, "om"},
		{"claude-code over mastra over a2a", agentBody, "", []string{"x-claude-code-session-id", "cc", "x-thread-id", "t", "x-a2a-context-id", "ctx"}, "cc"},
		{"mastra over a2a", agentBody, "", []string{"x-thread-id", "t", "x-a2a-context-id", "ctx"}, "t"},
		{"a2a over x-session-id", agentBody, "", []string{"x-a2a-context-id", "ctx", "x-session-id", "gen"}, "ctx"},
		{"x-session-id over body", withBody(`,"session_id":"body-s"`), "", []string{"x-session-id", "gen"}, "gen"},
		{"body session_id over metadata", withBody(`,"session_id":"body-s","metadata":{"session_id":"meta-s"}`), "", nil, "body-s"},
		{"metadata.session_id last", withBody(`,"metadata":{"session_id":"meta-s"}`), "", nil, "meta-s"},
		{"a non-string body session_id is not a session", withBody(`,"session_id":42,"metadata":{"session_id":"meta-s"}`), "", nil, "meta-s"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c := newAgentCall(t, tc.body, tc.headers...)
			if tc.runtime != "" {
				c.runtime = &models.RuntimeIdentity{OrgID: "o", AgentID: "a", RootRunID: "root", SessionID: tc.runtime}
			}
			a := c.run(t)
			if a.SessionID != tc.want || c.rc.SessionID != tc.want {
				t.Errorf("session = %q (rc.SessionID %q), want %q", a.SessionID, c.rc.SessionID, tc.want)
			}
		})
	}
}

func TestBodySessionReadBeforeStrip(t *testing.T) {
	up := startHookUpstream(t, 0)
	ht := newHookOrderTest(t, up.URL, hookPlain, false)
	var atEffective string
	ht.srv.handlers.hookObserver = func(hook, _ string, rc *models.RequestContext) {
		switch hook {
		case hookEffectiveOrgConfig: // effectiveOrgConfig buckets on the parsed session
			atEffective = rc.Agent.SessionID
		case hookApplyRoutingOptions: // applyRoutingOptions strips session_id from the body
			rc.Routing.SessionID = rc.Agent.SessionID
			delete(rc.Request.Extra, "session_id")
		}
	}
	w := ht.chat(t, `{"model":"gpt-4o","session_id":"sess-body","messages":[{"role":"user","content":"hi"}]}`)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	if atEffective != "sess-body" {
		t.Errorf("effectiveOrgConfig saw session %q; parse must read body session_id before the strip", atEffective)
	}
	snap := <-ht.gens
	if snap.Agent.SessionID != "sess-body" || snap.SessionID != "sess-body" {
		t.Errorf("logged session = %q / %q, want sess-body", snap.Agent.SessionID, snap.SessionID)
	}
	if bodies := up.received(); len(bodies) != 1 || bodies[0]["session_id"] != nil {
		t.Errorf("upstream received %v", bodies)
	}
}

func TestWorkflowHeader(t *testing.T) {
	for _, h := range []string{"x-agentcc-workflow", "x-omega-workflow"} {
		c := newAgentCall(t, agentBody, h, "invoice-review")
		if a := c.run(t); a.Workflow != "invoice-review" || c.rc.Metadata["agent_tagged"] != "true" {
			t.Errorf("%s: workflow = %q tagged %q", h, a.Workflow, c.rc.Metadata["agent_tagged"])
		}
	}
	body := `{"model":"m","messages":[{"role":"user","content":"hi"}],"metadata":{"agent":{"workflow":"wf-body"}}}`
	if a := newAgentCall(t, body).run(t); a.Workflow != "wf-body" {
		t.Errorf("body workflow = %q", a.Workflow)
	}
	wantAPIError(t, newAgentCall(t, agentBody, "x-agentcc-workflow", "wf!").parse(), http.StatusBadRequest, "agent_header_invalid", "x-agentcc-workflow")
}

func TestStepInference(t *testing.T) {
	tools := `,"tools":[{"type":"function","function":{"name":"f","parameters":{}}}]`
	body := func(extra string) string {
		return `{"model":"m","messages":[{"role":"user","content":"hi"}]` + extra + `}`
	}
	cases := []struct {
		name    string
		body    string
		headers []string
		want    models.Step
		source  string
	}{
		{"tool_choice none after turn 0 is final", body(tools + `,"tool_choice":"none"`), []string{"x-agentcc-run-id", "r", "x-agentcc-turn", "3"}, models.StepFinal, "inferred"},
		{"tool_choice none on turn 0 is not final", body(`,"tool_choice":"none"`), []string{"x-agentcc-run-id", "r", "x-agentcc-turn", "0"}, models.StepPlan, "inferred"},
		{"response_format is extract", body(tools + `,"response_format":{"type":"json_object"}`), []string{"x-agentcc-run-id", "r", "x-agentcc-turn", "2"}, models.StepExtract, "inferred"},
		// response_format {"type":"text"} is the default output, not
		// a request to extract, so it does not count as set.
		{"response_format text is not extract", body(tools + `,"response_format":{"type":"text"}`), []string{"x-agentcc-run-id", "r", "x-agentcc-turn", "2"}, models.StepAct, "inferred"},
		{"tools offered is act", body(tools), []string{"x-agentcc-run-id", "r", "x-agentcc-turn", "0"}, models.StepAct, "inferred"},
		{"turn 0 is plan", body(""), []string{"x-agentcc-run-id", "r", "x-agentcc-turn", "0"}, models.StepPlan, "inferred"},
		{"otherwise other", body(""), []string{"x-agentcc-run-id", "r", "x-agentcc-turn", "5"}, models.StepOther, "inferred"},
		{"no turn is other", body(""), []string{"x-agentcc-run-id", "r"}, models.StepOther, "inferred"},
		{"a header step is kept", body(tools), []string{"x-agentcc-step", "verify"}, models.StepVerify, "header"},
		{"an alias-tagged call is inferred too", body(tools), []string{"x-thread-id", "t-1"}, models.StepAct, "inferred"},
		{"an untagged call gets no step", body(tools), nil, "", ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c := newAgentCall(t, tc.body, tc.headers...)
			a := c.run(t)
			if a.Step != tc.want || a.StepSource != tc.source {
				t.Errorf("step = %q (%q), want %q (%q)", a.Step, a.StepSource, tc.want, tc.source)
			}
			if tc.source == "" && c.rc.Metadata["agent_tagged"] != "" {
				t.Error("an inferred step alone must not tag the request")
			}
		})
	}
}

func TestMetadataCannotSpoofAgentDims(t *testing.T) {
	spoof := `{"agent_run_id":"x","omega_step":"x","run_id":"x","step":"act","steps":"x","ledger_id":"x",
		"budget_scope":"x","agent":{"run_id":"typed"},"omega_framework":"x","team":"core"}`
	for _, blocked := range []string{"agent_run_id", "omega_step", "run_id", "step", "steps", "ledger_id", "budget_scope", "omega_x", "Agent_Id", "RUN_ID",
		"model_alias_requested", "agent_tagged", "agent_alias_ignored"} {
		if !isBlockedMetadataKey(blocked) {
			t.Errorf("caller metadata key %q is not blocked", blocked)
		}
	}
	for _, allowed := range []string{"team", "cost_center", "agentic", "runner", "session_id", "agent", "omega"} {
		if isBlockedMetadataKey(allowed) {
			t.Errorf("caller metadata key %q is blocked; it is an ordinary dimension", allowed)
		}
	}
	// Only an object under agent or omega is the agent-context channel.
	for raw, want := range map[string]bool{`{"run_id":"r"}`: true, ` {}`: true, `"support-bot"`: false, `null`: false, `[1]`: false, `7`: false} {
		for _, key := range []string{"agent", "omega"} {
			if got := isAgentContextObject(key, json.RawMessage(raw)); got != want {
				t.Errorf("isAgentContextObject(%s, %s) = %v", key, raw, got)
			}
		}
	}
	if isAgentContextObject("team", json.RawMessage(`{"a":1}`)) {
		t.Error("an object under an ordinary key is caller metadata")
	}

	up := startHookUpstream(t, 0)
	ht := newHookOrderTest(t, up.URL, hookPlain, false)
	body := `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}],"metadata":` + spoof + `}`
	w := ht.chat(t, body, "x-agentcc-metadata", `{"agent_id":"spoof","run_parent":"x","framework":"fake-but-allowed","model_alias_requested":"omega-default"}`)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	snap := <-ht.gens
	for _, k := range []string{"agent_run_id", "omega_step", "run_id", "step", "steps", "ledger_id", "budget_scope", "agent", "omega_framework", "agent_id", "run_parent", "model_alias_requested"} {
		if _, ok := snap.Metadata[k]; ok || slices.Contains(snap.CustomMetadataKeys, k) {
			t.Errorf("caller metadata %q reached the request's metadata", k)
		}
	}
	if snap.Metadata["team"] != "core" {
		t.Errorf("ordinary caller metadata was lost: %v", snap.Metadata)
	}
	if snap.Agent.RunID != "typed" || snap.Agent.AgentID != "" || snap.Agent.Step != models.StepOther {
		t.Errorf("agent context = %+v; only the typed metadata.agent channel may set it", snap.Agent)
	}
	bodies := up.received()
	if len(bodies) != 1 {
		t.Fatalf("upstream calls = %d", len(bodies))
	}
	meta, _ := bodies[0]["metadata"].(map[string]any)
	if _, ok := meta["agent"]; ok {
		t.Errorf("metadata.agent reached the upstream: %v", meta)
	}
}

func TestAttributionLimits(t *testing.T) {
	long := func(n int) string { return strings.Repeat("é", n) }
	c := newAgentCall(t, `{"model":"m","user":"body-user","messages":[{"role":"user","content":"hi"}]}`,
		"HTTP-Referer", "https://app.example/"+long(300), "X-Title", long(200), "x-agentcc-user", "end-1",
		"x-agentcc-tags", "a, b,a")
	a := c.run(t)
	if models.RuneCount(a.AppReferer) != 256 || !strings.HasPrefix(a.AppReferer, "https://app.example/") {
		t.Errorf("app_referer has %d characters, want 256", models.RuneCount(a.AppReferer))
	}
	if models.RuneCount(a.AppTitle) != 128 {
		t.Errorf("app_title has %d characters, want 128", models.RuneCount(a.AppTitle))
	}
	if a.EndUser != "end-1" || c.req.User != "end-1" || c.rc.UserID != "end-1" {
		t.Errorf("x-agentcc-user must override the body user: end_user %q, body user %q, user %q", a.EndUser, c.req.User, c.rc.UserID)
	}
	if strings.Join(a.Tags, ",") != "a,b" {
		t.Errorf("tags = %v", a.Tags)
	}
	if c.rc.Metadata["agent_tagged"] != "" {
		t.Error("attribution alone is not agent context")
	}

	c = newAgentCall(t, `{"model":"m","user":"body-user","messages":[{"role":"user","content":"hi"}]}`, "X-OpenRouter-Title", "My App")
	if a := c.run(t); a.AppTitle != "My App" || a.EndUser != "body-user" {
		t.Errorf("title %q end_user %q; X-OpenRouter-Title is the fallback and the body user is the end user", a.AppTitle, a.EndUser)
	}
	c = newAgentCall(t, agentBody, "X-Title", "first", "X-OpenRouter-Title", "second")
	if a := c.run(t); a.AppTitle != "first" {
		t.Errorf("title = %q, X-Title wins", a.AppTitle)
	}

	wantAPIError(t, newAgentCall(t, agentBody, "x-agentcc-user", strings.Repeat("u", 257)).parse(),
		http.StatusBadRequest, "agent_header_invalid", "x-agentcc-user")
	wantAPIError(t, newAgentCall(t, agentBody, "x-agentcc-tags", "1,2,3,4,5,6,7,8,9,10,11").parse(),
		http.StatusBadRequest, "invalid_tags", "x-agentcc-tags")
	wantAPIError(t, newAgentCall(t, agentBody, "x-agentcc-tags", "bad tag").parse(),
		http.StatusBadRequest, "invalid_tags", "x-agentcc-tags")

	t.Run("tags merge with providerOptions.gateway.tags", func(t *testing.T) {
		c := newAgentCall(t, agentBody, "x-agentcc-tags", "a,b")
		if err := c.parse(); err != nil {
			t.Fatal(err)
		}
		c.rc.Routing.Gateway = &models.GatewayOptions{Tags: []string{"b", "c"}} // as applyRoutingOptions parsed them
		if err := c.h.applyAgentContext(c.rc, nil, c.req); err != nil {
			t.Fatal(err)
		}
		if strings.Join(c.rc.Agent.Tags, ",") != "a,b,c" {
			t.Errorf("merged tags = %v", c.rc.Agent.Tags)
		}
		c = newAgentCall(t, agentBody, "x-agentcc-tags", "1,2,3,4,5,6")
		if err := c.parse(); err != nil {
			t.Fatal(err)
		}
		c.rc.Routing.Gateway = &models.GatewayOptions{Tags: []string{"7", "8", "9", "10", "11"}}
		wantAPIError(t, c.h.applyAgentContext(c.rc, nil, c.req), http.StatusBadRequest, "invalid_tags", "x-agentcc-tags")
	})
}

// TestAgentHeadersPlainRequestAllocatesNothing pins the hot path: a request
// with no agent context, as the chat benchmarks send, costs both hooks no
// allocation (the benchmark guard fails on one more per request).
func TestAgentHeadersPlainRequestAllocatesNothing(t *testing.T) {
	c := newAgentCall(t, `{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}`,
		"Content-Type", "application/json", "Authorization", "Bearer bench-key-123", "User-Agent", "bench")
	allocs := testing.AllocsPerRun(200, func() {
		if err := c.h.parseAgentContext(c.rc, c.r, c.req); err != nil {
			t.Fatal(err)
		}
		if err := c.h.applyAgentContext(c.rc, nil, c.req); err != nil {
			t.Fatal(err)
		}
	})
	if allocs != 0 {
		t.Errorf("parse and apply allocate %.1f times for a plain request, want 0", allocs)
	}
}

// x-agentcc-trace-id is the caller's gateway trace id (middleware.RequestID
// accepts any value), so it is read like an alias: an invalid value is
// ignored rather than refused, and on its own it does not make the call
// agent traffic. x-omega-trace-id is validated.
func TestAgentHeaderInvalidGatewayTraceIDIsNotRefused(t *testing.T) {
	for _, v := range []string{"trace with spaces", strings.Repeat("t", 200), "a+b/c=="} {
		c := newAgentCall(t, agentBody, "x-agentcc-trace-id", v)
		a := c.run(t)
		if a.TraceID != "" || a.IdentitySource != "" || c.rc.Metadata["agent_tagged"] != "" {
			t.Errorf("%q: agent = %+v tagged %q", v, *a, c.rc.Metadata["agent_tagged"])
		}
	}
	c := newAgentCall(t, agentBody, "x-agentcc-trace-id", "01HZX3TRACE")
	if a := c.run(t); a.TraceID != "01HZX3TRACE" || a.IdentitySource != "" || c.rc.Metadata["agent_tagged"] != "" {
		t.Errorf("a valid gateway trace id alone: agent = %+v tagged %q", *a, c.rc.Metadata["agent_tagged"])
	}
	c = newAgentCall(t, agentBody, "x-agentcc-trace-id", "01HZX3TRACE", "x-agentcc-run-id", "r")
	if a := c.run(t); a.TraceID != "01HZX3TRACE" || a.IdentitySource != models.IdentitySourceHeader {
		t.Errorf("with agent context: agent = %+v", *a)
	}
	wantAPIError(t, newAgentCall(t, agentBody, "x-omega-trace-id", "trace with spaces").parse(),
		http.StatusBadRequest, "agent_header_invalid", "x-omega-trace-id")
	// An invalid x-agentcc-trace-id is dropped before precedence: it cannot
	// conflict with x-omega-trace-id, and an invalid x-omega-trace-id is
	// still refused even when both carry it.
	c = newAgentCall(t, agentBody, "x-agentcc-trace-id", "my trace with spaces", "x-omega-trace-id", "abc")
	if a := c.run(t); a.TraceID != "abc" || a.Framework != "omega" || a.IdentitySource != models.IdentitySourceHeader {
		t.Errorf("invalid gateway trace id beside x-omega-trace-id: agent = %+v", *a)
	}
	wantAPIError(t, newAgentCall(t, agentBody, "x-agentcc-trace-id", "a b", "x-omega-trace-id", "a b").parse(),
		http.StatusBadRequest, "agent_header_invalid", "x-omega-trace-id")
	// Two valid, different trace ids still conflict.
	wantAPIError(t, newAgentCall(t, agentBody, "x-agentcc-trace-id", "t-1", "x-omega-trace-id", "t-2").parse(),
		http.StatusBadRequest, "agent_header_conflict", "x-omega-trace-id")
}
