package server

// R7-AGENT, NG-27 (run-budget half) through the real chat path: per-call cost
// caps (x-agentcc-max-cost-usd, x-agentcc-budget-remaining-usd), run budgets
// (x-agentcc-budget-usd with x-agentcc-run-id or x-agentcc-budget-scope) and
// the no-Redis rule. The server is built with the run-budget plugin wired the
// way cmd/agentcc/wiring_agentbudget.go wires it (pipeline plugin plus the
// runBudgetCap hook); cmd/agentcc's TestR7AgentBudgetWiring runs the real
// wiring and is red at the release base aed3c18c57, where none of this was
// read (gateway-b1 probe: every case 200 after the 400 ms upstream).

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/plugins/agentbudget"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	r7BudgetKey     = "sk-r7-agent-budget"
	r7BudgetOpenKey = "sk-r7-agent-budget-open"
	r7BudgetOrg     = "org-r7-agent"
	r7BudgetOpenOrg = "org-r7-agent-open"
)

// r7AgentRedis is a client on DB TEST_REDIS_DB of TEST_REDIS_ADDR, the only
// database these tests write. It skips without TEST_REDIS_ADDR. Every key
// under prefix is deleted when the test ends (SCAN and DEL, never FLUSHDB).
func r7AgentRedis(t *testing.T, prefix string) *redisstate.Client {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("skipping: TEST_REDIS_ADDR is not set")
	}
	db := 0
	if v := os.Getenv("TEST_REDIS_DB"); v != "" {
		var err error
		if db, err = strconv.Atoi(v); err != nil || db < 0 {
			t.Fatalf("TEST_REDIS_DB=%q is not a database number", v)
		}
	}
	client, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: db, Timeout: time.Second})
	if err != nil {
		t.Skipf("skipping: Redis not available at %s: %v", addr, err)
	}
	t.Cleanup(func() {
		ctx := context.Background()
		iter := client.Redis().Scan(ctx, 0, prefix+"*", 1000).Iterator()
		var keys []string
		for iter.Next(ctx) {
			keys = append(keys, iter.Val())
		}
		if len(keys) > 0 {
			client.Redis().Del(ctx, keys...)
		}
		client.Close()
	})
	return client
}

// r7BudgetGateway is a real server with one provider, "openai", serving
// gpt-4o on upstreamURL, and two internal keys: one of r7BudgetOrg (no org
// config: budgets.fail_mode closed, the default) and one of r7BudgetOpenOrg
// (budgets.fail_mode open). Its pipeline runs the run-budget plugin over
// store (nil: the gateway has no Redis), installed as the runBudgetCap hook.
func r7BudgetGateway(t *testing.T, upstreamURL string, store *redisstate.AgentBudgetStore) *hookOrderTest {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "r7", Key: r7BudgetKey, Owner: "t", KeyType: "internal", Metadata: map[string]string{"org_id": r7BudgetOrg}},
		{Name: "r7-open", Key: r7BudgetOpenKey, Owner: "t", KeyType: "internal", Metadata: map[string]string{"org_id": r7BudgetOpenOrg}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstreamURL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Routing.ProviderLock = config.ProviderLockConfig{Enabled: true}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	tenants.Set(r7BudgetOpenOrg, &tenant.OrgConfig{Budgets: &tenant.BudgetsConfig{FailMode: "open"}})
	keyStore := auth.NewKeyStore(cfg.Auth)
	var h *Handlers
	plugin := agentbudget.New(agentbudget.Deps{
		Store:    store,
		Settings: agentbudget.Settings{HoldTTL: time.Minute, ReserveDefaultOutputTokens: 4096},
		Prices: func(providerID, model, endpointID string) (models.PriceCard, bool) {
			return h.Deps().Prices(providerID, model, endpointID)
		},
		OrgConfig: tenants.Get,
	})
	engine := pipeline.NewEngine(authplugin.New(keyStore, true), plugin)
	srv := New(cfg, "", registry, engine, keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	h = srv.handlers
	h.SetRunBudget(plugin)
	return &hookOrderTest{srv: srv, ev: &hookEvents{}}
}

// r7BudgetChat sends body with key and headers (name, value pairs).
func r7BudgetChat(t *testing.T, ht *hookOrderTest, key, body string, headers ...string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	w := httptest.NewRecorder()
	ht.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

const r7BudgetBody = `{"model":"gpt-4o","max_tokens":2000,"messages":[{"role":"user","content":"hi"}]}`

// r7Worst is the BYOK worst case of r7BudgetBody at maxTokens, and usd
// writes micros as a header value.
func r7Worst(t *testing.T, ht *hookOrderTest, maxTokens int) int64 {
	t.Helper()
	card, ok := ht.srv.handlers.Deps().Prices("openai", "gpt-4o", "")
	if !ok || !(card.Completion > 0) {
		t.Fatalf("the test model DB prices no gpt-4o completion: %+v", card)
	}
	var req models.ChatCompletionRequest
	if err := json.Unmarshal([]byte(r7BudgetBody), &req); err != nil {
		t.Fatal(err)
	}
	req.MaxTokens = &maxTokens
	return billing.EstimateWorstCaseMicros(&req, card, 0, 4096)
}

func r7USD(micros int64) string { return fmt.Sprintf("%.6f", float64(micros)/1e6) }

func r7ErrorMeta(t *testing.T, b []byte) (string, map[string]any) {
	t.Helper()
	var body struct {
		Error struct {
			Code     string         `json:"code"`
			Metadata map[string]any `json:"metadata"`
		} `json:"error"`
	}
	if err := json.Unmarshal(b, &body); err != nil {
		t.Fatalf("decoding %s: %v", b, err)
	}
	return body.Error.Code, body.Error.Metadata
}

// TestR7AgentMaxCost: a per-call cap refuses a call no max_tokens of at
// least 256 fits under (402 agent_call_cap_exceeded, before routing: the
// upstream is never called), clamps one over the cap to the largest
// max_tokens that fits, and leaves one under it alone, in a run or outside
// any run (the audit probe's case: x-agentcc-max-cost-usd with no run id).
func TestR7AgentMaxCost(t *testing.T) {
	up := startHookUpstream(t, 0)
	prefix := fmt.Sprintf("test:r7-agent-maxcost:%d:", time.Now().UnixNano())
	ht := r7BudgetGateway(t, up.URL, redisstate.NewAgentBudgetStore(r7AgentRedis(t, prefix), prefix))

	for _, c := range []struct {
		name    string
		headers []string
	}{
		{"no run id (the audit probe)", []string{"x-agentcc-max-cost-usd", "0.0000001"}},
		{"x-omega alias, no run id", []string{"x-omega-max-cost-usd", "0.0000001"}},
		{"in a run", []string{"x-agentcc-run-id", "r7-mc-1", "x-agentcc-max-cost-usd", "0.0000001"}},
		{"budget-remaining 0 in a run", []string{"x-agentcc-run-id", "r7-mc-2", "x-agentcc-budget-remaining-usd", "0"}},
		{"budget-remaining 0, no run id", []string{"x-agentcc-budget-remaining-usd", "0"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			before := len(up.received())
			w := r7BudgetChat(t, ht, r7BudgetKey, r7BudgetBody, c.headers...)
			code, meta := r7ErrorMeta(t, w.Body.Bytes())
			if w.Code != http.StatusPaymentRequired || code != agentbudget.CodeCallCapExceeded ||
				meta["limit_source"] != models.LimitSourceCallCap {
				t.Fatalf("HTTP %d: %s, want 402 %s", w.Code, w.Body.String(), agentbudget.CodeCallCapExceeded)
			}
			if n := len(up.received()) - before; n != 0 {
				t.Fatalf("the upstream was called %d times", n)
			}
		})
	}

	for _, c := range []struct{ name, run string }{{"clamped in a run", "r7-mc-3"}, {"clamped with no run id", ""}} {
		t.Run(c.name, func(t *testing.T) {
			headers := []string{"x-agentcc-max-cost-usd", r7USD(r7Worst(t, ht, 300))}
			if c.run != "" {
				headers = append(headers, "x-agentcc-run-id", c.run)
			}
			before := len(up.received())
			w := r7BudgetChat(t, ht, r7BudgetKey, r7BudgetBody, headers...)
			if w.Code != http.StatusOK || w.Header().Get(agentbudget.HeaderMaxTokensClamped) != "300" {
				t.Fatalf("HTTP %d, %s %q: %s", w.Code, agentbudget.HeaderMaxTokensClamped, w.Header().Get(agentbudget.HeaderMaxTokensClamped), w.Body.String())
			}
			sent := up.received()[before:]
			if len(sent) != 1 || sent[0]["max_tokens"] != float64(300) {
				t.Fatalf("the upstream was sent %v, want max_tokens 300", sent)
			}
		})
	}

	t.Run("a cap the call fits under changes nothing", func(t *testing.T) {
		before := len(up.received())
		w := r7BudgetChat(t, ht, r7BudgetKey, r7BudgetBody, "x-agentcc-max-cost-usd", "10")
		if w.Code != http.StatusOK || w.Header().Get(agentbudget.HeaderMaxTokensClamped) != "" {
			t.Fatalf("HTTP %d: %s", w.Code, w.Body.String())
		}
		if sent := up.received()[before:]; len(sent) != 1 || sent[0]["max_tokens"] != float64(2000) {
			t.Fatalf("the upstream was sent %v, want max_tokens 2000", sent)
		}
	})
}

// TestR7AgentRunBudget: a run's budget is shared by every call in the run;
// a call its budget cannot hold is refused before routing with 402
// agent_budget_exhausted, and every call in the run carries what it leaves
// (x-agentcc-budget-remaining-usd). budget-scope (alias ledger-id) names the
// run like run-id. A budget sent with no run id covers that call alone.
func TestR7AgentRunBudget(t *testing.T) {
	up := startHookUpstream(t, 0)
	prefix := fmt.Sprintf("test:r7-agent-runbudget:%d:", time.Now().UnixNano())
	client := r7AgentRedis(t, prefix)
	ht := r7BudgetGateway(t, up.URL, redisstate.NewAgentBudgetStore(client, prefix))
	worst := r7Worst(t, ht, 2000)

	t.Run("the audit probe: 1e-7 USD for run r1", func(t *testing.T) {
		before := len(up.received())
		w := r7BudgetChat(t, ht, r7BudgetKey, r7BudgetBody, "x-agentcc-run-id", "r1", "x-agentcc-budget-usd", "0.0000001")
		code, meta := r7ErrorMeta(t, w.Body.Bytes())
		if w.Code != http.StatusPaymentRequired || code != agentbudget.CodeBudgetExhausted || meta["scope"] != "run:r1" {
			t.Fatalf("HTTP %d: %s, want 402 %s for run:r1", w.Code, w.Body.String(), agentbudget.CodeBudgetExhausted)
		}
		if n := len(up.received()) - before; n != 0 {
			t.Fatalf("the upstream was called %d times", n)
		}
	})

	for _, c := range []struct{ name, header, run string }{
		{"run-id", "x-agentcc-run-id", "r7-rb-run"},
		{"budget-scope", "x-agentcc-budget-scope", "r7-rb-scope"},
		{"ledger-id", "x-agentcc-ledger-id", "r7-rb-ledger"},
	} {
		t.Run("a budget of exactly one call, by "+c.name, func(t *testing.T) {
			before := len(up.received())
			w := r7BudgetChat(t, ht, r7BudgetKey, r7BudgetBody, c.header, c.run, "x-agentcc-budget-usd", r7USD(worst))
			if w.Code != http.StatusOK || w.Header().Get(agentbudget.HeaderBudgetRemaining) != "0.000000" {
				t.Fatalf("first call: HTTP %d, remaining %q: %s", w.Code, w.Header().Get(agentbudget.HeaderBudgetRemaining), w.Body.String())
			}
			// The second call names the run only: the budget is the run's.
			w = r7BudgetChat(t, ht, r7BudgetKey, r7BudgetBody, c.header, c.run)
			code, meta := r7ErrorMeta(t, w.Body.Bytes())
			if w.Code != http.StatusPaymentRequired || code != agentbudget.CodeBudgetExhausted || meta["scope"] != "run:"+c.run {
				t.Fatalf("a call in a spent run: HTTP %d: %s", w.Code, w.Body.String())
			}
			if n := len(up.received()) - before; n != 1 {
				t.Fatalf("the upstream was called %d times, want 1", n)
			}
			st, err := redisstate.NewAgentBudgetStore(client, prefix).Peek(context.Background(), r7BudgetOrg, c.run)
			if err != nil || st.SpentMicros != worst || st.HeldMicros != 0 {
				t.Fatalf("the run's state %+v (%v), want spent %d and nothing held", st, err, worst)
			}
		})
	}

	t.Run("calls in one run draw on one budget", func(t *testing.T) {
		budget := 3 * worst
		var remaining []string
		for i := 0; i < 3; i++ {
			w := r7BudgetChat(t, ht, r7BudgetKey, r7BudgetBody, "x-agentcc-run-id", "r7-rb-shared", "x-agentcc-budget-usd", r7USD(budget))
			if w.Code != http.StatusOK {
				t.Fatalf("call %d: HTTP %d %s", i+1, w.Code, w.Body.String())
			}
			remaining = append(remaining, w.Header().Get(agentbudget.HeaderBudgetRemaining))
		}
		want := []string{r7USD(2 * worst), r7USD(worst), "0.000000"}
		if strings.Join(remaining, ",") != strings.Join(want, ",") {
			t.Fatalf("remaining after each call %v, want %v", remaining, want)
		}
		w := r7BudgetChat(t, ht, r7BudgetKey, r7BudgetBody, "x-agentcc-run-id", "r7-rb-shared", "x-agentcc-budget-usd", r7USD(budget))
		if code, _ := r7ErrorMeta(t, w.Body.Bytes()); w.Code != http.StatusPaymentRequired || code != agentbudget.CodeBudgetExhausted {
			t.Fatalf("the fourth call: HTTP %d %s", w.Code, w.Body.String())
		}
	})

	t.Run("runs of different orgs are separate", func(t *testing.T) {
		w := r7BudgetChat(t, ht, r7BudgetOpenKey, r7BudgetBody, "x-agentcc-run-id", "r7-rb-run", "x-agentcc-budget-usd", r7USD(worst))
		if w.Code != http.StatusOK {
			t.Fatalf("another org's run of the same id: HTTP %d %s", w.Code, w.Body.String())
		}
	})

	t.Run("a budget with no run id covers that call alone", func(t *testing.T) {
		for i := 0; i < 2; i++ {
			w := r7BudgetChat(t, ht, r7BudgetKey, r7BudgetBody, "x-agentcc-budget-usd", r7USD(worst))
			if w.Code != http.StatusOK || w.Header().Get(agentbudget.HeaderBudgetRemaining) != "" {
				t.Fatalf("call %d: HTTP %d, remaining %q: %s", i+1, w.Code, w.Header().Get(agentbudget.HeaderBudgetRemaining), w.Body.String())
			}
		}
		w := r7BudgetChat(t, ht, r7BudgetKey, r7BudgetBody, "x-agentcc-budget-usd", "0.0000001")
		code, meta := r7ErrorMeta(t, w.Body.Bytes())
		if w.Code != http.StatusPaymentRequired || code != agentbudget.CodeBudgetExhausted {
			t.Fatalf("HTTP %d: %s", w.Code, w.Body.String())
		}
		if _, named := meta["scope"]; named {
			t.Fatalf("a call outside any run named a scope: %v", meta)
		}
	})
}

// TestR7AgentNoRedis: without Redis a call that sends a run budget is refused
// with 503 budget_state_unavailable (a run budget fails closed), unless its
// org sets budgets.fail_mode=open, when it is served unheld. A call in a run
// that sends no budget has nothing to enforce and is served, and a per-call
// cap needs no Redis at all.
func TestR7AgentNoRedis(t *testing.T) {
	up := startHookUpstream(t, 0)
	ht := r7BudgetGateway(t, up.URL, nil)

	w := r7BudgetChat(t, ht, r7BudgetKey, r7BudgetBody, "x-agentcc-run-id", "r7-nr", "x-agentcc-budget-usd", "5")
	code, meta := r7ErrorMeta(t, w.Body.Bytes())
	if w.Code != http.StatusServiceUnavailable || code != agentbudget.CodeBudgetStateUnavailable || meta["scope"] != "run:r7-nr" {
		t.Fatalf("closed (the default): HTTP %d %s, want 503 %s", w.Code, w.Body.String(), agentbudget.CodeBudgetStateUnavailable)
	}
	if n := len(up.received()); n != 0 {
		t.Fatalf("the upstream was called %d times", n)
	}

	if w := r7BudgetChat(t, ht, r7BudgetOpenKey, r7BudgetBody, "x-agentcc-run-id", "r7-nr", "x-agentcc-budget-usd", "5"); w.Code != http.StatusOK {
		t.Fatalf("budgets.fail_mode=open: HTTP %d %s, want 200", w.Code, w.Body.String())
	}
	if w := r7BudgetChat(t, ht, r7BudgetKey, r7BudgetBody, "x-agentcc-run-id", "r7-nr"); w.Code != http.StatusOK {
		t.Fatalf("a run with no budget: HTTP %d %s, want 200", w.Code, w.Body.String())
	}
	w = r7BudgetChat(t, ht, r7BudgetKey, r7BudgetBody, "x-agentcc-max-cost-usd", "0.0000001")
	if code, _ := r7ErrorMeta(t, w.Body.Bytes()); w.Code != http.StatusPaymentRequired || code != agentbudget.CodeCallCapExceeded {
		t.Fatalf("a per-call cap without Redis: HTTP %d %s, want 402 %s", w.Code, w.Body.String(), agentbudget.CodeCallCapExceeded)
	}
	if w := r7BudgetChat(t, ht, r7BudgetKey, r7BudgetBody); w.Code != http.StatusOK {
		t.Fatalf("a call with no agent headers: HTTP %d %s", w.Code, w.Body.String())
	}
}
