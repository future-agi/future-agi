package server

// Org budgets through the real seams: the customer key
// arrives by POST /-/keys/sync as Django sends it, the org config by PUT
// /-/orgs/{org}/config (admin contract decode, unknown fields refused) with
// the budgets Django pushes after the Set Budget dialog
// (plugins/budget/testdata/console_push.json), and requests by
// /v1/chat/completions. The budget plugin is wired as cmd/agentcc/main.go
// wires it, including its InvalidateOrg on every org config change.

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	budgetplugin "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	consoleOrg   = "org-console"
	consoleCust  = "sk-agentcc-console-customer-0123456789abcdef0123456789"
	consoleAdmin = "console-budget-admin-token-5c1e9a7b3d2f8e60aa11bb22cc"
	// The org's provider; its key is layered onto the config.yaml entry.
	consoleProviders = `"providers":{"alpha":{"api_key":"org-alpha-key","api_format":"openai","models":["org-model"],"enabled":true}}`
)

func budgetFixture(t *testing.T, name string) string {
	t.Helper()
	body, err := os.ReadFile(filepath.Join("..", "plugins", "budget", "testdata", "console_push.json"))
	if err != nil {
		t.Fatal(err)
	}
	var all map[string]json.RawMessage
	if err := json.Unmarshal(body, &all); err != nil {
		t.Fatal(err)
	}
	c, ok := all[name]
	if !ok {
		t.Fatalf("fixture has no case %q", name)
	}
	return string(c)
}

type budgetGateway struct {
	srv   *Server
	calls *atomic.Int64
}

func startBudgetGateway(t *testing.T) *budgetGateway {
	t.Helper()
	var calls atomic.Int64
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		if r.Method != http.MethodPost || !strings.HasSuffix(r.URL.Path, "/chat/completions") {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		var body struct {
			Model string `json:"model"`
		}
		_ = json.Unmarshal(b, &body)
		calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"id":"chatcmpl-console","object":"chat.completion","created":1,"model":"`+body.Model+`","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":1,"total_tokens":4}}`)
	}))
	t.Cleanup(up.Close)

	off := false
	cfg := config.DefaultConfig()
	cfg.Admin.Token = consoleAdmin
	cfg.Auth.Enabled = true
	cfg.Providers["alpha"] = config.ProviderConfig{BaseURL: up.URL + "/v1", APIKey: "node-alpha-key", APIFormat: "openai", Models: []string{"org-model"}, AutoDiscover: &off}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	tenants := tenant.NewStore()
	// $1 per token for org-model: an answer (3 + 1 tokens) costs $4.
	pricing := budgetplugin.NewPricingLookup(map[string]config.CustomPricing{"org-model": {InputPerMTok: 1_000_000, OutputPerMTok: 1_000_000}})
	bp := budgetplugin.New(budgetpkg.NewTracker(config.BudgetsConfig{Enabled: true}), pricing, true, tenants)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true), bp), keyStore, nil, nil, nil, testModelDBPtr(), tenants, bp.InvalidateOrg)
	srv.ready.Store(true)
	g := &budgetGateway{srv: srv, calls: &calls}

	syncBody := `{"keys":[{"id":"key_console_customer","name":"console key","owner":"member@example.test","key_hash":"` + auth.HashKey(consoleCust) + `","key_prefix":"sk-agentcc-con...","metadata":{"org_id":"` + consoleOrg + `"}}]}`
	if code, body := g.admin(t, http.MethodPost, "/-/keys/sync", syncBody); code != http.StatusOK {
		t.Fatalf("key sync -> %d %s", code, body)
	}
	return g
}

func (g *budgetGateway) admin(t *testing.T, method, path, body string) (int, string) {
	t.Helper()
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+consoleAdmin)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w.Code, strings.TrimSpace(w.Body.String())
}

func (g *budgetGateway) push(t *testing.T, budgets string) {
	t.Helper()
	if code, body := g.admin(t, http.MethodPut, "/-/orgs/"+consoleOrg+"/config", `{`+consoleProviders+`,"budgets":`+budgets+`}`); code != http.StatusOK {
		t.Fatalf("PUT org config -> %d %s", code, body)
	}
}

type budgetAnswer struct {
	code       int
	errCode    string
	retryAfter string
}

func (g *budgetGateway) chat(t *testing.T, hdr map[string]string) budgetAnswer {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(`{"model":"org-model","messages":[{"role":"user","content":"hello"}]}`))
	req.Header.Set("Authorization", "Bearer "+consoleCust)
	req.Header.Set("Content-Type", "application/json")
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	var body struct {
		Error struct {
			Code string `json:"code"`
		} `json:"error"`
	}
	_ = json.Unmarshal(w.Body.Bytes(), &body)
	return budgetAnswer{code: w.Code, errCode: body.Error.Code, retryAfter: w.Header().Get("Retry-After")}
}

func budgetCodes(as []budgetAnswer) []string {
	out := make([]string, len(as))
	for i, a := range as {
		out[i] = http.StatusText(a.code)
		if a.errCode != "" {
			out[i] += " " + a.errCode
		}
	}
	return out
}

func TestBudgetConsoleThroughServer(t *testing.T) {
	t.Run("Set Budget, Block: past the cap 429 org_budget_exceeded", func(t *testing.T) {
		g := startBudgetGateway(t)
		g.push(t, budgetFixture(t, "org_block"))
		var got []budgetAnswer
		for i := 0; i < 3; i++ {
			got = append(got, g.chat(t, nil))
		}
		if got[0].code != 200 || got[1].errCode != "org_budget_exceeded" || got[2].errCode != "org_budget_exceeded" {
			t.Fatalf("got %v, want [OK, 429 org_budget_exceeded x2]", budgetCodes(got))
		}
		if n := g.calls.Load(); n != 1 {
			t.Fatalf("upstream called %d times, want 1", n)
		}
	})

	t.Run("another save of the org config keeps the period's spend", func(t *testing.T) {
		g := startBudgetGateway(t)
		g.push(t, budgetFixture(t, "org_block"))
		if a := g.chat(t, nil); a.code != 200 {
			t.Fatalf("first request: %v", budgetCodes([]budgetAnswer{a}))
		}
		g.push(t, budgetFixture(t, "org_block"))
		if a := g.chat(t, nil); a.errCode != "org_budget_exceeded" {
			t.Fatalf("after a second push the $4 spent was forgotten: %v", budgetCodes([]budgetAnswer{a}))
		}
	})

	t.Run("spend before Set Budget counts: $8 spent, then a $5 Block budget refuses", func(t *testing.T) {
		g := startBudgetGateway(t)
		if code, body := g.admin(t, http.MethodPut, "/-/orgs/"+consoleOrg+"/config", `{`+consoleProviders+`}`); code != http.StatusOK {
			t.Fatalf("PUT org config without budgets -> %d %s", code, body)
		}
		for i := 0; i < 2; i++ {
			if a := g.chat(t, nil); a.code != 200 {
				t.Fatalf("request %d before any budget: %v", i+1, budgetCodes([]budgetAnswer{a}))
			}
		}
		g.push(t, `{"action":"block","enabled":true,"hard_limit":true,"org_limit":5,"warn_threshold":0.8}`)
		if a := g.chat(t, nil); a.errCode != "org_budget_exceeded" {
			t.Fatalf("$8 spent before Set Budget $5 Block: %v, want 429 org_budget_exceeded", budgetCodes([]budgetAnswer{a}))
		}
		if n := g.calls.Load(); n != 2 {
			t.Fatalf("upstream called %d times, want 2", n)
		}
	})

	t.Run("an explicit off stays off", func(t *testing.T) {
		g := startBudgetGateway(t)
		g.push(t, budgetFixture(t, "explicit_off"))
		for i := 0; i < 3; i++ {
			if a := g.chat(t, nil); a.code != 200 {
				t.Fatalf("request %d with budgets off: %v", i+1, budgetCodes([]budgetAnswer{a}))
			}
		}
	})

	t.Run("Set Budget, Throttle: throttle_rpm a minute past the cap, then 429 with Retry-After", func(t *testing.T) {
		for attempt := 0; attempt < 2; attempt++ {
			g := startBudgetGateway(t)
			g.push(t, budgetFixture(t, "org_throttle"))
			minute := time.Now().UTC().Truncate(time.Minute)
			var got []budgetAnswer
			for i := 0; i < 4; i++ {
				got = append(got, g.chat(t, nil))
			}
			if !time.Now().UTC().Truncate(time.Minute).Equal(minute) {
				continue // the window rolled over mid-sequence; run it again
			}
			if got[0].code != 200 || got[1].code != 200 || got[2].code != 200 || got[3].errCode != "budget_throttled" || got[3].code != 429 {
				t.Fatalf("got %v, want [OK OK OK, 429 budget_throttled]", budgetCodes(got))
			}
			if got[3].retryAfter == "" || got[3].retryAfter == "0" {
				t.Fatalf("throttled answer has Retry-After %q", got[3].retryAfter)
			}
			return
		}
		t.Fatal("two minute rollovers in a row")
	})

	t.Run("x-agentcc-user-id keys the per-user budget", func(t *testing.T) {
		g := startBudgetGateway(t)
		g.push(t, `{"users":{"user-42":{"limit":1e-06,"action":"block","hard":true}},"enabled":true}`)
		user42 := map[string]string{"x-agentcc-user-id": "user-42"}
		first, second := g.chat(t, user42), g.chat(t, user42)
		if first.code != 200 || second.errCode != "org_budget_exceeded" {
			t.Fatalf("user-42: got %v, want [OK, 429 org_budget_exceeded]", budgetCodes([]budgetAnswer{first, second}))
		}
		if a := g.chat(t, nil); a.code != 200 {
			t.Fatalf("the key's owner, naming no user, was refused for user-42's spend: %v", budgetCodes([]budgetAnswer{a}))
		}
		if a := g.chat(t, map[string]string{"x-agentcc-user-id": "user-7"}); a.code != 200 {
			t.Fatalf("user-7 was refused for user-42's spend: %v", budgetCodes([]budgetAnswer{a}))
		}
	})
}
