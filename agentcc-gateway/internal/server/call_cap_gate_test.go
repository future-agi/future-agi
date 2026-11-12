package server

// The call-cap gate: no route serves past a
// per-request cost limit, or a run ceiling, that it cannot enforce. On every
// route below, a request that carries one is refused 402 before dispatch, in
// the route's own error envelope, with zero upstream calls; a request without
// one is dispatched and answered exactly as before (each control logs a
// "capgate-digest" of its status, content type and body, so the two arms of a
// pair can be compared byte for byte). The routes run on okGateway: the org's
// own key on each provider, which every route reaches.

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/plugins/agentbudget"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	cgCallCode = "call_cap_unenforceable"
	cgRunCode  = "run_budget_unenforceable"
	cgCallMsg  = "This endpoint can't enforce a per-request cost limit. Remove the limit from this request, or use chat completions or Messages."
	cgRunMsg   = "This endpoint can't enforce a run budget. Send this request without a run budget, or use chat completions."
)

// cgRoute is one gated route: the request okGateway serves on it.
type cgRoute struct {
	name, route, model string
	body               func(*testing.T, string) (string, []byte)
	json               bool // a JSON body, which can carry metadata.agent
	genai              bool // the Google error envelope
	runCounted         bool // the run budget counts it in its run (embeddings, rerank)
}

func cgRoutes(t *testing.T) []cgRoute {
	t.Helper()
	from := func(name, okName string, isJSON bool) cgRoute {
		c := okCaseNamed(t, okName)
		return cgRoute{name: name, route: c.route, model: c.model, body: c.body, json: isJSON}
	}
	const gem = "/v1beta/models/gemini/gemini-2.0-flash"
	routes := []cgRoute{
		from("responses", "openai responses", true),
		{name: "responses, streamed", route: "/v1/responses", model: "openai/gpt-4o-mini",
			body: okJSON(`{"model":"@MODEL@","input":"hi","store":false,"stream":true}`), json: true},
		from("completions", "openai completions", true),
		from("genai generateContent", "gemini native", true),
		{name: "genai streamGenerateContent", route: gem + ":streamGenerateContent", model: "gemini/gemini-2.0-flash", body: okJSON(okGenAI), json: true},
		{name: "genai embedContent", route: gem + ":embedContent", model: "gemini/gemini-2.0-flash",
			body: okJSON(`{"content":{"parts":[{"text":"hi"}]}}`), json: true},
		from("images", "openai images", true),
		from("audio speech", "openai speech", true),
		from("audio speech stream", "openai speech stream", true),
		from("audio transcriptions", "openai transcription", false),
		from("audio translations", "openai translation", false),
		from("embeddings", "openai embeddings", true),
		from("rerank", "cohere rerank", true),
	}
	for i := range routes {
		routes[i].genai = strings.HasPrefix(routes[i].route, "/v1beta/")
		routes[i].runCounted = routes[i].name == "embeddings" || routes[i].name == "rerank"
	}
	return routes
}

// cgSend sends rt's body (with meta, a JSON member, spliced in when set) and
// headers (name, value pairs) with the org's key.
func cgSend(t *testing.T, g *okGateway, rt cgRoute, meta string, headers ...string) *httptest.ResponseRecorder {
	t.Helper()
	ct, body := rt.body(t, rt.model)
	if meta != "" {
		body = bytes.Replace(body, []byte("{"), []byte("{"+meta+","), 1)
	}
	req := httptest.NewRequest(http.MethodPost, rt.route, bytes.NewReader(body))
	req.Header.Set("Content-Type", ct)
	if rt.genai {
		req.Header.Set("x-goog-api-key", okOrgKey)
	} else {
		req.Header.Set("Authorization", "Bearer "+okOrgKey)
	}
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// cgCalls is every call any of the gateway's hosts was sent.
func cgCalls(g *okGateway) int {
	n := 0
	for _, h := range g.hosts {
		seen, _ := h.calls()
		n += len(seen)
	}
	return n
}

// cgAssertRefused checks a 402 naming code and message in rt's error
// envelope, and that no host was called.
func cgAssertRefused(t *testing.T, rt cgRoute, w *httptest.ResponseRecorder, code, message string, calls int) {
	t.Helper()
	if w.Code != http.StatusPaymentRequired || calls != 0 {
		t.Fatalf("%s: HTTP %d, %d upstream calls: %s; want 402 %s before dispatch", rt.name, w.Code, calls, w.Body.String(), code)
	}
	var out struct {
		Error struct {
			Code    json.RawMessage `json:"code"`
			Message string          `json:"message"`
			Details []struct {
				Reason string `json:"reason"`
			} `json:"details"`
		} `json:"error"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &out); err != nil {
		t.Fatalf("%s: refusal body %s: %v", rt.name, w.Body.String(), err)
	}
	got := ""
	if rt.genai {
		if string(out.Error.Code) != "402" || len(out.Error.Details) == 0 {
			t.Fatalf("%s: refusal %s, want the Google envelope with error.code 402 and an ErrorInfo reason", rt.name, w.Body.String())
		}
		got = out.Error.Details[0].Reason
	} else {
		_ = json.Unmarshal(out.Error.Code, &got)
	}
	if got != code || out.Error.Message != message {
		t.Fatalf("%s: refusal code %q message %q, want %q %q: %s", rt.name, got, out.Error.Message, code, message, w.Body.String())
	}
}

// cgDispatched checks the request was not refused by the gate and reached a
// host, and logs its digest.
func cgDispatched(t *testing.T, rt cgRoute, variant string, w *httptest.ResponseRecorder, calls int) {
	t.Helper()
	if calls < 1 || strings.Contains(w.Body.String(), cgCallCode) || strings.Contains(w.Body.String(), cgRunCode) {
		t.Fatalf("%s (%s): HTTP %d, %d upstream calls: %s; want it dispatched as before", rt.name, variant, w.Code, calls, w.Body.String())
	}
	sum := sha256.Sum256([]byte(fmt.Sprintf("%d\n%s\n%s", w.Code, w.Header().Get("Content-Type"), w.Body.Bytes())))
	t.Logf("capgate-digest route=%q variant=%q status=%d calls=%d sha256=%x", rt.name, variant, w.Code, calls, sum)
}

// TestCallCapGateRefusesPerRequestLimit: every per-request limit source is
// refused before dispatch on every gated route, whatever its value.
func TestCallCapGateRefusesPerRequestLimit(t *testing.T) {
	sources := []struct {
		name    string
		meta    string
		headers []string
	}{
		{"x-agentcc-max-cost-usd", "", []string{"x-agentcc-max-cost-usd", "10"}},
		{"x-omega-max-cost-usd", "", []string{"x-omega-max-cost-usd", "0.0000001"}},
		{"x-agentcc-budget-remaining-usd", "", []string{"x-agentcc-budget-remaining-usd", "10"}},
		{"x-agentcc-budget-usd with no run id", "", []string{"x-agentcc-budget-usd", "10"}},
		{"max-cost-usd in a run", "", []string{"x-agentcc-run-id", "cg-run-1", "x-agentcc-max-cost-usd", "10"}},
		{"metadata.agent.max_cost_usd", `"metadata":{"agent":{"max_cost_usd":10}}`, nil},
		{"metadata.omega.budget_remaining_usd", `"metadata":{"omega":{"budget_remaining_usd":"1"}}`, nil},
	}
	for _, rt := range cgRoutes(t) {
		t.Run(rt.name, func(t *testing.T) {
			g := newOKGateway(t)
			for _, s := range sources {
				if s.meta != "" && !rt.json {
					continue // a multipart upload has no JSON metadata
				}
				before := cgCalls(g)
				w := cgSend(t, g, rt, s.meta, s.headers...)
				cgAssertRefused(t, rt, w, cgCallCode, cgCallMsg, cgCalls(g)-before)
			}
		})
	}
}

// TestCallCapGateNoLimitUnchanged: a request with no limit, a run id with no
// ceiling anywhere, or agent context that sets no limit is dispatched as at
// the base; the digests compare the arms.
func TestCallCapGateNoLimitUnchanged(t *testing.T) {
	for _, rt := range cgRoutes(t) {
		t.Run(rt.name, func(t *testing.T) {
			g := newOKGateway(t)
			for _, v := range []struct {
				name    string
				headers []string
			}{
				{"plain", nil},
				{"plain again", nil},
				{"run id, no ceiling", []string{"x-agentcc-run-id", "cg-run-free"}},
				{"agent id only", []string{"x-agentcc-agent-id", "cg-agent"}},
			} {
				before := cgCalls(g)
				w := cgSend(t, g, rt, "", v.headers...)
				cgDispatched(t, rt, v.name, w, cgCalls(g)-before)
			}
		})
	}
}

// TestCallCapGateOrgMaxCallCost: the org's managed_models.max_call_cost_usd
// is a per-request limit on a call paid from prepaid credit (a managed key),
// refused on every gated route; on the org's own key it is unchanged.
func TestCallCapGateOrgMaxCallCost(t *testing.T) {
	limit := 10.0
	orgCap := func(_ *config.Config, org *tenant.OrgConfig) {
		org.ManagedModels = &tenant.ManagedModelsConfig{MaxCallCostUSD: &limit}
	}
	managedKey := func(cfg *config.Config, _ *tenant.OrgConfig) { cfg.Auth.Keys[0].KeyType = "managed" }
	for _, rt := range cgRoutes(t) {
		t.Run(rt.name, func(t *testing.T) {
			credit := newOKGateway(t, orgCap, managedKey)
			before := cgCalls(credit)
			w := cgSend(t, credit, rt, "")
			cgAssertRefused(t, rt, w, cgCallCode, cgCallMsg, cgCalls(credit)-before)

			own := newOKGateway(t, orgCap)
			before = cgCalls(own)
			w = cgSend(t, own, rt, "")
			cgDispatched(t, rt, "own key under the org cap", w, cgCalls(own)-before)
		})
	}
}

// TestCallCapGateRunBudget: a call in a run under a run budget sent with it,
// or under the org's routing.agents.max_run_usd, is refused before dispatch,
// stream and non-stream; embeddings and rerank are counted in their run by
// the run budget and are left to it. The same org without a run id is served.
func TestCallCapGateRunBudget(t *testing.T) {
	limit := 5.0
	orgRun := func(_ *config.Config, org *tenant.OrgConfig) {
		org.Routing = &tenant.RoutingConfig{Agents: &tenant.AgentsConfig{MaxRunUSD: &limit}}
	}
	for _, rt := range cgRoutes(t) {
		t.Run(rt.name, func(t *testing.T) {
			g := newOKGateway(t)
			for _, headers := range [][]string{
				{"x-agentcc-run-id", "cg-run-2", "x-agentcc-budget-usd", "10"},
				{"x-agentcc-budget-scope", "cg-scope-2", "x-omega-budget-usd", "10"},
			} {
				before := cgCalls(g)
				w := cgSend(t, g, rt, "", headers...)
				if rt.runCounted {
					cgDispatched(t, rt, "run budget, counted", w, cgCalls(g)-before)
					continue
				}
				cgAssertRefused(t, rt, w, cgRunCode, cgRunMsg, cgCalls(g)-before)
			}

			org := newOKGateway(t, orgRun)
			before := cgCalls(org)
			w := cgSend(t, org, rt, "", "x-agentcc-run-id", "cg-run-3")
			if rt.runCounted {
				cgDispatched(t, rt, "org max_run_usd, counted", w, cgCalls(org)-before)
			} else {
				cgAssertRefused(t, rt, w, cgRunCode, cgRunMsg, cgCalls(org)-before)
			}
			before = cgCalls(org)
			w = cgSend(t, org, rt, "")
			cgDispatched(t, rt, "org max_run_usd, no run id", w, cgCalls(org)-before)
		})
	}
}

// TestCallCapGateStoredRunCeiling: a run whose scope already holds a limit in
// the run store (an earlier call's budget) is refused before dispatch with
// only its run id; another run with no limit is served.
func TestCallCapGateStoredRunCeiling(t *testing.T) {
	prefix := "cg-gate-stored:"
	store := redisstate.NewAgentBudgetStore(r7AgentRedis(t, prefix), prefix)
	plugin := agentbudget.New(agentbudget.Deps{Store: store})
	ctx := context.Background()
	if _, refusal, err := plugin.Scopes().Reserve(ctx, okOrg, "cg-run-stored", "cg-seed", 0, agentbudget.ScopeOptions{LimitMicros: 1_000_000}); err != nil || refusal != nil {
		t.Fatalf("seeding the stored ceiling: %v %v", err, refusal)
	}
	if _, err := plugin.Scopes().Void(ctx, okOrg, "cg-run-stored", "cg-seed", 0); err != nil {
		t.Fatal(err)
	}
	for _, rt := range cgRoutes(t) {
		if rt.runCounted {
			continue
		}
		t.Run(rt.name, func(t *testing.T) {
			g := newOKGateway(t)
			g.srv.handlers.SetRunBudget(plugin)
			before := cgCalls(g)
			w := cgSend(t, g, rt, "", "x-agentcc-run-id", "cg-run-stored")
			cgAssertRefused(t, rt, w, cgRunCode, cgRunMsg, cgCalls(g)-before)
			before = cgCalls(g)
			w = cgSend(t, g, rt, "", "x-agentcc-run-id", "cg-run-unlimited")
			cgDispatched(t, rt, "stored store, run with no limit", w, cgCalls(g)-before)
		})
	}
}

// TestCallCapGateBatchSubmit: a per-request limit header on POST /-/batches
// never reached its items (each runs as a chat call from its own body), so the
// submit is refused; a submit without one is accepted as before.
func TestCallCapGateBatchSubmit(t *testing.T) {
	submit := func(t *testing.T, rig *r7BatchRig, headers ...string) *httptest.ResponseRecorder {
		t.Helper()
		req := httptest.NewRequest(http.MethodPost, "/-/batches", strings.NewReader(r7BatchBody))
		req.Header.Set("Authorization", "Bearer "+r7BatchAdminToken)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set(r7BatchKeyHeader, rig.keys.orgA)
		for i := 0; i+1 < len(headers); i += 2 {
			req.Header.Set(headers[i], headers[i+1])
		}
		prepareBatch733(t, rig.srv)
		rig.keys.orgAID = signBatch733(rig.srv, req, r7BatchBody)
		w := httptest.NewRecorder()
		rig.srv.httpServer.Handler.ServeHTTP(w, req)
		return w
	}
	for _, h := range [][]string{
		{"x-agentcc-max-cost-usd", "0.5"},
		{"x-omega-max-cost-usd", "0.5"},
		{"x-agentcc-budget-usd", "1"},
	} {
		rig := newR7BatchRig(t, true, false)
		w := submit(t, rig, h...)
		rig.up.mu.Lock()
		calls := len(rig.up.calls)
		rig.up.mu.Unlock()
		var out map[string]any
		_ = json.Unmarshal(w.Body.Bytes(), &out)
		e, _ := out["error"].(map[string]any)
		if w.Code != http.StatusPaymentRequired || r7BatchErrorCode(out) != cgCallCode || e["message"] != cgCallMsg || out["batch_id"] != nil || calls != 0 {
			t.Fatalf("submit with %s: HTTP %d, %d upstream calls: %s; want 402 %s and no batch", h[0], w.Code, calls, w.Body.String(), cgCallCode)
		}
	}
	rig := newR7BatchRig(t, true, false)
	if w := submit(t, rig); w.Code != http.StatusAccepted {
		t.Fatalf("submit with no limit: HTTP %d: %s; want 202 as before", w.Code, w.Body.String())
	}
}
