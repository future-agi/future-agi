package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	outcomesplugin "github.com/futureagi/agentcc-gateway/internal/plugins/outcomes"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/routing/affinity"
	"github.com/futureagi/agentcc-gateway/internal/routing/outcomes"
)

// outcomeTestRedis is a client on the test Redis (TEST_REDIS_ADDR,
// TEST_REDIS_DB). It skips without Redis. Every key of org is deleted when
// the test ends (SCAN and DEL, never FLUSHDB). Every agent-signal key
// carries the hash tag {<org>}, so the pattern finds them under any prefix,
// and without one.
func outcomeTestRedis(t *testing.T, org string) *redisstate.Client {
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
	rdb := client.Redis()
	t.Cleanup(func() {
		ctx := context.Background()
		iter := rdb.Scan(ctx, 0, "*{"+org+"}*", 1000).Iterator()
		var keys []string
		for iter.Next(ctx) {
			keys = append(keys, iter.Val())
		}
		if len(keys) > 0 {
			rdb.Del(ctx, keys...)
		}
		client.Close()
	})
	return client
}

// Every agent-signal key (outcome counters, the Last record, the
// previous-request pointer and the affinity pin) carries the gateway's Redis
// key prefix: redis.prefix when set, else the "agentcc:" the budget, credit
// and rate-limit stores use. The Last record lives at most an hour.
func TestOutcomeSignalKeysCarryTheRedisPrefix(t *testing.T) {
	nonce := time.Now().UnixNano()
	for _, c := range []struct {
		name       string
		configured string // redis.prefix
		want       string // the prefix every key must carry
	}{
		{"configured", fmt.Sprintf("test:outcome-keys:%d:", nonce), fmt.Sprintf("test:outcome-keys:%d:", nonce)},
		{"default", "", "agentcc:"},
	} {
		t.Run(c.name, func(t *testing.T) {
			org := fmt.Sprintf("org-outcome-keys-%s-%d", c.name, nonce)
			client := outcomeTestRedis(t, org)
			rdb := client.Redis()
			cfg := config.DefaultConfig()
			cfg.Redis.Prefix = c.configured
			d := &wiringDeps{cfg: cfg, redis: client, catalogStore: catalog.NewStore()}
			if err := initOutcomes(d); err != nil {
				t.Fatalf("initOutcomes: %v", err)
			}
			var plugin *outcomesplugin.Plugin
			for _, p := range d.plugins {
				if op, ok := p.(*outcomesplugin.Plugin); ok {
					plugin = op
				}
			}
			if plugin == nil {
				t.Fatal("initOutcomes added no outcomes plugin")
			}

			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.RequestID = "req-1"
			rc.Metadata["org_id"] = org
			rc.Agent = models.AgentContext{AgentID: "researcher", RunID: "R1", SessionID: "S1", Step: models.StepAct,
				IdentitySource: models.IdentitySourceHeader}
			rc.AddAttempt(models.Attempt{Kind: models.AttemptPrimary, Provider: "openrouter", Model: "vendor/m",
				CredentialType: models.CredentialBYOK, Outcome: models.OutcomeSuccess, LatencyMs: 50})
			content, _ := json.Marshal("hi")
			rc.Response = &models.ChatCompletionResponse{
				Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: content}, FinishReason: "stop"}},
				Usage:   &models.Usage{PromptTokens: 10, CompletionTokens: 2},
			}
			ctx := context.Background()
			plugin.ProcessResponse(ctx, rc)

			last := outcomes.LastKey(org, "req-1")
			for _, key := range []string{
				last,
				outcomes.PrevKey(org, "S1", affinity.ClassMain),
				affinity.Key(org, "S1", affinity.ClassMain),
				outcomes.Key{Org: org, Agent: "researcher", Step: models.StepAct, Model: "vendor/m"}.String(),
			} {
				if n := rdb.Exists(ctx, c.want+key).Val(); n != 1 {
					t.Errorf("%s is not in Redis under the prefix %q", key, c.want)
				}
				if n := rdb.Exists(ctx, key).Val(); n != 0 {
					t.Errorf("%s was written without the Redis key prefix", key)
				}
			}
			if pttl := rdb.PTTL(ctx, c.want+last).Val(); pttl <= 0 || pttl > time.Hour {
				t.Errorf("PTTL(%s) = %v, want within (0, 1h]", last, pttl)
			}
		})
	}
}
