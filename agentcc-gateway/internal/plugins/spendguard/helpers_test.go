package spendguard

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

var bg = context.Background()

// t0 is a fixed instant 10 s into a minute.
var t0 = time.Date(2026, 10, 18, 9, 30, 10, 0, time.UTC)

// testRedis is a spend guard store on the test Redis (TEST_REDIS_ADDR,
// TEST_REDIS_DB) under the prefix test:G17:<test>:<nonce>:, whose keys are
// deleted when the test ends (SCAN and DEL, never FLUSHDB). It skips without
// Redis, and when TEST_REDIS_ADDR is unset: there is no default address (it
// was localhost:6379).
func testRedis(t *testing.T) (*redisstate.SpendGuardStore, redis.UniversalClient, string) {
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
	prefix := fmt.Sprintf("test:G17:%s:%d:", t.Name(), time.Now().UnixNano())
	rdb := client.Redis()
	t.Cleanup(func() {
		iter := rdb.Scan(bg, 0, prefix+"*", 1000).Iterator()
		var keys []string
		for iter.Next(bg) {
			keys = append(keys, iter.Val())
		}
		if len(keys) > 0 {
			rdb.Del(bg, keys...)
		}
		client.Close()
	})
	return redisstate.NewSpendGuardStore(client, prefix), rdb, prefix
}

// clock is a settable fake clock.
type clock struct {
	mu  sync.Mutex
	now time.Time
}

func (c *clock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

func (c *clock) Set(t time.Time) {
	c.mu.Lock()
	c.now = t
	c.mu.Unlock()
}

// orgs is a tenant config lookup over a fixed map.
func orgs(cfgs map[string]*tenant.OrgConfig) func(string) *tenant.OrgConfig {
	return func(org string) *tenant.OrgConfig { return cfgs[org] }
}

// guardWith is a Guard on store with the gateway defaults, the org configs
// and a fake clock at t0.
func guardWith(store *redisstate.SpendGuardStore, defaults config.SpendGuardConfig, cfgs map[string]*tenant.OrgConfig) (*Guard, *clock) {
	g := NewGuard(store, defaults)
	g.SetOrgConfig(orgs(cfgs))
	c := &clock{now: t0}
	g.SetClock(c.Now)
	return g, c
}

func usd(v float64) *float64 { return &v }

// managedRC is a managed chat request of org through key, held at the
// price card's worst case for "hi" with maxTokens of output.
func managedRC(org, key, requestID string, maxTokens int) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.RequestID = requestID
	rc.Provider = "openrouter"
	rc.Model = "vendor/model"
	rc.Request = &models.ChatCompletionRequest{Model: "vendor/model", MaxTokens: &maxTokens,
		Messages: []models.Message{{Role: "user", Content: rawString("hi")}}}
	rc.Metadata["billing_mode"] = models.BillingModeManaged
	rc.Metadata["org_id"] = org
	if key != "" {
		rc.Metadata["auth_key_id"] = key
	}
	return rc
}

func rawString(s string) []byte { return []byte(strconv.Quote(s)) }

func bucket(t *testing.T, rdb redis.UniversalClient, key string) int64 {
	t.Helper()
	n, err := rdb.Get(bg, key).Int64()
	if err != nil && err != redis.Nil {
		t.Fatalf("get %s: %v", key, err)
	}
	return n
}

func orgMinute(prefix, org string, at time.Time) string {
	return fmt.Sprintf("%ssg:{%s}:m:%d", prefix, org, at.UnixMilli()/60_000)
}

func keyMinute(prefix, org, key string, at time.Time) string {
	return fmt.Sprintf("%ssg:{%s}:k:%s:m:%d", prefix, org, key, at.UnixMilli()/60_000)
}
