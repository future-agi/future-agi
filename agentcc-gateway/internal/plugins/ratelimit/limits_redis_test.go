package ratelimit

// The org's limits are shared by every replica through the
// redisstate limiter. Two plugins with their own local limiters and their own
// Redis clients stand in for two gateway replicas.

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// limitsRedisClient is a client on the test Redis (TEST_REDIS_ADDR, DB
// TEST_REDIS_DB). Without TEST_REDIS_ADDR the test is skipped; with it, an
// unreachable Redis fails the test rather than skipping it.
func limitsRedisClient(t *testing.T) *redisstate.Client {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("set TEST_REDIS_ADDR (and TEST_REDIS_DB) to run the Redis-backed rate limit tests")
	}
	db, err := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
	if err != nil && os.Getenv("TEST_REDIS_DB") != "" {
		t.Fatalf("TEST_REDIS_DB=%q: %v", os.Getenv("TEST_REDIS_DB"), err)
	}
	c, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: db, Timeout: 2 * time.Second})
	if err != nil {
		t.Fatalf("TEST_REDIS_ADDR=%s is set but Redis is not reachable: %v", addr, err)
	}
	t.Cleanup(func() { _ = c.Close() })
	return c
}

// limitsRedisPrefix is a key prefix of this test's own; every key under it is
// deleted when the test ends (never FLUSHDB).
func limitsRedisPrefix(t *testing.T, c *redisstate.Client) string {
	t.Helper()
	prefix := fmt.Sprintf("test:orglimits:%s:%d:", t.Name(), time.Now().UnixNano())
	t.Cleanup(func() {
		ctx := context.Background()
		iter := c.Redis().Scan(ctx, 0, prefix+"*", 200).Iterator()
		for iter.Next(ctx) {
			c.Redis().Del(ctx, iter.Val())
		}
	})
	return prefix
}

func limitsRedisKeys(t *testing.T, c *redisstate.Client, prefix string) []string {
	t.Helper()
	ctx := context.Background()
	var keys []string
	iter := c.Redis().Scan(ctx, 0, prefix+"*", 200).Iterator()
	for iter.Next(ctx) {
		keys = append(keys, iter.Val())
	}
	return keys
}

// newReplica is one gateway replica's rate limit plugin on the shared Redis.
func newReplica(t *testing.T, prefix string) *Plugin {
	t.Helper()
	c := limitsRedisClient(t)
	local := NewRateLimiter()
	t.Cleanup(local.Stop)
	return New(config.RateLimitConfig{Enabled: true}, nil, redisstate.NewRateLimiter(c, local, prefix))
}

func TestOrgWideRPMAcrossReplicas(t *testing.T) {
	c := limitsRedisClient(t)
	prefix := limitsRedisPrefix(t, c)
	a, b := newReplica(t, prefix), newReplica(t, prefix)
	ctx := context.Background()
	mk := func(org, key string) *models.RequestContext {
		rc := orgLimitRC(org, key, "m-chat")
		rc.Metadata["org_rate_limit_rpm"] = "1"
		return rc
	}
	wantAllowed(t, "replica A, org-a key1", a.ProcessRequest(ctx, mk("org-a", "k1")))
	wantRefused(t, "replica B, org-a key2", b.ProcessRequest(ctx, mk("org-a", "k2")), "global_rpm")
	wantAllowed(t, "replica B, org-b key9", b.ProcessRequest(ctx, mk("org-b", "k9")))

	// Every org counter's Redis key names its org.
	var orgKeys []string
	for _, k := range limitsRedisKeys(t, c, prefix) {
		if strings.Contains(k, "org:") {
			orgKeys = append(orgKeys, k)
		}
	}
	if len(orgKeys) == 0 {
		t.Fatalf("no org counter in Redis under %s: %v", prefix, limitsRedisKeys(t, c, prefix))
	}
	for _, k := range orgKeys {
		if !strings.Contains(k, "org-a") && !strings.Contains(k, "org-b") {
			t.Errorf("Redis key %q does not name its org", k)
		}
	}
}

func TestOrgTPMAcrossReplicas(t *testing.T) {
	c := limitsRedisClient(t)
	prefix := limitsRedisPrefix(t, c)
	a, b := newReplica(t, prefix), newReplica(t, prefix)
	ctx := context.Background()
	mk := func(org, key string) *models.RequestContext {
		rc := orgLimitRC(org, key, "m-chat")
		rc.Metadata["org_rate_limit_tpm"] = "1000"
		return rc
	}
	rc1 := mk("org-a", "k1")
	wantAllowed(t, "replica A, first call", a.ProcessRequest(ctx, rc1))
	rc1.Response = usage(2000)
	a.ProcessResponse(ctx, rc1)
	wantRefused(t, "replica B, org-a", b.ProcessRequest(ctx, mk("org-a", "k2")), "global_tpm")
	wantAllowed(t, "replica B, org-b", b.ProcessRequest(ctx, mk("org-b", "k9")))
}

func TestPerUserRPMAcrossReplicasHashesLongIDs(t *testing.T) {
	c := limitsRedisClient(t)
	prefix := limitsRedisPrefix(t, c)
	a, b := newReplica(t, prefix), newReplica(t, prefix)
	ctx := context.Background()
	long := strings.Repeat("x", 200)
	mk := func(user string) *models.RequestContext {
		rc := orgLimitRC("org-a", "k1", "m-chat")
		rc.Metadata["org_rate_limit_per_user_rpm"] = "1"
		rc.UserID = user
		return rc
	}
	wantAllowed(t, "replica A, long user", a.ProcessRequest(ctx, mk(long)))
	wantRefused(t, "replica B, same long user", b.ProcessRequest(ctx, mk(long)), "per_user_rpm")
	for _, k := range limitsRedisKeys(t, c, prefix) {
		if strings.Contains(k, long[:65]) {
			t.Errorf("Redis key carries the raw user id over 64 bytes: %q", k)
		}
	}
}

// A request the gateway reads no estimate from (a pass-through /v1/messages,
// /v1beta or /v1/responses call: rc.Request is nil) still counts its settled
// usage on Redis: the hold creates its window's counter even at cost 0.
func TestOrgTPMAcrossReplicasPassThroughUsage(t *testing.T) {
	c := limitsRedisClient(t)
	prefix := limitsRedisPrefix(t, c)
	a, b := newReplica(t, prefix), newReplica(t, prefix)
	ctx := context.Background()
	mk := func(key string) *models.RequestContext {
		rc := orgLimitRC("org-a", key, "claude-x")
		rc.Request = nil // pass-through: no chat request to estimate from
		rc.Metadata["org_rate_limit_tpm"] = "1000"
		return rc
	}
	rc1 := mk("k1")
	wantAllowed(t, "replica A, pass-through call", a.ProcessRequest(ctx, rc1))
	found := false
	for _, k := range limitsRedisKeys(t, c, prefix) {
		if strings.Contains(k, ":tpm:") {
			found = true
		}
	}
	if !found {
		t.Errorf("no token counter in Redis after a zero-estimate hold: %v", limitsRedisKeys(t, c, prefix))
	}
	rc1.Response = usage(2000)
	a.ProcessResponse(ctx, rc1)
	wantRefused(t, "replica B, after 2000 pass-through tokens", b.ProcessRequest(ctx, mk("k2")), "global_tpm")
}
