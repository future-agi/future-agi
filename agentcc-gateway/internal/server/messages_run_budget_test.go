package server

// Run budgets on /v1/messages (required with the per-call fix): Messages
// spend is not counted into a run scope, so a Messages request that falls
// under a run ceiling that applies is refused before dispatch, 402
// run_budget_unenforceable in the Anthropic envelope, pointing to
// /v1/chat/completions, instead of being served uncounted. A request under no
// ceiling is served as before, so callers that send a run id without a limit
// are never refused. The ceilings that apply: the caller's budget-usd sent
// with a run (run-id, budget-scope or ledger-id), and a ceiling an earlier
// call stored on the run; a stopped run is refused as chat refuses it (402
// agent_run_stopped). The org's routing.agents.max_run_usd is an
// admission-cap policy: with admission caps on, the central guard refuses
// every Messages call before the handler (402 call_cap_unenforceable,
// cap_kind run, unchanged); with them off chat does not read it either.

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/plugins/agentbudget"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// mrbError is a Messages error envelope.
type mrbError struct {
	Type  string `json:"type"`
	Error struct {
		Type    string `json:"type"`
		Code    string `json:"code"`
		Message string `json:"message"`
	} `json:"error"`
}

func mrbDecode(t *testing.T, w *httptest.ResponseRecorder) mrbError {
	t.Helper()
	var e mrbError
	if err := json.Unmarshal(w.Body.Bytes(), &e); err != nil {
		t.Fatalf("body %s: %v", w.Body.String(), err)
	}
	return e
}

// mrbAssertRunRefused checks a 402 run_budget_unenforceable in the Anthropic
// envelope that points to chat, and that nothing reached the upstream.
func mrbAssertRunRefused(t *testing.T, w *httptest.ResponseRecorder, upstreamCalls int) {
	t.Helper()
	if w.Code != http.StatusPaymentRequired {
		t.Fatalf("HTTP %d: %s, want 402 run_budget_unenforceable before dispatch", w.Code, w.Body.String())
	}
	e := mrbDecode(t, w)
	if e.Type != "error" || e.Error.Code != "run_budget_unenforceable" ||
		!strings.Contains(e.Error.Message, "/v1/chat/completions") || !strings.Contains(e.Error.Message, "Messages") {
		t.Fatalf("refusal %s, want an Anthropic error with code run_budget_unenforceable pointing to /v1/chat/completions", w.Body.String())
	}
	if upstreamCalls != 0 {
		t.Fatalf("the upstream was called %d times for a refused call", upstreamCalls)
	}
}

// mrbAssertServed checks a Messages call served with one upstream call.
func mrbAssertServed(t *testing.T, w *httptest.ResponseRecorder, upstreamCalls int) {
	t.Helper()
	if w.Code != http.StatusOK || upstreamCalls != 1 {
		t.Fatalf("HTTP %d, %d upstream calls: %s, want served once", w.Code, upstreamCalls, w.Body.String())
	}
}

// TestMessagesRunBudgetCallerRefused: a run budget the caller sends with its
// run is refused before dispatch on a routed Messages call, stream or not,
// however the run and the budget are named, even when the call would fit a
// per-call cap: Messages spend cannot be counted against the run.
func TestMessagesRunBudgetCallerRefused(t *testing.T) {
	up := startHookUpstream(t, 0)
	ht := r7BudgetGateway(t, up.URL, nil)

	for _, c := range []struct {
		name    string
		extra   string
		headers []string
	}{
		{"run-id and budget-usd", "", []string{"x-agentcc-run-id", "mrb-run-1", "x-agentcc-budget-usd", "10"}},
		{"budget-scope and budget-usd", "", []string{"x-agentcc-budget-scope", "mrb-scope-1", "x-agentcc-budget-usd", "10"}},
		{"ledger-id and budget-usd", "", []string{"x-agentcc-ledger-id", "mrb-ledger-1", "x-agentcc-budget-usd", "10"}},
		{"x-omega run-id and budget-usd", "", []string{"x-omega-run-id", "mrb-run-2", "x-omega-budget-usd", "10"}},
		{"metadata.agent run_id and budget_usd", `,"metadata":{"agent":{"run_id":"mrb-run-3","budget_usd":10}}`, nil},
		{"with a per-call cap it fits under", "", []string{"x-agentcc-run-id", "mrb-run-4", "x-agentcc-budget-usd", "10", "x-agentcc-max-cost-usd", "10"}},
	} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s, stream=%t", c.name, stream), func(t *testing.T) {
				before := len(up.received())
				w := mccMessages(t, ht.srv, r7BudgetKey, mccBody("gpt-4o", 64, stream, c.extra), c.headers...)
				mrbAssertRunRefused(t, w, len(up.received())-before)
			})
		}
	}

	// budget-usd with no run covers this call alone: a per-call term, fitted.
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("budget-usd with no run is per-call (control), stream=%t", stream), func(t *testing.T) {
			before := len(up.received())
			w := mccMessages(t, ht.srv, r7BudgetKey, mccBody("gpt-4o", 64, stream, ""), "x-agentcc-budget-usd", "10")
			mrbAssertServed(t, w, len(up.received())-before)
		})
	}
}

// TestMessagesRunBudgetStoredCeiling: a ceiling an earlier chat call stored on
// the run applies to a Messages call that names only the run; a stopped run
// is refused as chat refuses it; a run with no ceiling is served as before,
// and nothing is held or counted on it.
func TestMessagesRunBudgetStoredCeiling(t *testing.T) {
	up := startHookUpstream(t, 0)
	prefix := fmt.Sprintf("test:mrb-stored:%d:", time.Now().UnixNano())
	store := redisstate.NewAgentBudgetStore(r7AgentRedis(t, prefix), prefix)
	ht := r7BudgetGateway(t, up.URL, store)
	ctx := context.Background()

	if w := r7BudgetChat(t, ht, r7BudgetKey, r7BudgetBody, "x-agentcc-run-id", "mrb-stored", "x-agentcc-budget-usd", "10"); w.Code != http.StatusOK {
		t.Fatalf("the chat call that sets the run's budget: HTTP %d %s", w.Code, w.Body.String())
	}
	if st, err := store.Peek(ctx, r7BudgetOrg, "mrb-stored"); err != nil || !st.HasLimit() {
		t.Fatalf("the run's stored state %+v (%v), want a limit", st, err)
	}
	if err := store.Stop(ctx, r7BudgetOrg, "mrb-stopped", "tool_call_blocked:shell", time.Minute); err != nil {
		t.Fatal(err)
	}

	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("the run's stored budget, stream=%t", stream), func(t *testing.T) {
			before := len(up.received())
			w := mccMessages(t, ht.srv, r7BudgetKey, mccBody("gpt-4o", 64, stream, ""), "x-agentcc-run-id", "mrb-stored")
			mrbAssertRunRefused(t, w, len(up.received())-before)
		})
		t.Run(fmt.Sprintf("the same run named in metadata.omega, stream=%t", stream), func(t *testing.T) {
			before := len(up.received())
			w := mccMessages(t, ht.srv, r7BudgetKey, mccBody("gpt-4o", 64, stream, `,"metadata":{"omega":{"run_id":"mrb-stored"}}`))
			mrbAssertRunRefused(t, w, len(up.received())-before)
		})
		t.Run(fmt.Sprintf("a stopped run, stream=%t", stream), func(t *testing.T) {
			before := len(up.received())
			w := mccMessages(t, ht.srv, r7BudgetKey, mccBody("gpt-4o", 64, stream, ""), "x-agentcc-run-id", "mrb-stopped")
			if w.Code != http.StatusPaymentRequired || !strings.Contains(mrbDecode(t, w).Error.Message, "tool_call_blocked:shell") {
				t.Fatalf("HTTP %d: %s, want 402 naming the stop reason", w.Code, w.Body.String())
			}
			if n := len(up.received()) - before; n != 0 {
				t.Fatalf("the upstream was called %d times for a stopped run", n)
			}
		})
		t.Run(fmt.Sprintf("a run with no ceiling, stream=%t", stream), func(t *testing.T) {
			run := fmt.Sprintf("mrb-free-%t", stream)
			before := len(up.received())
			w := mccMessages(t, ht.srv, r7BudgetKey, mccBody("gpt-4o", 64, stream, ""), "x-agentcc-run-id", run)
			mrbAssertServed(t, w, len(up.received())-before)
			st, err := store.Peek(ctx, r7BudgetOrg, run)
			if err != nil || st.HasLimit() || st.SpentMicros != 0 || st.HeldMicros != 0 {
				t.Fatalf("the run's state %+v (%v), want nothing stored", st, err)
			}
		})
	}
}

// TestMessagesRunBudgetNoCeiling: a run id with no ceiling anywhere is served
// as at the base, stream or not, with or without a per-call term; and the
// org's routing.agents.max_run_usd with admission caps off is not read on
// Messages, as it is not read on chat.
func TestMessagesRunBudgetNoCeiling(t *testing.T) {
	up := startHookUpstream(t, 0)
	ht := r7BudgetGateway(t, up.URL, nil)

	for _, h := range [][]string{
		{"x-agentcc-run-id", "mrb-nc-run"},
		{"x-omega-run-id", "mrb-nc-omega"},
		{"x-agentcc-budget-scope", "mrb-nc-scope"},
		{"x-agentcc-ledger-id", "mrb-nc-ledger"},
		{"x-agentcc-run-id", "mrb-nc-remaining", "x-agentcc-budget-remaining-usd", "10"},
		{"x-agentcc-run-id", "mrb-nc-maxcost", "x-agentcc-max-cost-usd", "10"},
	} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s, stream=%t", strings.Join(h, " "), stream), func(t *testing.T) {
				before := len(up.received())
				w := mccMessages(t, ht.srv, r7BudgetKey, mccBody("gpt-4o", 64, stream, ""), h...)
				mrbAssertServed(t, w, len(up.received())-before)
			})
		}
	}

	tiny := 0.0000001
	ht.srv.TenantStore.Set(r7BudgetOrg, &tenant.OrgConfig{Routing: &tenant.RoutingConfig{Agents: &tenant.AgentsConfig{MaxRunUSD: &tiny}}})
	before := len(up.received())
	if w := r7BudgetChat(t, ht, r7BudgetKey, r7BudgetBody, "x-agentcc-run-id", "mrb-nc-org"); w.Code != http.StatusOK || len(up.received()) != before+1 {
		t.Fatalf("chat with max_run_usd and admission caps off (control): HTTP %d %s", w.Code, w.Body.String())
	}
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("max_run_usd with admission caps off, stream=%t", stream), func(t *testing.T) {
			before := len(up.received())
			w := mccMessages(t, ht.srv, r7BudgetKey, mccBody("gpt-4o", 64, stream, ""), "x-agentcc-run-id", "mrb-nc-org")
			mrbAssertServed(t, w, len(up.received())-before)
		})
	}
}

// TestMessagesRunBudgetAdmissionRunPolicy pins the org run ceiling where it
// applies, under admission caps: the central guard refuses every Messages
// call before the handler, with or without a run id, stream or not, with
// its documented code and run identity, and no model call is made.
func TestMessagesRunBudgetAdmissionRunPolicy(t *testing.T) {
	g, _, _ := capComposedGateway(t)
	cfg := g.tenants.Get("org-m")
	cfg.Budgets.Agents, cfg.Budgets.KeyIDs = nil, nil
	limit := 0.5
	cfg.Routing.Agents.MaxRunUSD = &limit
	g.tenants.Set("org-m", cfg)
	for _, stream := range []string{"false", "true"} {
		for _, run := range []string{"mrb-policy-run", ""} {
			t.Run(fmt.Sprintf("run=%q, stream=%s", run, stream), func(t *testing.T) {
				headers := map[string]string{"Authorization": "Bearer " + managedOrgKey, "anthropic-version": "2023-06-01"}
				if run != "" {
					headers["x-agentcc-run-id"] = run
				}
				w := serveWithHeaders(t, g.srv, "POST", "/v1/messages",
					`{"model":"openai/gpt-6-sol","max_tokens":64,"messages":[{"role":"user","content":"hi"}],"stream":`+stream+`}`, headers, 5*time.Second)
				if w.Code != http.StatusPaymentRequired || errorCode(t, w) != "call_cap_unenforceable" || g.managed.calls() != 0 {
					t.Fatalf("HTTP %d, %d model calls: %s", w.Code, g.managed.calls(), w.Body.String())
				}
				if mrbDecode(t, w).Type != "error" {
					t.Fatalf("not Messages-shaped: %s", w.Body.String())
				}
				assertCapIdentity(t, w, "agent", "worker", "run", 1)
			})
		}
	}
}

// mrbNativeGateway is the native Anthropic pass-through of
// TestMessagesCallCapNativePassThrough with the run budget over store (nil:
// no Redis). Its key has no org, so runs are keyed under agentbudget's "_".
func mrbNativeGateway(t *testing.T, store *redisstate.AgentBudgetStore) (*Server, *atomic.Int32) {
	t.Helper()
	calls := &atomic.Int32{}
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"id":"msg_mrb","type":"message","role":"assistant","model":"claude-fable-5-1-20260831","content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","usage":{"input_tokens":5,"output_tokens":1}}`)
	}))
	t.Cleanup(up.Close)
	off := false
	cfg := config.DefaultConfig()
	cfg.Providers["anthropic"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "synthetic-key", APIFormat: "anthropic",
		Models: []string{"claude-fable-5-1-20260831"}, AutoDiscover: &off, Local: &off}
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "mrb", Key: "sk-synthetic-mrb", Owner: "synthetic-user", KeyType: "internal"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keys := auth.NewKeyStore(cfg.Auth)
	plugin := agentbudget.New(agentbudget.Deps{Store: store})
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true), plugin), keys, nil, nil, nil, testModelDBPtr(), nil, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	srv.handlers.SetRunBudget(plugin)
	return srv, calls
}

// TestMessagesRunBudgetNativePassThrough: the native pass-through counts no
// run either, so a run budget the caller sends is refused before dispatch
// with the run code; a run id alone is served as before.
func TestMessagesRunBudgetNativePassThrough(t *testing.T) {
	srv, calls := mrbNativeGateway(t, nil)
	body := mccBody("claude-fable-5-1-20260831", 64, false, "")
	w := mccMessages(t, srv, "sk-synthetic-mrb", body, "x-agentcc-run-id", "mrb-native-free")
	mrbAssertServed(t, w, int(calls.Load()))

	for _, stream := range []bool{false, true} {
		for _, c := range []struct {
			name    string
			extra   string
			headers []string
		}{
			{"run-id and budget-usd", "", []string{"x-agentcc-run-id", "mrb-native-1", "x-agentcc-budget-usd", "10"}},
			{"metadata.agent run_id and budget_usd", `,"metadata":{"agent":{"run_id":"mrb-native-2","budget_usd":10}}`, nil},
		} {
			t.Run(fmt.Sprintf("%s, stream=%t", c.name, stream), func(t *testing.T) {
				before := calls.Load()
				w := mccMessages(t, srv, "sk-synthetic-mrb", mccBody("claude-fable-5-1-20260831", 64, stream, c.extra), c.headers...)
				mrbAssertRunRefused(t, w, int(calls.Load()-before))
			})
		}
	}
}

// TestMessagesRunBudgetNativeStoredCeiling: a run that holds a stored budget
// is refused on the native pass-through too; another run is served.
func TestMessagesRunBudgetNativeStoredCeiling(t *testing.T) {
	prefix := fmt.Sprintf("test:mrb-native-stored:%d:", time.Now().UnixNano())
	store := redisstate.NewAgentBudgetStore(r7AgentRedis(t, prefix), prefix)
	srv, calls := mrbNativeGateway(t, store)
	ctx := context.Background()
	if _, err := store.Reserve(ctx, "_", "mrb-native-stored", "mrb-seed", 0, 10_000_000, time.Minute, time.Hour); err != nil {
		t.Fatal(err)
	}
	if _, err := store.Void(ctx, "_", "mrb-native-stored", "mrb-seed", time.Hour); err != nil {
		t.Fatal(err)
	}
	if st, err := store.Peek(ctx, "_", "mrb-native-stored"); err != nil || !st.HasLimit() {
		t.Fatalf("the run's stored state %+v (%v), want a limit", st, err)
	}
	body := mccBody("claude-fable-5-1-20260831", 64, false, "")
	before := calls.Load()
	w := mccMessages(t, srv, "sk-synthetic-mrb", body, "x-agentcc-run-id", "mrb-native-stored")
	mrbAssertRunRefused(t, w, int(calls.Load()-before))
	before = calls.Load()
	w = mccMessages(t, srv, "sk-synthetic-mrb", body, "x-agentcc-run-id", "mrb-native-other")
	mrbAssertServed(t, w, int(calls.Load()-before))
}
