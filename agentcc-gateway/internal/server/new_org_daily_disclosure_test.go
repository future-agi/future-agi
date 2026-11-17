package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"reflect"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/plugins/spendguard"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// The real HTTP pipeline runs the spend guard before credit reservation. Even
// when a minute cap also fails, the actual daily refusal must not be disguised
// as velocity. A prior charged request makes unchanged-ledger assertions useful.
func TestNewOrgDailyDisclosureNoCharge(t *testing.T) {
	for _, minute := range []float64{357, 1} {
		t.Run(fmt.Sprint(minute), func(t *testing.T) {
			prefix := fmt.Sprintf("test-new-org-disclosure-%d:", time.Now().UnixNano())
			client := r7AgentRedis(t, prefix)
			store := redisstate.NewSpendGuardStore(client, prefix)
			defaults := config.DefaultConfig().Managed.SpendGuard
			if defaults.NewOrgDailyCapUSD != 25 {
				t.Fatalf("launch daily default changed: %+v", defaults)
			}
			defaults.MaxUSDPerMinute, defaults.MaxUSDPerHour = minute, 3570
			guard := spendguard.NewGuard(store, defaults)
			estimator := &spendguard.Estimator{Settings: creditsplugin.DefaultSettings()}
			g := newManagedTestGatewayWith(t, managedOptIn(), managedGatewayOptions{
				extraPlugins: []pipeline.Plugin{spendguard.New(guard, estimator.Estimate)},
			})
			estimator.Prices = g.srv.handlers.Deps().Prices
			g.grant(t, "org-m", 1_000_000_000)
			control := g.chat(t, chatBody("anthropic/claude-sonnet-5.5"), nil)
			if control.Code != http.StatusOK || g.managed.calls() != 1 {
				t.Fatalf("actual mock not reached: %d %s", control.Code, control.Body)
			}
			// Pre-existing daily spend, admitted under a wider previous minute
			// policy; the daily gate must compare against REMAINING allowance.
			v, err := store.Admit(context.Background(), redisstate.SpendGuardAdmission{
				Org: "org-m", HoldID: "prior-daily-spend", EstimateMicros: 24_000_000,
				Limits: redisstate.SpendGuardLimits{NewOrgDailyMicros: 25_000_000}, Now: time.Now(),
			})
			if err != nil || v.Outcome != redisstate.SpendGuardAdmitted {
				t.Fatalf("seed daily spend: %+v %v", v, err)
			}
			guard.Settle(context.Background(), "org-m", "prior-daily-spend", 24_000_000)
			before, entries := g.account(t, "org-m"), g.outbox("org-m")
			if before.SpentMicros <= 0 || len(entries) == 0 {
				t.Fatal("control was not charged")
			}
			body := `{"model":"anthropic/claude-sonnet-5.5","max_tokens":100000,"messages":[{"role":"user","content":"hi"}]}`
			w := g.chat(t, body, nil)
			var got map[string]any
			if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
				t.Fatal(err)
			}
			want := map[string]any{"error": map[string]any{
				"type": "rate_limit_error", "code": "managed_spend_velocity", "param": nil,
				"message":  "New organizations have a daily spending limit for Future AGI credits. This request would exceed the remaining daily allowance. Contact support to raise the limit.",
				"metadata": map[string]any{"reason": "new_org_daily_cap"},
			}}
			if w.Code != http.StatusTooManyRequests || !reflect.DeepEqual(got, want) || w.Header().Get("Retry-After") != "" {
				t.Errorf("daily refusal must disclose limit and support, without values: status=%d body=%s headers=%v", w.Code, w.Body, w.Header())
			}
			after := g.account(t, "org-m")
			if !reflect.DeepEqual(entries, g.outbox("org-m")) || after.SpentMicros != before.SpentMicros || after.HeldMicros != before.HeldMicros {
				t.Fatalf("refused request changed charge ledger: before=%+v after=%+v entries=%v", before, after, g.outbox("org-m"))
			}
			if g.managed.calls() != 1 {
				t.Fatal("refusal reached provider")
			}
		})
	}
}
