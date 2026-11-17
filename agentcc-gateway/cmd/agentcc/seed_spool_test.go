package main

// The budget seed counts the spend still in the request-log spool (X) on top
// of the control plane's spend summary (S): main's test. After a restart that
// follows a lost batch, the seed is the full spend; with the control plane
// unreachable it is X; in Redis mode nothing is ever added to a counter, so
// restarts never compound.

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/logspool"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const seedOrg = "org-seed-spool"

var seedSpoolKey = logspool.MACKey("synthetic-seed-spool-key", "")

func seedRedis(t *testing.T) (*redisstate.Client, string) {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("TEST_REDIS_ADDR unset: a synthetic Redis is required")
	}
	db, _ := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
	c, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: db})
	if err != nil {
		t.Fatal(err)
	}
	var b [8]byte
	_, _ = rand.Read(b[:])
	prefix := "seedspool:" + hex.EncodeToString(b[:]) + ":"
	t.Cleanup(func() {
		ctx := context.Background()
		if keys, _ := c.Redis().Keys(ctx, prefix+"*").Result(); len(keys) > 0 {
			c.Redis().Del(ctx, keys...)
		}
		c.Close()
	})
	return c, prefix
}

// spoolWithSpend spools one batch holding a request of cost for seedOrg.
func spoolWithSpend(t *testing.T, c *redisstate.Client, prefix string, requestID string, cost float64) *logspool.Store {
	t.Helper()
	store := logspool.NewStore(c, prefix, "monthly", 0)
	entry, _ := json.Marshal(map[string]any{
		"request_id": requestID, "timestamp": time.Now().UTC(), "model": "m-1", "cost": cost,
		"auth_key_id": "key-1", "metadata": map[string]string{"key_org_id": seedOrg},
	})
	b, _, err := logspool.NewBatch(seedSpoolKey, time.Now(), []json.RawMessage{entry}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.Put(context.Background(), b); err != nil {
		t.Fatal(err)
	}
	return store
}

// controlPlane answers the spend summary with orgSpend for seedOrg, or 503.
func controlPlane(t *testing.T, up bool, orgSpend float64) string {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !up {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		fmt.Fprintf(w, `{"status":true,"result":{"period":"monthly","period_start":%q,"orgs":{%q:{"total_spend":%v,"per_key":{},"per_user":{},"per_model":{}}}}}`,
			logspool.PeriodStart("monthly", time.Now()).Format(time.RFC3339), seedOrg, orgSpend)
	}))
	t.Cleanup(srv.Close)
	return srv.URL
}

func seedConfig(url string) *config.Config {
	cfg := config.DefaultConfig()
	cfg.ControlPlane.URL = url
	cfg.Budgets.Enabled = true
	cfg.Budgets.DefaultPeriod = "monthly"
	cfg.Budgets.Org = &config.BudgetLevelConfig{Limit: 1000}
	return cfg
}

func TestSeedAfterALostBatchIsTheFullSpend(t *testing.T) {
	c, prefix := seedRedis(t)
	store := spoolWithSpend(t, c, prefix, "req-lost", 2.5)
	cfg := seedConfig(controlPlane(t, true, 10))
	tenants := tenant.NewStore()
	plugin, tracker := buildBudgetPlugin(cfg, tenants, nil)
	seedBudgetSpend(cfg, tenants, plugin, tracker, nil, store, seedSpoolKey)
	if got, _ := tracker.GetSpend("org", seedOrg); got != 12.5 {
		t.Fatalf("seeded org spend = %v, want 12.5 (the summary's 10 plus the spooled 2.5)", got)
	}
}

func TestSeedWithoutTheControlPlaneIsTheSpooledSpend(t *testing.T) {
	if testing.Short() {
		t.Skip("waits out the seed's retries")
	}
	c, prefix := seedRedis(t)
	store := spoolWithSpend(t, c, prefix, "req-lost", 2.5)
	cfg := seedConfig(controlPlane(t, false, 0))
	tenants := tenant.NewStore()
	plugin, tracker := buildBudgetPlugin(cfg, tenants, nil)
	seedBudgetSpend(cfg, tenants, plugin, tracker, nil, store, seedSpoolKey)
	if got, _ := tracker.GetSpend("org", seedOrg); got != 2.5 {
		t.Fatalf("seeded org spend = %v, want the spooled 2.5, not zero", got)
	}
}

// Redis mode: the counter already holds the spend (recorded at request time),
// and a seed sets a counter only when it is missing. Two restarts with the
// same batch still spooled leave it as it was.
func TestRedisCountersNeverCompoundAcrossRestarts(t *testing.T) {
	c, prefix := seedRedis(t)
	store := spoolWithSpend(t, c, prefix, "req-lost", 2.5)
	budget := redisstate.NewBudgetStore(c, prefix+"budget:")
	budget.SeedSpend(seedOrg, "org", "", "monthly", 9, nil) // what live traffic recorded
	cfg := seedConfig(controlPlane(t, true, 5))
	for restart := 0; restart < 2; restart++ {
		tenants := tenant.NewStore()
		plugin, tracker := buildBudgetPlugin(cfg, tenants, budget)
		seedBudgetSpend(cfg, tenants, plugin, tracker, budget, store, seedSpoolKey)
		if got, _, _ := budget.GetSpend(seedOrg, "org", "", "monthly"); got != 9 {
			t.Fatalf("restart %d: the Redis counter = %v, want 9 unchanged (S+X is 7.5; nothing added)", restart+1, got)
		}
	}
}
