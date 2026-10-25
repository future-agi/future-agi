package main

// Through the real wiring: initAgentBudget installs per-run agent budgets
// the way main runs it (runWiring, then the afterServer work once the server
// is built), so x-agentcc-budget-usd, x-agentcc-max-cost-usd and
// x-agentcc-budget-remaining-usd are enforced on a real HTTP server.

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/server"
)

// agentBudgetTestRedis is a client on DB TEST_REDIS_DB of TEST_REDIS_ADDR, the
// only database this test writes; it skips without TEST_REDIS_ADDR. Every key
// under prefix is deleted when the test ends (SCAN and DEL, never FLUSHDB).
func agentBudgetTestRedis(t *testing.T, prefix string) *redisstate.Client {
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

func TestAgentBudgetWiring(t *testing.T) {
	prefix := fmt.Sprintf("test:agent-budget-wiring:%d:", time.Now().UnixNano())
	client := agentBudgetTestRedis(t, prefix)

	var calls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path != "/v1/chat/completions" {
			_, _ = w.Write([]byte(`{"object":"list","data":[]}`))
			return
		}
		calls.Add(1)
		_, _ = w.Write([]byte(`{"id":"x","object":"chat.completion","created":1,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":10,"total_tokens":20}}`))
	}))
	defer upstream.Close()

	cfg := config.DefaultConfig()
	cfg.Server.Host = "127.0.0.1"
	cfg.Server.Port = freePort(t)
	cfg.Redis.Prefix = prefix
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "rb", Key: "sk-rb-agent", Owner: "t", KeyType: "internal"}}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Routing.ProviderLock = config.ProviderLockConfig{Enabled: true}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(server.BuildModelDB(nil, catalog.NewStore()))

	d := &wiringDeps{cfg: cfg, registry: registry, redis: client, keyStore: keyStore, catalogStore: catalog.NewStore(),
		plugins: []pipeline.Plugin{authplugin.New(keyStore, true)}}
	d.seedInjected(mdb.Load)
	d.step = "agentbudget"
	if err := initAgentBudget(d); err != nil {
		t.Fatalf("initAgentBudget: %v", err)
	}
	engine := pipeline.NewEngine(d.plugins...)
	srv := server.New(cfg, "", registry, engine, keyStore, nil, nil, nil, &mdb, nil, nil)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	d.engine, d.server, d.handlers, d.ctx = engine, srv, srv.Handlers(), ctx
	if err := d.startServerWiring(); err != nil {
		t.Fatalf("startServerWiring: %v", err)
	}
	srv.Handlers().SetInjectedDeps(d.injected())

	served := make(chan error, 1)
	go func() { served <- srv.Start() }()
	defer func() {
		shutdownCtx, stop := context.WithTimeout(context.Background(), 5*time.Second)
		defer stop()
		_ = srv.Shutdown(shutdownCtx)
		<-served
	}()
	base := "http://" + cfg.Addr()
	for deadline := time.Now().Add(10 * time.Second); ; time.Sleep(20 * time.Millisecond) {
		if resp, err := http.Get(base + "/healthz"); err == nil {
			resp.Body.Close()
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("the gateway did not start")
		}
	}

	chat := func(headers ...string) (int, string, http.Header) {
		t.Helper()
		req, _ := http.NewRequest(http.MethodPost, base+"/v1/chat/completions",
			strings.NewReader(`{"model":"gpt-4o","max_tokens":2000,"messages":[{"role":"user","content":"hi"}]}`))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer sk-rb-agent")
		for i := 0; i+1 < len(headers); i += 2 {
			req.Header.Set(headers[i], headers[i+1])
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatalf("chat: %v", err)
		}
		defer resp.Body.Close()
		b, _ := io.ReadAll(resp.Body)
		var e struct {
			Error struct {
				Code string `json:"code"`
			} `json:"error"`
		}
		_ = json.Unmarshal(b, &e)
		return resp.StatusCode, e.Error.Code, resp.Header
	}

	// Control: a call with no agent headers is served.
	if code, errCode, _ := chat(); code != http.StatusOK {
		t.Fatalf("a call with no agent headers: HTTP %d %s", code, errCode)
	}
	for _, c := range []struct {
		name    string
		headers []string
		status  int
		code    string
	}{
		{"a run budget smaller than the call's worst case", []string{"x-agentcc-run-id", "run-1", "x-agentcc-budget-usd", "0.000001"},
			http.StatusPaymentRequired, "agent_budget_exhausted"},
		{"a per-call cap no output fits under", []string{"x-agentcc-run-id", "run-2", "x-agentcc-max-cost-usd", "0.000001"},
			http.StatusPaymentRequired, "agent_call_cap_exceeded"},
		{"no budget left for the call", []string{"x-agentcc-run-id", "run-3", "x-agentcc-budget-remaining-usd", "0"},
			http.StatusPaymentRequired, "agent_call_cap_exceeded"},
		// Without a run id, a per-call cap and a budget each cover that call alone.
		{"a per-call cap with no run id", []string{"x-agentcc-max-cost-usd", "0.0000001"},
			http.StatusPaymentRequired, "agent_call_cap_exceeded"},
		{"a budget with no run id covers that call alone", []string{"x-agentcc-budget-usd", "0.0000001"},
			http.StatusPaymentRequired, "agent_budget_exhausted"},
	} {
		before := calls.Load()
		code, errCode, _ := chat(c.headers...)
		t.Logf("%s: HTTP %d %s", c.name, code, errCode)
		if code != c.status || errCode != c.code {
			t.Errorf("%s: HTTP %d %q, want %d %q", c.name, code, errCode, c.status, c.code)
		}
		if n := calls.Load() - before; n != 0 {
			t.Errorf("%s: the upstream was called %d times, want none", c.name, n)
		}
	}
	// A run whose budget holds the call is served and told what is left.
	code, errCode, hdr := chat("x-agentcc-run-id", "run-4", "x-agentcc-budget-usd", "5")
	if code != http.StatusOK || hdr.Get("x-agentcc-budget-remaining-usd") == "" {
		t.Errorf("a run with room: HTTP %d %s, x-agentcc-budget-remaining-usd %q", code, errCode, hdr.Get("x-agentcc-budget-remaining-usd"))
	}
	// Its scope is in Redis under the gateway's key prefix.
	if n, err := client.Redis().Exists(context.Background(), prefix+"abud:{_}:run-4:spent").Result(); err != nil || n != 1 {
		t.Errorf("the run's spent counter is not at %sabud:{_}:run-4:spent (EXISTS %d, %v)", prefix, n, err)
	}
}
