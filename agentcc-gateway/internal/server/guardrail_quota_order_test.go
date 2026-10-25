package server

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// The request quota (an org's free-tier request limit, and the pause set on
// an org whose budget limit is reached) refuses a request before the checks
// on the request read it. A refused request is not served, so the checks
// have nothing to protect, and they do no work for it: no external or
// FutureAGI check call is made for it, and a request both paused and
// blocked gets the 429 that says why the org is refused. The quota reads
// none of the request's content and has nothing a check needs.

// quotaValues answers the quota plugin's Redis reads from a map, without a
// server: a key it does not hold reads as missing (redis.Nil).
type quotaValues map[string]string

func (v quotaValues) DialHook(next redis.DialHook) redis.DialHook { return next }

func (v quotaValues) ProcessHook(redis.ProcessHook) redis.ProcessHook {
	return func(_ context.Context, cmd redis.Cmder) error {
		v.answer(cmd)
		return cmd.Err()
	}
}

func (v quotaValues) ProcessPipelineHook(redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return func(_ context.Context, cmds []redis.Cmder) error {
		for _, cmd := range cmds {
			v.answer(cmd)
		}
		return nil
	}
}

func (v quotaValues) answer(cmd redis.Cmder) {
	sc, ok := cmd.(*redis.StringCmd)
	if !ok || cmd.Name() != "get" || len(cmd.Args()) != 2 {
		cmd.SetErr(errors.New("quotaValues answers GET only"))
		return
	}
	if val, ok := v[fmt.Sprint(cmd.Args()[1])]; ok {
		sc.SetVal(val)
		return
	}
	sc.SetErr(redis.Nil)
}

// newQuotaRedis is a Redis client whose reads quotaValues answers; it never
// dials (no address it could reach is given, and every command is answered
// by the hook).
func newQuotaRedis(t *testing.T, values quotaValues) *redis.Client {
	t.Helper()
	c := redis.NewClient(&redis.Options{Addr: "127.0.0.1:1", MaxRetries: -1})
	c.AddHook(values)
	t.Cleanup(func() { _ = c.Close() })
	return c
}

func TestQuota_ARefusedOrgIsRefusedBeforeTheChecksReadTheRequest(t *testing.T) {
	period := time.Now().UTC().Format("2006-01")
	const benign = "What time does the cafeteria open on Mondays?"
	for _, quota := range []struct {
		name   string
		values quotaValues
		code   string
	}{
		{"paused (budget limit reached)", quotaValues{"pause:" + cacheOrderOrg + ":gateway_requests": "1"}, "usage_paused"},
		{"over the free-tier request limit", quotaValues{
			"quota:" + cacheOrderOrg + ":gateway_requests":           "10",
			"usage:" + cacheOrderOrg + ":gateway_requests:" + period: "10",
		}, "quota_exceeded"},
	} {
		for _, shape := range []struct {
			name  string
			setup cacheOrderSetup
		}{
			{"the org's check", cacheOrderSetup{checks: cacheOrderCheck}},
			{"a rule in the config file", cacheOrderSetup{configRule: true}},
		} {
			for _, prompt := range []struct{ name, text string }{
				{"a request the check passes", benign},
				{"a request the check blocks", cacheOrderPrompt},
			} {
				t.Run(quota.name+"/"+shape.name+"/"+prompt.name, func(t *testing.T) {
					setup := shape.setup
					setup.quota = newQuotaRedis(t, quota.values)
					g := newCacheOrderGateway(t, setup)
					c := g.chat(t, cacheOrderKey, prompt.text, nil)
					if c.status != http.StatusTooManyRequests || !strings.Contains(c.body, quota.code) {
						t.Errorf("got %d %q, want the quota's 429 %s", c.status, c.body, quota.code)
					}
					if n := g.checked.Load(); n != 0 {
						t.Errorf("keyword-blocklist read the request %d times before the quota refused it", n)
					}
					if n := g.calls.Load(); n != 0 {
						t.Errorf("provider called %d times", n)
					}
				})
			}
		}
	}

	// Control: an org under its limit and not paused has every request read
	// by its checks; one they block gets their 403, one they pass is served.
	t.Run("an org the quota lets through", func(t *testing.T) {
		g := newCacheOrderGateway(t, cacheOrderSetup{checks: cacheOrderCheck, quota: newQuotaRedis(t, quotaValues{
			"quota:" + cacheOrderOrg + ":gateway_requests":           "10",
			"usage:" + cacheOrderOrg + ":gateway_requests:" + period: "9",
		})})
		if c := g.chat(t, cacheOrderKey, benign, nil); c.status != http.StatusOK || c.content != "upstream answer 1" {
			t.Errorf("a passing request: %+v", c)
		}
		g.assertBlockedNotServed(t, g.chat(t, cacheOrderKey, cacheOrderPrompt, nil), 1)
		if n := g.checked.Load(); n != 2 {
			t.Errorf("keyword-blocklist read %d requests, want 2", n)
		}
	})
}
