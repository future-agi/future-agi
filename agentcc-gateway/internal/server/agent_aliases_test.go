package server

import (
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const testTraceID = "4bf92f3577b34da6a3ce929d0e0e4736"

func TestFrameworkAliasesMapped(t *testing.T) {
	cases := []struct {
		name      string
		headers   []string
		framework string
		check     func(a *models.AgentContext) bool
	}{
		{"mastra thread and resource", []string{"x-thread-id", "thr-1", "x-resource-id", "user-9"}, "mastra",
			func(a *models.AgentContext) bool { return a.SessionID == "thr-1" && a.UserID == "user-9" }},
		{"claude code session", []string{"x-claude-code-session-id", "cc-1"}, "claude-code",
			func(a *models.AgentContext) bool { return a.SessionID == "cc-1" }},
		{"a2a context and task", []string{"x-a2a-context-id", "ctx-1", "x-a2a-task-id", "task-1"}, "a2a",
			func(a *models.AgentContext) bool { return a.SessionID == "ctx-1" && a.TaskID == "task-1" }},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c := newAgentCall(t, agentBody, tc.headers...)
			a := c.run(t)
			if !tc.check(a) || a.Framework != tc.framework || a.IdentitySource != models.IdentitySourceAlias(tc.framework) {
				t.Errorf("agent = %+v; want framework %s from alias:%s", *a, tc.framework, tc.framework)
			}
			if c.rc.SessionID != a.SessionID || c.rc.Metadata["agent_tagged"] != "true" {
				t.Errorf("rc.SessionID %q, agent_tagged %q", c.rc.SessionID, c.rc.Metadata["agent_tagged"])
			}
		})
	}

	t.Run("a mastra user id becomes the request's user when the body has none", func(t *testing.T) {
		c := newAgentCall(t, agentBody, "x-resource-id", "user-9")
		c.run(t)
		if c.rc.UserID != "user-9" {
			t.Errorf("rc.UserID = %q", c.rc.UserID)
		}
		c = newAgentCall(t, `{"model":"m","user":"u-body","messages":[{"role":"user","content":"hi"}]}`, "x-resource-id", "user-9")
		c.run(t)
		if c.rc.UserID != "u-body" || c.rc.Agent.UserID != "user-9" {
			t.Errorf("rc.UserID %q agent user %q; the body user stays the request's user", c.rc.UserID, c.rc.Agent.UserID)
		}
	})

	t.Run("traceparent maps the trace id but neither tags nor names a framework", func(t *testing.T) {
		c := newAgentCall(t, agentBody, "traceparent", "00-"+testTraceID+"-00f067aa0ba902b7-01")
		a := c.run(t)
		if a.TraceID != testTraceID || a.Framework != "" || a.IdentitySource != "" || c.rc.Metadata["agent_tagged"] != "" {
			t.Errorf("agent = %+v tagged %q", *a, c.rc.Metadata["agent_tagged"])
		}
		c = newAgentCall(t, agentBody, "traceparent", "00-"+testTraceID+"-00f067aa0ba902b7-01", "x-agentcc-run-id", "r")
		if a := c.run(t); a.TraceID != testTraceID || a.RunID != "r" {
			t.Errorf("trace %q run %q", a.TraceID, a.RunID)
		}
	})

	t.Run("a canonical framework wins over the alias framework", func(t *testing.T) {
		c := newAgentCall(t, agentBody, "x-thread-id", "t", "x-agentcc-framework", "custom")
		if a := c.run(t); a.Framework != "custom" || a.SessionID != "t" {
			t.Errorf("framework %q session %q", a.Framework, a.SessionID)
		}
		c = newAgentCall(t, agentBody, "x-thread-id", "t", "x-omega-agent-id", "o")
		if a := c.run(t); a.Framework != "omega" {
			t.Errorf("framework %q, an x-omega-* header forces omega", a.Framework)
		}
	})
}

func TestAliasNeverOverridesCanonical(t *testing.T) {
	c := newAgentCall(t, agentBody,
		"x-agentcc-session-id", "canon-s", "x-thread-id", "alias-s",
		"x-agentcc-task-id", "canon-t", "x-a2a-task-id", "alias-t",
		"x-agentcc-trace-id", "canon-trace", "traceparent", "00-"+testTraceID+"-00f067aa0ba902b7-01",
		"x-omega-user-id", "canon-u", "x-resource-id", "alias-u")
	a := c.run(t)
	if a.SessionID != "canon-s" || a.TaskID != "canon-t" || a.TraceID != "canon-trace" || a.UserID != "canon-u" {
		t.Errorf("agent = %+v; canonical values must win", *a)
	}
	for _, alias := range []string{"x-thread-id", "x-a2a-task-id", "traceparent", "x-resource-id"} {
		if !strings.Contains(strings.Join(a.AliasOverridden, ","), alias) {
			t.Errorf("alias_overridden = %v, missing %s", a.AliasOverridden, alias)
		}
	}
	if a.IdentitySource != models.IdentitySourceHeader || a.Framework != "omega" {
		t.Errorf("identity %q framework %q", a.IdentitySource, a.Framework)
	}

	// An overridden alias still says which framework sent the call.
	c = newAgentCall(t, agentBody, "x-agentcc-session-id", "canon-s", "x-thread-id", "alias-s")
	if a := c.run(t); a.SessionID != "canon-s" || a.Framework != "mastra" || a.IdentitySource != "alias:mastra" ||
		strings.Join(a.AliasOverridden, ",") != "x-thread-id" || c.rc.Metadata["agent_tagged"] != "true" {
		t.Errorf("agent = %+v tagged %q", *a, c.rc.Metadata["agent_tagged"])
	}

	// The runtime claim's session also outranks an alias.
	c = newAgentCall(t, agentBody, "x-claude-code-session-id", "cc")
	c.runtime = &models.RuntimeIdentity{OrgID: "o", AgentID: "a", RootRunID: "root", SessionID: "claim-s"}
	if a := c.run(t); a.SessionID != "claim-s" || strings.Join(a.AliasOverridden, ",") != "x-claude-code-session-id" {
		t.Errorf("session %q overridden %v", a.SessionID, a.AliasOverridden)
	}
}

func TestAliasInvalidIgnored(t *testing.T) {
	c := newAgentCall(t, agentBody,
		"x-thread-id", "has space", "x-a2a-task-id", strings.Repeat("t", 129),
		"traceparent", "not-a-traceparent", "x-claude-code-session-id", "cc-ok")
	if err := c.parse(); err != nil {
		t.Fatalf("an alias must never return 400, got %v", err)
	}
	a := &c.rc.Agent
	if a.SessionID != "cc-ok" || a.TaskID != "" || a.TraceID != "" {
		t.Errorf("agent = %+v; invalid aliases are ignored", *a)
	}
	got := c.rc.Metadata["agent_alias_ignored"]
	for _, name := range []string{"x-thread-id", "x-a2a-task-id", "traceparent"} {
		if !strings.Contains(got, name) {
			t.Errorf("agent_alias_ignored = %q, missing %s", got, name)
		}
	}
	if strings.Contains(got, "x-claude-code-session-id") {
		t.Errorf("agent_alias_ignored = %q names a valid alias", got)
	}
	// Only invalid aliases: nothing is mapped and no framework is claimed.
	c = newAgentCall(t, agentBody, "x-thread-id", "bad value")
	if a := c.run(t); a.Framework != "" || a.SessionID != "" {
		t.Errorf("agent = %+v", *a)
	}
	for _, bad := range []string{
		"00-" + strings.Repeat("0", 32) + "-00f067aa0ba902b7-01", // all-zero trace id
		"00-" + strings.ToUpper(testTraceID) + "-00f067aa0ba902b7-01",
		"00-" + testTraceID + "-0000000000000000-01",
		"ff-" + testTraceID + "-00f067aa0ba902b7-01",
	} {
		c := newAgentCall(t, agentBody, "traceparent", bad)
		if a := c.run(t); a.TraceID != "" {
			t.Errorf("traceparent %q mapped trace %q", bad, a.TraceID)
		}
	}
}

func TestFrameworkAliasesOff(t *testing.T) {
	off := false
	cfg := &tenant.OrgConfig{Routing: &tenant.RoutingConfig{Agents: &tenant.AgentsConfig{FrameworkAliases: &off}}}
	c := newAgentCall(t, agentBody, "x-thread-id", "t", "x-resource-id", "u", "x-a2a-task-id", "k",
		"traceparent", "00-"+testTraceID+"-00f067aa0ba902b7-01").withOrg(cfg)
	a := c.run(t)
	if a.SessionID != "" || a.UserID != "" || a.TaskID != "" || a.TraceID != "" || a.Framework != "" || a.IdentitySource != "" {
		t.Errorf("agent = %+v; framework_aliases=false must ignore every alias", *a)
	}
	if c.rc.Metadata["agent_tagged"] != "" || c.rc.Metadata["agent_alias_ignored"] != "" {
		t.Errorf("metadata = %v", c.rc.Metadata)
	}
	// Canonical headers still work, and aliases are on by default.
	c = newAgentCall(t, agentBody, "x-thread-id", "t", "x-agentcc-run-id", "r").withOrg(cfg)
	if a := c.run(t); a.RunID != "r" || a.SessionID != "" {
		t.Errorf("agent = %+v", *a)
	}
	c = newAgentCall(t, agentBody, "x-thread-id", "t").withOrg(&tenant.OrgConfig{})
	if a := c.run(t); a.SessionID != "t" || a.Framework != "mastra" {
		t.Errorf("agent = %+v; aliases default to on", *a)
	}
}

// runtimeCall is a request authenticated by a runtime token with root run R1.
func runtimeCall(t *testing.T, headers ...string) *agentCall {
	t.Helper()
	budget := 5.0
	c := newAgentCall(t, agentBody, headers...)
	c.runtime = &models.RuntimeIdentity{
		OrgID: "org-rt", DeploymentID: "dep-1", AgentID: "agent-rt", WorkspaceID: "ws-claim",
		AgentVersion: "v-claim", Env: "prod", Framework: "omega-runtime",
		RootRunID: "R1", ParentRunID: "R0", SessionID: "sess-claim",
		BudgetScope: "scope-claim", BudgetUSD: &budget,
	}
	return c
}

func TestRuntimeClaimsWinOverHeaders(t *testing.T) {
	c := runtimeCall(t, "x-agentcc-agent-version", "v-header", "x-agentcc-workspace-id", "ws-header",
		"x-agentcc-env", "dev", "x-agentcc-session-id", "sess-header", "x-agentcc-budget-scope", "scope-header",
		"x-agentcc-step", "act", "x-agentcc-turn", "2")
	a := c.run(t)
	if a.AgentID != "agent-rt" || a.AgentVersion != "v-claim" || a.WorkspaceID != "ws-claim" || a.Env != "prod" ||
		a.SessionID != "sess-claim" || a.BudgetScope != "scope-claim" || a.RootRunID != "R1" || a.RunID != "R1" ||
		a.ParentRunID != "R0" || a.IdentitySource != models.IdentitySourceRuntimeToken {
		t.Errorf("agent = %+v; the runtime claims are authoritative", *a)
	}
	// Per-call fields still come from headers.
	if a.Step != models.StepAct || a.StepSource != "header" || a.Turn == nil || *a.Turn != 2 {
		t.Errorf("per-call fields lost: %+v", *a)
	}
	if a.Framework != "omega-runtime" {
		t.Errorf("framework = %q, want the claim's", a.Framework)
	}
	c = runtimeCall(t, "x-agentcc-framework", "langgraph")
	if a := c.run(t); a.Framework != "langgraph" {
		t.Errorf("framework = %q; x-agentcc-framework comes before the claim's", a.Framework)
	}
	c = runtimeCall(t)
	c.runtime.BudgetScope = ""
	if a := c.run(t); a.BudgetScope != "R1" {
		t.Errorf("budget scope = %q; it defaults to the root run", a.BudgetScope)
	}
}

func TestRuntimeClaimBudgetOnlyLowered(t *testing.T) {
	for _, tc := range []struct {
		header string
		want   float64
	}{{"", 5}, {"10", 5}, {"5", 5}, {"2.5", 2.5}, {"0", 0}} {
		var headers []string
		if tc.header != "" {
			headers = []string{"x-agentcc-budget-usd", tc.header}
		}
		a := runtimeCall(t, headers...).run(t)
		if a.BudgetUSD == nil || *a.BudgetUSD != tc.want {
			t.Errorf("header %q: budget = %v, want %v", tc.header, a.BudgetUSD, tc.want)
		}
	}
	c := runtimeCall(t, "x-agentcc-budget-usd", "3")
	c.runtime.BudgetUSD = nil
	if a := c.run(t); a.BudgetUSD == nil || *a.BudgetUSD != 3 {
		t.Errorf("budget = %v; with no claimed budget the header's cap applies", a.BudgetUSD)
	}
	if a := runtimeCall(t, "x-agentcc-budget-scope", "elsewhere").run(t); a.BudgetScope != "scope-claim" {
		t.Errorf("budget scope = %q; a header never moves a call to another scope", a.BudgetScope)
	}
}

func TestRuntimeDescendantRunAccepted(t *testing.T) {
	cases := []struct {
		name     string
		headers  []string
		verified bool
		parent   string
	}{
		{"parent is the root", []string{"x-agentcc-run-id", "R2", "x-agentcc-parent-run-id", "R1"}, false, "R1"},
		{"lineage verified by the token store", []string{"x-omega-run-id", "R3", "x-omega-parent-run-id", "R2"}, true, "R2"},
		{"same budget scope", []string{"x-agentcc-run-id", "R4", "x-agentcc-budget-scope", "scope-claim"}, false, ""},
		{"the root itself", []string{"x-agentcc-run-id", "R1"}, false, "R0"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c := runtimeCall(t, tc.headers...)
			c.runtime.DescendantVerified = tc.verified
			a := c.run(t)
			wantRun := tc.headers[1]
			if a.RunID != wantRun || a.RootRunID != "R1" || a.BudgetScope != "scope-claim" || a.ParentRunID != tc.parent {
				t.Errorf("agent = %+v; want run %s under root R1, parent %q", *a, wantRun, tc.parent)
			}
		})
	}
}

func TestRuntimeRunOutsideRootRejected(t *testing.T) {
	for _, headers := range [][]string{
		{"x-agentcc-run-id", "R9"},
		{"x-agentcc-run-id", "R9", "x-agentcc-parent-run-id", "R5"},
		{"x-agentcc-run-id", "R9", "x-agentcc-budget-scope", "other-scope"},
	} {
		err := runtimeCall(t, headers...).parse()
		wantAPIError(t, err, http.StatusBadRequest, "agent_header_conflict", "x-agentcc-run-id")
		if m := errMeta(err); m["reason"] != "run_outside_root" || m["source"] != "runtime_token" {
			t.Errorf("%v: metadata = %v", headers, m)
		}
	}
}

func TestRuntimeAgentConflictStill400(t *testing.T) {
	for _, tc := range []struct{ header, value string }{
		{"x-agentcc-agent-id", "someone-else"},
		{"x-omega-agent-id", "someone-else"},
		{"x-agentcc-org-id", "org-other"},
		{"x-agentcc-deployment-id", "dep-other"},
	} {
		c := runtimeCall(t, tc.header, tc.value, "x-agentcc-run-id", "R2", "x-agentcc-parent-run-id", "R1")
		c.runtime.DescendantVerified = true
		err := c.parse()
		wantAPIError(t, err, http.StatusBadRequest, "agent_header_conflict", tc.header)
		if m := errMeta(err); m["source"] != "runtime_token" {
			t.Errorf("%s: metadata = %v", tc.header, m)
		}
	}
	// Agreeing with the claims is fine.
	c := runtimeCall(t, "x-agentcc-agent-id", "agent-rt", "x-agentcc-org-id", "org-rt", "x-agentcc-deployment-id", "dep-1")
	if a := c.run(t); a.AgentID != "agent-rt" {
		t.Errorf("agent = %+v", *a)
	}
	// Without a runtime token, org and deployment headers mean nothing.
	if err := newAgentCall(t, agentBody, "x-agentcc-org-id", "any").parse(); err != nil {
		t.Errorf("org header without a token: %v", err)
	}
}
