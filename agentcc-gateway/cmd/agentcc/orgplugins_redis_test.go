package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"net"
	"os"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// redisCommandCounter counts every command (and every command of a pipeline) a
// Redis client sends, and keeps each command's name and arguments.
type redisCommandCounter struct {
	n    atomic.Int64
	mu   sync.Mutex
	sent [][]any
}

func (c *redisCommandCounter) keep(cmd redis.Cmder) {
	c.mu.Lock()
	c.sent = append(c.sent, cmd.Args())
	c.mu.Unlock()
}

// since returns the commands sent after the first n.
func (c *redisCommandCounter) since(n int) [][]any {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([][]any(nil), c.sent[n:]...)
}

func (c *redisCommandCounter) kept() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.sent)
}

func (c *redisCommandCounter) DialHook(next redis.DialHook) redis.DialHook {
	return func(ctx context.Context, network, addr string) (net.Conn, error) { return next(ctx, network, addr) }
}

func (c *redisCommandCounter) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		c.n.Add(1)
		c.keep(cmd)
		return next(ctx, cmd)
	}
}

func (c *redisCommandCounter) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return func(ctx context.Context, cmds []redis.Cmder) error {
		c.n.Add(int64(len(cmds)))
		for _, cmd := range cmds {
			c.keep(cmd)
		}
		return next(ctx, cmds)
	}
}

// orgPluginsRedis is a client on the test Redis (TEST_REDIS_ADDR, TEST_REDIS_DB) and a
// key prefix of its own; every key under the prefix is deleted when the test
// ends (SCAN and DEL, never FLUSHDB).
func orgPluginsRedis(t *testing.T) (*redisstate.Client, string) {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		addr = "localhost:6379"
	}
	db, _ := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
	client, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: db, Timeout: time.Second})
	if err != nil {
		t.Skipf("skipping: Redis not available at %s: %v", addr, err)
	}
	var b [6]byte
	_, _ = rand.Read(b[:])
	prefix := "orgplug-" + hex.EncodeToString(b[:]) + ":"
	rdb := client.Redis()
	t.Cleanup(func() {
		ctx := context.Background()
		iter := rdb.Scan(ctx, 0, prefix+"*", 1000).Iterator()
		var keys []string
		for iter.Next(ctx) {
			keys = append(keys, iter.Val())
		}
		if len(keys) > 0 {
			rdb.Del(ctx, keys...)
		}
		client.Close()
	})
	return client, prefix
}

// TestOrgPluginsInstalledNoRedisForUnsetOrg: on a node whose switches are
// off, the org plugins main builds (ip_acl, budgets, tool_policy, alerting)
// send no Redis command for a request of an org that set none of those
// sections, but the organization-level spend count the budget plugin keeps
// for every organization (a budget set partway through a period counts the
// period's spend so far): each command is the spend script on that org's
// own org-level key. The same request for an org with budgets does reach
// Redis, which shows the counter sees the budget plugin's calls.
func TestOrgPluginsInstalledNoRedisForUnsetOrg(t *testing.T) {
	client, prefix := orgPluginsRedis(t)
	counter := &redisCommandCounter{}
	client.Redis().AddHook(counter)

	cfg := config.DefaultConfig()
	cfg.Admin.Token = testAdminToken                       // org configs can arrive through the admin API
	cfg.Server.TrustedProxies = []string{"192.0.2.255/32"} // the node resolves client IPs
	tenants := tenant.NewStore()
	tenants.Set("org-nothing", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{}})
	tenants.Set("org-spend", &tenant.OrgConfig{Budgets: &tenant.BudgetsConfig{Enabled: true, OrgLimit: 100, HardLimit: true}})

	var plugins []pipeline.Plugin
	if p := buildIPACLPlugin(cfg, tenants); p != nil {
		plugins = append(plugins, p)
	} else {
		t.Fatal("ip_acl plugin not built on a node org configs can reach")
	}
	budget, tracker := buildBudgetPlugin(cfg, tenants, redisstate.NewBudgetStore(client, prefix+"budget:"))
	if budget == nil || tracker != nil {
		t.Fatalf("budget plugin %v, node tracker %v: want a plugin and no node tracker (node budgets off)", budget, tracker)
	}
	plugins = append(plugins, budget)
	if p := buildToolPolicyPlugin(cfg, tenants); p != nil {
		plugins = append(plugins, p)
	} else {
		t.Fatal("tool_policy plugin not built")
	}
	alerting, err := buildAlertingPlugin(cfg, tenants)
	if err != nil || alerting == nil {
		t.Fatalf("alerting plugin %v, %v", alerting, err)
	}
	plugins = append(plugins, alerting)
	engine := pipeline.NewEngine(plugins...)

	serve := func(org string) {
		t.Helper()
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.Metadata[tenant.MetadataKeyOrgID] = org
		rc.Metadata["client_ip"] = "192.0.2.1"
		rc.Model = "gpt-4o"
		rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
		err := engine.Process(context.Background(), rc, func(_ context.Context, rc *models.RequestContext) error {
			rc.Response = &models.ChatCompletionResponse{Model: "gpt-4o", Usage: &models.Usage{PromptTokens: 10, CompletionTokens: 10, TotalTokens: 20}}
			rc.Metadata["cost"] = "0.01"
			return nil
		})
		if err != nil {
			t.Fatalf("org %s: %v", org, err)
		}
	}

	before, kept := counter.n.Load(), counter.kept()
	serve("org-nothing")
	orgLevel := prefix + "budget:org-nothing:org:"
	sent := counter.since(kept)
	if got := counter.n.Load() - before; got != int64(len(sent)) || got == 0 || got > 2 {
		t.Errorf("an org that set no ip_acl, budgets, tool_policy or alerting sent %d Redis command(s) %v; want only the org-level spend count (EVALSHA, or EVAL after NOSCRIPT)", got, sent)
	}
	for _, args := range sent {
		var name, key string
		if len(args) >= 4 {
			name, _ = args[0].(string)
			key, _ = args[3].(string)
		}
		if (name != "evalsha" && name != "eval") || !strings.HasPrefix(key, orgLevel) {
			t.Errorf("an org that set no ip_acl, budgets, tool_policy or alerting sent %v; want only the spend script on %s<period>", args, orgLevel)
		}
	}
	before = counter.n.Load()
	serve("org-spend")
	if got := counter.n.Load() - before; got == 0 {
		t.Errorf("an org with budgets sent no Redis command: the counter does not see the budget plugin's calls")
	}
}
