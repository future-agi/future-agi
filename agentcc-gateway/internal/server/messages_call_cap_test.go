package server

// The per-call cost limit on /v1/messages (seen in a launch walk-through,
// F9-3): chat refused an over-cap call with 402 before any model call, while
// the same cap on POST /v1/messages was served and charged. These tests send
// the walk's request shape (Bearer key, anthropic-version 2023-06-01,
// x-agentcc-max-cost-usd 0.0000001, max_tokens 64, one user message) and the
// caps chain's Messages cases (fe053a65 caller and org per-call sources and
// the clamp reaching the provider; bb913f2d's "zero" and "fits"), through the
// real server with the run-budget plugin wired as cmd/agentcc wires it
// (r7BudgetGateway). No Redis: per-call limits need no run store.

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/plugins/agentbudget"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// mccMessages sends a Messages request with key and headers (name, value
// pairs), as the walk did.
func mccMessages(t *testing.T, srv *Server, key, body string, headers ...string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/v1/messages", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("anthropic-version", "2023-06-01")
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// mccBody is a Messages body for model with maxTokens, streamed or not.
func mccBody(model string, maxTokens int, stream bool, extra string) string {
	return fmt.Sprintf(`{"model":%q,"max_tokens":%d,"stream":%t,"messages":[{"role":"user","content":"walk-b2gw-f9 messages"}]%s}`, model, maxTokens, stream, extra)
}

// mccAssertRefused checks a 402 in the Anthropic error envelope whose message
// contains want, and that no request reached the upstream.
func mccAssertRefused(t *testing.T, w *httptest.ResponseRecorder, want string, upstreamCalls int) {
	t.Helper()
	var body struct {
		Type  string `json:"type"`
		Error struct {
			Type    string `json:"type"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if w.Code != http.StatusPaymentRequired {
		t.Fatalf("HTTP %d: %s, want 402 before dispatch", w.Code, w.Body.String())
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil || body.Type != "error" || !strings.Contains(body.Error.Message, want) {
		t.Fatalf("refusal body %s (err %v), want an Anthropic error naming %q", w.Body.String(), err, want)
	}
	if upstreamCalls != 0 {
		t.Fatalf("the upstream was called %d times for a refused call", upstreamCalls)
	}
}

// TestMessagesCallCapRefusedBeforeDispatch: an over-cap Messages call is
// refused 402 before any model call, stream or not, whichever way the cap is
// sent, exactly as chat refuses it.
func TestMessagesCallCapRefusedBeforeDispatch(t *testing.T) {
	up := startHookUpstream(t, 0)
	ht := r7BudgetGateway(t, up.URL, nil)

	t.Run("chat refuses the walk's cap (control)", func(t *testing.T) {
		before := len(up.received())
		w := r7BudgetChat(t, ht, r7BudgetKey, `{"model":"gpt-4o","max_tokens":64,"messages":[{"role":"user","content":"walk-b2gw-f9 chat"}]}`,
			"x-agentcc-max-cost-usd", "0.0000001")
		if w.Code != http.StatusPaymentRequired || len(up.received()) != before {
			t.Fatalf("chat: HTTP %d, %d upstream calls: %s", w.Code, len(up.received())-before, w.Body.String())
		}
	})

	for _, c := range []struct {
		name    string
		stream  bool
		extra   string
		headers []string
	}{
		{"the walk's request (x-agentcc-max-cost-usd 0.0000001)", false, "", []string{"x-agentcc-max-cost-usd", "0.0000001"}},
		{"the walk's request, streamed", true, "", []string{"x-agentcc-max-cost-usd", "0.0000001"}},
		{"max-cost-usd 0", false, "", []string{"x-agentcc-max-cost-usd", "0"}},
		{"x-omega alias", false, "", []string{"x-omega-max-cost-usd", "0.0000001"}},
		{"budget-remaining 0", true, "", []string{"x-agentcc-budget-remaining-usd", "0"}},
		{"budget-usd with no run id", false, "", []string{"x-agentcc-budget-usd", "0.0000001"}},
		{"in a run", false, "", []string{"x-agentcc-run-id", "mcc-run-1", "x-agentcc-max-cost-usd", "0.0000001"}},
		{"metadata.agent.max_cost_usd in the body", false, `,"metadata":{"agent":{"max_cost_usd":0.0000001}}`, nil},
	} {
		t.Run(c.name, func(t *testing.T) {
			before := len(up.received())
			w := mccMessages(t, ht.srv, r7BudgetKey, mccBody("gpt-4o", 64, c.stream, c.extra), c.headers...)
			mccAssertRefused(t, w, "worst-case cost", len(up.received())-before)
		})
	}
}

// TestMessagesCallCapFitsAndClamps: a cap the call fits under changes
// nothing; one it is over by its max_tokens alone clamps max_tokens to fit,
// and the provider is sent the clamped value (chat's clamp, >= 256 tokens).
func TestMessagesCallCapFitsAndClamps(t *testing.T) {
	up := startHookUpstream(t, 0)
	ht := r7BudgetGateway(t, up.URL, nil)

	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("no cap, stream=%t (control)", stream), func(t *testing.T) {
			before := len(up.received())
			w := mccMessages(t, ht.srv, r7BudgetKey, mccBody("gpt-4o", 2000, stream, ""))
			sent := up.received()[before:]
			if w.Code != http.StatusOK || len(sent) != 1 || sent[0]["max_tokens"] != float64(2000) {
				t.Fatalf("HTTP %d, sent %v: %s", w.Code, sent, w.Body.String())
			}
		})
		t.Run(fmt.Sprintf("a cap it fits under, stream=%t", stream), func(t *testing.T) {
			before := len(up.received())
			w := mccMessages(t, ht.srv, r7BudgetKey, mccBody("gpt-4o", 2000, stream, ""), "x-agentcc-max-cost-usd", "10")
			sent := up.received()[before:]
			if w.Code != http.StatusOK || w.Header().Get(agentbudget.HeaderMaxTokensClamped) != "" || len(sent) != 1 || sent[0]["max_tokens"] != float64(2000) {
				t.Fatalf("HTTP %d, clamped %q, sent %v: %s", w.Code, w.Header().Get(agentbudget.HeaderMaxTokensClamped), sent, w.Body.String())
			}
		})
		t.Run(fmt.Sprintf("a cap it is over, clamped, stream=%t", stream), func(t *testing.T) {
			before := len(up.received())
			w := mccMessages(t, ht.srv, r7BudgetKey, mccBody("gpt-4o", 2000, stream, ""), "x-agentcc-max-cost-usd", r7USD(r7Worst(t, ht, 300)))
			sent := up.received()[before:]
			clamped, err := strconv.Atoi(w.Header().Get(agentbudget.HeaderMaxTokensClamped))
			if w.Code != http.StatusOK || err != nil || clamped < 256 || clamped >= 2000 {
				t.Fatalf("HTTP %d, clamped %q: %s", w.Code, w.Header().Get(agentbudget.HeaderMaxTokensClamped), w.Body.String())
			}
			if len(sent) != 1 || sent[0]["max_tokens"] != float64(clamped) {
				t.Fatalf("the upstream was sent %v, want max_tokens %d", sent, clamped)
			}
		})
	}
}

// TestMessagesOrgMaxCallCost: the org's managed_models.max_call_cost_usd is a
// per-call cap on a routed Messages call as on chat.
func TestMessagesOrgMaxCallCost(t *testing.T) {
	up := startHookUpstream(t, 0)
	ht := r7BudgetGateway(t, up.URL, nil)
	tiny := 0.0000001
	ht.srv.TenantStore.Set(r7BudgetOrg, &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{MaxCallCostUSD: &tiny}})

	before := len(up.received())
	w := r7BudgetChat(t, ht, r7BudgetKey, `{"model":"gpt-4o","max_tokens":64,"messages":[{"role":"user","content":"hi"}]}`)
	if w.Code != http.StatusPaymentRequired || len(up.received()) != before {
		t.Fatalf("chat (control): HTTP %d, %d upstream calls: %s", w.Code, len(up.received())-before, w.Body.String())
	}
	for _, stream := range []bool{false, true} {
		before := len(up.received())
		w := mccMessages(t, ht.srv, r7BudgetKey, mccBody("gpt-4o", 64, stream, ""))
		mccAssertRefused(t, w, "worst-case cost", len(up.received())-before)
	}
}

// TestMessagesCallCapNativePassThrough: a native Anthropic pass-through sends
// the caller's bytes as written, so a per-call cap it cannot fit is refused
// with a plain 4xx before dispatch; a request without one is served as before.
func TestMessagesCallCapNativePassThrough(t *testing.T) {
	var calls atomic.Int32
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"id":"msg_mcc","type":"message","role":"assistant","model":"claude-fable-5-1-20260831","content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","usage":{"input_tokens":5,"output_tokens":1}}`)
	}))
	t.Cleanup(up.Close)
	off := false
	cfg := config.DefaultConfig()
	cfg.Providers["anthropic"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "synthetic-key", APIFormat: "anthropic",
		Models: []string{"claude-fable-5-1-20260831"}, AutoDiscover: &off, Local: &off}
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "mcc", Key: "sk-synthetic-mcc", Owner: "synthetic-user", KeyType: "internal"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keys := auth.NewKeyStore(cfg.Auth)
	plugin := agentbudget.New(agentbudget.Deps{})
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true), plugin), keys, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	srv.handlers.SetRunBudget(plugin)

	body := mccBody("claude-fable-5-1-20260831", 64, false, "")
	w := mccMessages(t, srv, "sk-synthetic-mcc", body)
	if w.Code != http.StatusOK || calls.Load() != 1 {
		t.Fatalf("no cap (control): HTTP %d, %d upstream calls: %s", w.Code, calls.Load(), w.Body.String())
	}
	for _, h := range [][]string{
		{"x-agentcc-max-cost-usd", "0.0000001"},
		{"x-omega-max-cost-usd", "10"},
		{"x-agentcc-budget-usd", "1"},
	} {
		before := calls.Load()
		w := mccMessages(t, srv, "sk-synthetic-mcc", body, h...)
		mccAssertRefused(t, w, "native Anthropic pass-through", int(calls.Load()-before))
	}
}
