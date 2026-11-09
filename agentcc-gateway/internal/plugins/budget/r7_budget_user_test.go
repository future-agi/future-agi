package budget

// R7-BUDGET (NG-26, contract K4): a per-user budget keys on the request's
// user id: the agent context's user id (x-agentcc-user-id or
// metadata.agent.user_id) when sent, else the body's user (rc.UserID), else
// the key's owner. The key owner's own user budget still applies when the
// request names another user, so a caller cannot step around it.

import (
	"context"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const r7Owner = "owner@example.test"

// r7UserRC is a request from a key owned by r7Owner, with the agent context's
// user id and the body's user as given ("" = not sent). The auth plugin sets
// rc.UserID to the owner when the body names none; this does the same.
func r7UserRC(agentUser, bodyUser string) *models.RequestContext {
	rc := r7RC(map[string]string{"auth_key_owner": r7Owner})
	rc.Agent.UserID = agentUser
	rc.UserID = bodyUser
	if rc.UserID == "" {
		rc.UserID = r7Owner
	}
	return rc
}

func r7UserSpend(p *Plugin, agentUser, bodyUser, cost string) {
	rc := r7UserRC(agentUser, bodyUser)
	rc.Metadata["cost"] = cost
	rc.Response = &models.ChatCompletionResponse{}
	p.ProcessResponse(context.Background(), rc)
}

func r7UserAsk(p *Plugin, agentUser, bodyUser string) (string, string) {
	rc := r7UserRC(agentUser, bodyUser)
	res := p.ProcessRequest(context.Background(), rc)
	return r7Code(res), rc.Metadata["budget_exceeded_level"]
}

func r7Users(names ...string) *tenant.BudgetsConfig {
	users := map[string]*tenant.BudgetLevelConfig{}
	for _, n := range names {
		users[n] = &tenant.BudgetLevelConfig{Limit: 0.000001}
	}
	return &tenant.BudgetsConfig{Enabled: true, Users: users}
}

func TestR7BudgetPerUserHeader(t *testing.T) {
	t.Run("x-agentcc-user-id is the request's user", func(t *testing.T) {
		p, _ := r7Plugin(t, r7Users("user-42"))
		r7UserSpend(p, "user-42", "", "1.00")
		if code, level := r7UserAsk(p, "user-42", ""); code != "org_budget_exceeded" || level != "user:user-42" {
			t.Fatalf("user-42 past its cap: got %s %q, want org_budget_exceeded user:user-42", code, level)
		}
		if code, _ := r7UserAsk(p, "user-7", ""); code != "allowed" {
			t.Fatalf("another user of the same key: got %s, want allowed", code)
		}
		if code, _ := r7UserAsk(p, "", ""); code != "allowed" {
			t.Fatalf("the same key naming no user: got %s, want allowed", code)
		}
	})
	t.Run("the agent context's user id wins over the body's user", func(t *testing.T) {
		p, _ := r7Plugin(t, r7Users("user-42", "body-9"))
		r7UserSpend(p, "user-42", "body-9", "1.00")
		if code, level := r7UserAsk(p, "user-42", "other"); code != "org_budget_exceeded" || level != "user:user-42" {
			t.Fatalf("got %s %q, want user:user-42 refused", code, level)
		}
		if code, _ := r7UserAsk(p, "", "body-9"); code != "allowed" {
			t.Fatalf("body-9 was charged for user-42's request: %s", code)
		}
	})
	t.Run("without an agent user id the body's user", func(t *testing.T) {
		p, _ := r7Plugin(t, r7Users("body-9"))
		r7UserSpend(p, "", "body-9", "1.00")
		if code, level := r7UserAsk(p, "", "body-9"); code != "org_budget_exceeded" || level != "user:body-9" {
			t.Fatalf("got %s %q, want user:body-9 refused", code, level)
		}
	})
	t.Run("naming no user, the key's owner (as before)", func(t *testing.T) {
		p, _ := r7Plugin(t, r7Users(r7Owner))
		r7UserSpend(p, "", "", "1.00")
		if code, level := r7UserAsk(p, "", ""); code != "org_budget_exceeded" || level != "user:"+r7Owner {
			t.Fatalf("got %s %q, want the owner's budget refused", code, level)
		}
	})
	t.Run("the owner's budget still applies with a foreign user id", func(t *testing.T) {
		p, _ := r7Plugin(t, r7Users(r7Owner))
		r7UserSpend(p, "someone-else", "", "1.00")
		if code, level := r7UserAsk(p, "anyone", ""); code != "org_budget_exceeded" || level != "user:"+r7Owner {
			t.Fatalf("a request naming another user stepped around the owner's budget: %s %q", code, level)
		}
	})
	t.Run("one request is charged once to each of its users", func(t *testing.T) {
		both := &tenant.BudgetsConfig{Enabled: true, Users: map[string]*tenant.BudgetLevelConfig{
			"user-42": {Limit: 10}, r7Owner: {Limit: 10},
		}}
		p, _ := r7Plugin(t, both)
		r7UserSpend(p, "user-42", "", "6.00")
		rc := r7UserRC("user-42", "")
		p.ProcessRequest(context.Background(), rc)
		if got := rc.Metadata["org_budget_remaining"]; got != "4.00" {
			t.Fatalf("remaining %q, want 4.00 (each level charged $6 once)", got)
		}
	})
	t.Run("a long user id keys a bounded Redis counter", func(t *testing.T) {
		long := "u-" + strings.Repeat("x", 80)
		fake := &r7FakeRedis{}
		p, _ := r7Plugin(t, r7Users(long))
		p.SetRedisBudget(fake)
		r7UserSpend(p, long, "", "1.00")
		if len(fake.keys) == 0 {
			t.Fatal("nothing written to Redis")
		}
		for _, k := range fake.keys {
			if len(k) > 64 {
				t.Fatalf("Redis key part %d bytes: %q", len(k), k)
			}
		}
		if code, level := r7UserAsk(p, long, ""); code != "org_budget_exceeded" || level != "user:"+long {
			t.Fatalf("long user id past its cap: %s %q", code, level)
		}
	})
}

// r7FakeRedis records the key parts the plugin writes and serves them back.
type r7FakeRedis struct {
	keys  []string
	spend map[string]float64
}

func (f *r7FakeRedis) RecordSpend(org, level, key, period, model string, cost float64) (float64, bool) {
	if f.spend == nil {
		f.spend = map[string]float64{}
	}
	f.keys = append(f.keys, key)
	f.spend[org+"|"+level+"|"+key+"|"+period] += cost
	return f.spend[org+"|"+level+"|"+key+"|"+period], true
}

func (f *r7FakeRedis) GetSpend(org, level, key, period string) (float64, map[string]float64, bool) {
	return f.spend[org+"|"+level+"|"+key+"|"+period], nil, true
}

func (f *r7FakeRedis) Available() bool { return true }
