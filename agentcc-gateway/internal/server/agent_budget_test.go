package server

// Per-call cost caps (x-agentcc-max-cost-usd, x-agentcc-budget-remaining-usd),
// run budgets (x-agentcc-budget-usd with x-agentcc-run-id or
// x-agentcc-budget-scope) and the no-Redis rule, through the real chat path.
// The server is built with the run-budget plugin wired the way
// cmd/agentcc/wiring_agentbudget.go wires it (pipeline plugin plus the
// runBudgetCap hook); cmd/agentcc's TestAgentBudgetWiring runs the real
// wiring.

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
	runBudgetKey     = "sk-agent-budget"
	runBudgetOpenKey = "sk-agent-budget-open"
	runBudgetOrg     = "org-agent"
	runBudgetOpenOrg = "org-agent-open"
)

// testRedisClient is a client on DB TEST_REDIS_DB of TEST_REDIS_ADDR, the only
// database these tests write. It skips without TEST_REDIS_ADDR. Every key
// under prefix is deleted when the test ends (SCAN and DEL, never FLUSHDB).
func testRedisClient(t *testing.T, prefix string) *redisstate.Client {
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

// runBudgetGateway is a real server with one provider, "openai", serving
// gpt-4o on upstreamURL, and two internal keys: one of runBudgetOrg (no org
// config: budgets.fail_mode closed, the default) and one of runBudgetOpenOrg
// (budgets.fail_mode open). Its pipeline runs the run-budget plugin over
// store (nil: the gateway has no Redis), installed as the runBudgetCap hook.
func runBudgetGateway(t *testing.T, upstreamURL string, store *redisstate.AgentBudgetStore) *hookOrderTest {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "agent", Key: runBudgetKey, Owner: "t", KeyType: "internal", Metadata: map[string]string{"org_id": runBudgetOrg}},
		{Name: "agent-open", Key: runBudgetOpenKey, Owner: "t", KeyType: "internal", Metadata: map[string]string{"org_id": runBudgetOpenOrg}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstreamURL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Routing.ProviderLock = config.ProviderLockConfig{Enabled: true}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	tenants.Set(runBudgetOpenOrg, &tenant.OrgConfig{Budgets: &tenant.BudgetsConfig{FailMode: "open"}})
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

// runBudgetChat sends body with key and headers (name, value pairs).
func runBudgetChat(t *testing.T, ht *hookOrderTest, key, body string, headers ...string) *httptest.ResponseRecorder {
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

const runBudgetBody = `{"model":"gpt-4o","max_tokens":2000,"messages":[{"role":"user","content":"hi"}]}`

// runBudgetWorst is the BYOK worst case of runBudgetBody at maxTokens, and usd
// writes micros as a header value.
func runBudgetWorst(t *testing.T, ht *hookOrderTest, maxTokens int) int64 {
	t.Helper()
	card, ok := ht.srv.handlers.Deps().Prices("openai", "gpt-4o", "")
	if !ok || !(card.Completion > 0) {
		t.Fatalf("the test model DB prices no gpt-4o completion: %+v", card)
	}
	var req models.ChatCompletionRequest
	if err := json.Unmarshal([]byte(runBudgetBody), &req); err != nil {
		t.Fatal(err)
	}
	req.MaxTokens = &maxTokens
	return billing.EstimateWorstCaseMicros(&req, card, 0, 4096)
}

func runBudgetUSD(micros int64) string { return fmt.Sprintf("%.6f", float64(micros)/1e6) }

func runBudgetErrorMeta(t *testing.T, b []byte) (string, map[string]any) {
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

// TestAgentMaxCost: a per-call cap refuses a call no max_tokens of at
// least 256 fits under (402 agent_call_cap_exceeded, before routing: the
// upstream is never called), clamps one over the cap to the largest
// max_tokens that fits, and leaves one under it alone, in a run or outside
// any run (x-agentcc-max-cost-usd with no run id included).
func TestAgentMaxCost(t *testing.T) {
	up := startHookUpstream(t, 0)
	prefix := fmt.Sprintf("test:agent-maxcost:%d:", time.Now().UnixNano())
	ht := runBudgetGateway(t, up.URL, redisstate.NewAgentBudgetStore(testRedisClient(t, prefix), prefix))

	for _, c := range []struct {
		name    string
		headers []string
	}{
		{"no run id", []string{"x-agentcc-max-cost-usd", "0.0000001"}},
		{"x-omega alias, no run id", []string{"x-omega-max-cost-usd", "0.0000001"}},
		{"in a run", []string{"x-agentcc-run-id", "mc-1", "x-agentcc-max-cost-usd", "0.0000001"}},
		{"budget-remaining 0 in a run", []string{"x-agentcc-run-id", "mc-2", "x-agentcc-budget-remaining-usd", "0"}},
		{"budget-remaining 0, no run id", []string{"x-agentcc-budget-remaining-usd", "0"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			before := len(up.received())
			w := runBudgetChat(t, ht, runBudgetKey, runBudgetBody, c.headers...)
			code, meta := runBudgetErrorMeta(t, w.Body.Bytes())
			if w.Code != http.StatusPaymentRequired || code != agentbudget.CodeCallCapExceeded ||
				meta["limit_source"] != models.LimitSourceCallCap {
				t.Fatalf("HTTP %d: %s, want 402 %s", w.Code, w.Body.String(), agentbudget.CodeCallCapExceeded)
			}
			if n := len(up.received()) - before; n != 0 {
				t.Fatalf("the upstream was called %d times", n)
			}
		})
	}

	for _, c := range []struct{ name, run string }{{"clamped in a run", "mc-3"}, {"clamped with no run id", ""}} {
		t.Run(c.name, func(t *testing.T) {
			headers := []string{"x-agentcc-max-cost-usd", runBudgetUSD(runBudgetWorst(t, ht, 300))}
			if c.run != "" {
				headers = append(headers, "x-agentcc-run-id", c.run)
			}
			before := len(up.received())
			w := runBudgetChat(t, ht, runBudgetKey, runBudgetBody, headers...)
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
		w := runBudgetChat(t, ht, runBudgetKey, runBudgetBody, "x-agentcc-max-cost-usd", "10")
		if w.Code != http.StatusOK || w.Header().Get(agentbudget.HeaderMaxTokensClamped) != "" {
			t.Fatalf("HTTP %d: %s", w.Code, w.Body.String())
		}
		if sent := up.received()[before:]; len(sent) != 1 || sent[0]["max_tokens"] != float64(2000) {
			t.Fatalf("the upstream was sent %v, want max_tokens 2000", sent)
		}
	})
}

// TestAgentRunBudget: a run's budget is shared by every call in the run;
// a call its budget cannot hold is refused before routing with 402
// agent_budget_exhausted, and every call in the run carries what it leaves
// (x-agentcc-budget-remaining-usd). budget-scope (alias ledger-id) names the
// run like run-id. A budget sent with no run id covers that call alone.
func TestAgentRunBudget(t *testing.T) {
	up := startHookUpstream(t, 0)
	prefix := fmt.Sprintf("test:agent-runbudget:%d:", time.Now().UnixNano())
	client := testRedisClient(t, prefix)
	ht := runBudgetGateway(t, up.URL, redisstate.NewAgentBudgetStore(client, prefix))
	worst := runBudgetWorst(t, ht, 2000)

	t.Run("1e-7 USD for run r1", func(t *testing.T) {
		before := len(up.received())
		w := runBudgetChat(t, ht, runBudgetKey, runBudgetBody, "x-agentcc-run-id", "r1", "x-agentcc-budget-usd", "0.0000001")
		code, meta := runBudgetErrorMeta(t, w.Body.Bytes())
		if w.Code != http.StatusPaymentRequired || code != agentbudget.CodeBudgetExhausted || meta["scope"] != "run:r1" {
			t.Fatalf("HTTP %d: %s, want 402 %s for run:r1", w.Code, w.Body.String(), agentbudget.CodeBudgetExhausted)
		}
		if n := len(up.received()) - before; n != 0 {
			t.Fatalf("the upstream was called %d times", n)
		}
	})

	for _, c := range []struct{ name, header, run string }{
		{"run-id", "x-agentcc-run-id", "rb-run"},
		{"budget-scope", "x-agentcc-budget-scope", "rb-scope"},
		{"ledger-id", "x-agentcc-ledger-id", "rb-ledger"},
	} {
		t.Run("a budget of exactly one call, by "+c.name, func(t *testing.T) {
			before := len(up.received())
			w := runBudgetChat(t, ht, runBudgetKey, runBudgetBody, c.header, c.run, "x-agentcc-budget-usd", runBudgetUSD(worst))
			if w.Code != http.StatusOK || w.Header().Get(agentbudget.HeaderBudgetRemaining) != "0.000000" {
				t.Fatalf("first call: HTTP %d, remaining %q: %s", w.Code, w.Header().Get(agentbudget.HeaderBudgetRemaining), w.Body.String())
			}
			// The second call names the run only: the budget is the run's.
			w = runBudgetChat(t, ht, runBudgetKey, runBudgetBody, c.header, c.run)
			code, meta := runBudgetErrorMeta(t, w.Body.Bytes())
			if w.Code != http.StatusPaymentRequired || code != agentbudget.CodeBudgetExhausted || meta["scope"] != "run:"+c.run {
				t.Fatalf("a call in a spent run: HTTP %d: %s", w.Code, w.Body.String())
			}
			if n := len(up.received()) - before; n != 1 {
				t.Fatalf("the upstream was called %d times, want 1", n)
			}
			st, err := redisstate.NewAgentBudgetStore(client, prefix).Peek(context.Background(), runBudgetOrg, c.run)
			if err != nil || st.SpentMicros != worst || st.HeldMicros != 0 {
				t.Fatalf("the run's state %+v (%v), want spent %d and nothing held", st, err, worst)
			}
		})
	}

	t.Run("calls in one run draw on one budget", func(t *testing.T) {
		budget := 3 * worst
		var remaining []string
		for i := 0; i < 3; i++ {
			w := runBudgetChat(t, ht, runBudgetKey, runBudgetBody, "x-agentcc-run-id", "rb-shared", "x-agentcc-budget-usd", runBudgetUSD(budget))
			if w.Code != http.StatusOK {
				t.Fatalf("call %d: HTTP %d %s", i+1, w.Code, w.Body.String())
			}
			remaining = append(remaining, w.Header().Get(agentbudget.HeaderBudgetRemaining))
		}
		want := []string{runBudgetUSD(2 * worst), runBudgetUSD(worst), "0.000000"}
		if strings.Join(remaining, ",") != strings.Join(want, ",") {
			t.Fatalf("remaining after each call %v, want %v", remaining, want)
		}
		w := runBudgetChat(t, ht, runBudgetKey, runBudgetBody, "x-agentcc-run-id", "rb-shared", "x-agentcc-budget-usd", runBudgetUSD(budget))
		if code, _ := runBudgetErrorMeta(t, w.Body.Bytes()); w.Code != http.StatusPaymentRequired || code != agentbudget.CodeBudgetExhausted {
			t.Fatalf("the fourth call: HTTP %d %s", w.Code, w.Body.String())
		}
	})

	t.Run("runs of different orgs are separate", func(t *testing.T) {
		w := runBudgetChat(t, ht, runBudgetOpenKey, runBudgetBody, "x-agentcc-run-id", "rb-run", "x-agentcc-budget-usd", runBudgetUSD(worst))
		if w.Code != http.StatusOK {
			t.Fatalf("another org's run of the same id: HTTP %d %s", w.Code, w.Body.String())
		}
	})

	t.Run("a budget with no run id covers that call alone", func(t *testing.T) {
		for i := 0; i < 2; i++ {
			w := runBudgetChat(t, ht, runBudgetKey, runBudgetBody, "x-agentcc-budget-usd", runBudgetUSD(worst))
			if w.Code != http.StatusOK || w.Header().Get(agentbudget.HeaderBudgetRemaining) != "" {
				t.Fatalf("call %d: HTTP %d, remaining %q: %s", i+1, w.Code, w.Header().Get(agentbudget.HeaderBudgetRemaining), w.Body.String())
			}
		}
		w := runBudgetChat(t, ht, runBudgetKey, runBudgetBody, "x-agentcc-budget-usd", "0.0000001")
		code, meta := runBudgetErrorMeta(t, w.Body.Bytes())
		if w.Code != http.StatusPaymentRequired || code != agentbudget.CodeBudgetExhausted {
			t.Fatalf("HTTP %d: %s", w.Code, w.Body.String())
		}
		if _, named := meta["scope"]; named {
			t.Fatalf("a call outside any run named a scope: %v", meta)
		}
	})
}

// TestAgentRunBudgetWithoutRedis: without Redis a call that sends a run budget is refused
// with 503 budget_state_unavailable (a run budget fails closed), unless its
// org sets budgets.fail_mode=open, when it is served unheld. A call in a run
// that sends no budget has nothing to enforce and is served, and a per-call
// cap needs no Redis at all.
func TestAgentRunBudgetWithoutRedis(t *testing.T) {
	up := startHookUpstream(t, 0)
	ht := runBudgetGateway(t, up.URL, nil)

	w := runBudgetChat(t, ht, runBudgetKey, runBudgetBody, "x-agentcc-run-id", "nr", "x-agentcc-budget-usd", "5")
	code, meta := runBudgetErrorMeta(t, w.Body.Bytes())
	if w.Code != http.StatusServiceUnavailable || code != agentbudget.CodeBudgetStateUnavailable || meta["scope"] != "run:nr" {
		t.Fatalf("closed (the default): HTTP %d %s, want 503 %s", w.Code, w.Body.String(), agentbudget.CodeBudgetStateUnavailable)
	}
	if n := len(up.received()); n != 0 {
		t.Fatalf("the upstream was called %d times", n)
	}

	if w := runBudgetChat(t, ht, runBudgetOpenKey, runBudgetBody, "x-agentcc-run-id", "nr", "x-agentcc-budget-usd", "5"); w.Code != http.StatusOK {
		t.Fatalf("budgets.fail_mode=open: HTTP %d %s, want 200", w.Code, w.Body.String())
	}
	if w := runBudgetChat(t, ht, runBudgetKey, runBudgetBody, "x-agentcc-run-id", "nr"); w.Code != http.StatusOK {
		t.Fatalf("a run with no budget: HTTP %d %s, want 200", w.Code, w.Body.String())
	}
	w = runBudgetChat(t, ht, runBudgetKey, runBudgetBody, "x-agentcc-max-cost-usd", "0.0000001")
	if code, _ := runBudgetErrorMeta(t, w.Body.Bytes()); w.Code != http.StatusPaymentRequired || code != agentbudget.CodeCallCapExceeded {
		t.Fatalf("a per-call cap without Redis: HTTP %d %s, want 402 %s", w.Code, w.Body.String(), agentbudget.CodeCallCapExceeded)
	}
	if w := runBudgetChat(t, ht, runBudgetKey, runBudgetBody); w.Code != http.StatusOK {
		t.Fatalf("a call with no agent headers: HTTP %d %s", w.Code, w.Body.String())
	}
}
