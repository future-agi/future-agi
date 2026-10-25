package budget

// A per-user budget keys on the request's
// user id: the agent context's user id (x-agentcc-user-id or
// metadata.agent.user_id) when sent, else the body's user (rc.UserID), else
// the key's owner. The key owner's own user budget also applies when the
// request names another user.

import (
	"context"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const btOwner = "owner@example.test"

// btUserRC is a request from a key owned by btOwner, with the agent context's
// user id and the body's user as given ("" = not sent). The auth plugin sets
// rc.UserID to the owner when the body names none; this does the same.
func btUserRC(agentUser, bodyUser string) *models.RequestContext {
	rc := btRC(map[string]string{"auth_key_owner": btOwner})
	rc.Agent.UserID = agentUser
	rc.UserID = bodyUser
	if rc.UserID == "" {
		rc.UserID = btOwner
	}
	return rc
}

func btUserSpend(p *Plugin, agentUser, bodyUser, cost string) {
	rc := btUserRC(agentUser, bodyUser)
	rc.Metadata["cost"] = cost
	rc.Response = &models.ChatCompletionResponse{}
	p.ProcessResponse(context.Background(), rc)
}

func btUserAsk(p *Plugin, agentUser, bodyUser string) (string, string) {
	rc := btUserRC(agentUser, bodyUser)
	res := p.ProcessRequest(context.Background(), rc)
	return btCode(res), rc.Metadata["budget_exceeded_level"]
}

func btUsers(names ...string) *tenant.BudgetsConfig {
	users := map[string]*tenant.BudgetLevelConfig{}
	for _, n := range names {
		users[n] = &tenant.BudgetLevelConfig{Limit: 0.000001}
	}
	return &tenant.BudgetsConfig{Enabled: true, Users: users}
}

func TestBudgetPerUserHeader(t *testing.T) {
	t.Run("x-agentcc-user-id is the request's user", func(t *testing.T) {
		p, _ := btPlugin(t, btUsers("user-42"))
		btUserSpend(p, "user-42", "", "1.00")
		if code, level := btUserAsk(p, "user-42", ""); code != "org_budget_exceeded" || level != "user:user-42" {
			t.Fatalf("user-42 past its cap: got %s %q, want org_budget_exceeded user:user-42", code, level)
		}
		if code, _ := btUserAsk(p, "user-7", ""); code != "allowed" {
			t.Fatalf("another user of the same key: got %s, want allowed", code)
		}
		if code, _ := btUserAsk(p, "", ""); code != "allowed" {
			t.Fatalf("the same key naming no user: got %s, want allowed", code)
		}
	})
	t.Run("the agent context's user id wins over the body's user", func(t *testing.T) {
		p, _ := btPlugin(t, btUsers("user-42", "body-9"))
		btUserSpend(p, "user-42", "body-9", "1.00")
		if code, level := btUserAsk(p, "user-42", "other"); code != "org_budget_exceeded" || level != "user:user-42" {
			t.Fatalf("got %s %q, want user:user-42 refused", code, level)
		}
		if code, _ := btUserAsk(p, "", "body-9"); code != "allowed" {
			t.Fatalf("body-9 was charged for user-42's request: %s", code)
		}
	})
	t.Run("without an agent user id the body's user", func(t *testing.T) {
		p, _ := btPlugin(t, btUsers("body-9"))
		btUserSpend(p, "", "body-9", "1.00")
		if code, level := btUserAsk(p, "", "body-9"); code != "org_budget_exceeded" || level != "user:body-9" {
			t.Fatalf("got %s %q, want user:body-9 refused", code, level)
		}
	})
	t.Run("naming no user, the key's owner", func(t *testing.T) {
		p, _ := btPlugin(t, btUsers(btOwner))
		btUserSpend(p, "", "", "1.00")
		if code, level := btUserAsk(p, "", ""); code != "org_budget_exceeded" || level != "user:"+btOwner {
			t.Fatalf("got %s %q, want the owner's budget refused", code, level)
		}
	})
	t.Run("the owner's budget applies with a foreign user id", func(t *testing.T) {
		p, _ := btPlugin(t, btUsers(btOwner))
		btUserSpend(p, "someone-else", "", "1.00")
		if code, level := btUserAsk(p, "anyone", ""); code != "org_budget_exceeded" || level != "user:"+btOwner {
			t.Fatalf("a request naming another user was not held to the owner's budget: %s %q", code, level)
		}
	})
	t.Run("one request is charged once to each of its users", func(t *testing.T) {
		both := &tenant.BudgetsConfig{Enabled: true, Users: map[string]*tenant.BudgetLevelConfig{
			"user-42": {Limit: 10}, btOwner: {Limit: 10},
		}}
		p, _ := btPlugin(t, both)
		btUserSpend(p, "user-42", "", "6.00")
		rc := btUserRC("user-42", "")
		p.ProcessRequest(context.Background(), rc)
		if got := rc.Metadata["org_budget_remaining"]; got != "4.00" {
			t.Fatalf("remaining %q, want 4.00 (each level charged $6 once)", got)
		}
	})
	t.Run("a long user id keys a bounded Redis counter", func(t *testing.T) {
		long := "u-" + strings.Repeat("x", 80)
		fake := &btFakeRedis{}
		p, _ := btPlugin(t, btUsers(long))
		p.SetRedisBudget(fake)
		btUserSpend(p, long, "", "1.00")
		if len(fake.keys) == 0 {
			t.Fatal("nothing written to Redis")
		}
		for _, k := range fake.keys {
			if len(k) > 64 {
				t.Fatalf("Redis key part %d bytes: %q", len(k), k)
			}
		}
		if code, level := btUserAsk(p, long, ""); code != "org_budget_exceeded" || level != "user:"+long {
			t.Fatalf("long user id past its cap: %s %q", code, level)
		}
	})
}

// btFakeRedis records the key parts the plugin writes and serves them back.
type btFakeRedis struct {
	keys  []string
	spend map[string]float64
}

func (f *btFakeRedis) RecordSpend(org, level, key, period, model string, cost float64) (float64, bool) {
	if f.spend == nil {
		f.spend = map[string]float64{}
	}
	f.keys = append(f.keys, key)
	f.spend[org+"|"+level+"|"+key+"|"+period] += cost
	return f.spend[org+"|"+level+"|"+key+"|"+period], true
}

func (f *btFakeRedis) GetSpend(org, level, key, period string) (float64, map[string]float64, bool) {
	return f.spend[org+"|"+level+"|"+key+"|"+period], nil, true
}

func (f *btFakeRedis) Available() bool { return true }
