package agentbudget

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"os"
	"strconv"
	"sync/atomic"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

var bg = context.Background()

const testOrg = "org-agentbudget"

// testStore is an AgentBudgetStore on the test Redis (TEST_REDIS_ADDR,
// TEST_REDIS_DB) under the prefix test:agentbudget:<test>:<nonce>:, whose keys
// are deleted when the test ends (SCAN and DEL, never FLUSHDB). It skips
// without TEST_REDIS_ADDR.
func testStore(t *testing.T) (*redisstate.AgentBudgetStore, redis.UniversalClient, string) {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("skipping: TEST_REDIS_ADDR is not set")
	}
	db, _ := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
	client, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: db, Timeout: time.Second})
	if err != nil {
		t.Skipf("skipping: Redis not available at %s: %v", addr, err)
	}
	prefix := fmt.Sprintf("test:agentbudget:%s:%d:", t.Name(), time.Now().UnixNano())
	rdb := client.Redis()
	t.Cleanup(func() {
		for _, k := range scanKeys(rdb, prefix) {
			rdb.Del(bg, k)
		}
		client.Close()
	})
	return redisstate.NewAgentBudgetStore(client, prefix), rdb, prefix
}

func scanKeys(rdb redis.UniversalClient, prefix string) []string {
	var keys []string
	iter := rdb.Scan(bg, 0, prefix+"*", 1000).Iterator()
	for iter.Next(bg) {
		keys = append(keys, iter.Val())
	}
	return keys
}

// Cards the tests price calls on, per million tokens and per request.
var (
	// cardFee10k holds every call at exactly $0.01: a request fee only.
	cardFee10k = models.PriceCard{Request: 0.01}
	// cardTokens is $1 per million prompt tokens and $10 per million
	// completion tokens.
	cardTokens = models.PriceCard{Prompt: 1e-6, Completion: 1e-5}
)

// testPlugin is the plugin over store (nil for none), pricing model "m" on
// provider "p" at card and leaving every other model unpriced; cfg is every
// org's stored config.
func testPlugin(store *redisstate.AgentBudgetStore, card models.PriceCard, cfg *tenant.OrgConfig) *Plugin {
	return New(Deps{
		Store:    store,
		Settings: Settings{MarkupPercent: 5, HoldTTL: time.Minute, ReserveDefaultOutputTokens: 4096},
		Prices: func(providerID, model, _ string) (models.PriceCard, bool) {
			return card, providerID == "p" && model == "m"
		},
		OrgConfig: func(string) *tenant.OrgConfig { return cfg },
	})
}

// callSeq makes every test call's request id unique, so no two calls share
// a hold id by accident.
var callSeq atomic.Int64

// call is one chat call's request context: model "m" on provider "p",
// BYOK, in org testOrg, asking for maxTokens (0 for none). The test releases
// it, which runs its finalizers.
func call(t *testing.T, maxTokens int) *models.RequestContext {
	t.Helper()
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.RequestID = fmt.Sprintf("req-%s-%d", t.Name(), callSeq.Add(1))
	rc.Metadata["org_id"] = testOrg
	rc.Metadata["billing_mode"] = models.BillingModeBYOK
	rc.Provider, rc.Model = "p", "m"
	req := &models.ChatCompletionRequest{Model: "m", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}}}
	if maxTokens > 0 {
		req.MaxTokens = &maxTokens
	}
	rc.Request = req
	return rc
}

// inRun puts rc in run scope scope with budget usd (negative for none).
func inRun(rc *models.RequestContext, scope string, usd float64) *models.RequestContext {
	rc.Agent.BudgetScope = scope
	if usd >= 0 {
		rc.Agent.BudgetUSD = &usd
	}
	return rc
}

// admit runs a chat call's run-budget steps up to the dispatch: the hook,
// then the reserve. It returns the refusal, or nil.
func admit(p *Plugin, rc *models.RequestContext, cfg *tenant.OrgConfig) *models.APIError {
	if apiErr := p.CallCap(rc, cfg); apiErr != nil {
		return apiErr
	}
	return p.ProcessRequest(bg, rc).Error
}

func peek(t *testing.T, store *redisstate.AgentBudgetStore, scope string) redisstate.AgentBudgetState {
	t.Helper()
	st, err := store.Peek(bg, testOrg, scope)
	if err != nil {
		t.Fatal(err)
	}
	return st
}

func header(rc *models.RequestContext, name string) string {
	for _, h := range rc.ResponseHeaders() {
		if h.Name == name {
			return h.Value
		}
	}
	return ""
}

// body is err as the client receives it.
func body(t *testing.T, err *models.APIError) map[string]any {
	t.Helper()
	w := httptest.NewRecorder()
	models.WriteError(w, err)
	if w.Code != err.Status {
		t.Fatalf("status %d, want %d", w.Code, err.Status)
	}
	var out struct {
		Error map[string]any `json:"error"`
	}
	if e := json.Unmarshal(w.Body.Bytes(), &out); e != nil {
		t.Fatalf("decoding %s: %v", w.Body.Bytes(), e)
	}
	return out.Error
}

func wantRefusal(t *testing.T, err *models.APIError, status int, code, limitSource string) map[string]any {
	t.Helper()
	if err == nil {
		t.Fatalf("admitted, want %d %s", status, code)
	}
	if err.Status != status || err.Code != code {
		t.Fatalf("got %d %s (%s), want %d %s", err.Status, err.Code, err.Message, status, code)
	}
	b := body(t, err)
	meta, _ := b["metadata"].(map[string]any)
	if limitSource != "" && meta["limit_source"] != limitSource {
		t.Fatalf("limit_source %v, want %s", meta["limit_source"], limitSource)
	}
	return meta
}
