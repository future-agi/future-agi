package server

import (
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

func TestOwnHourRefusalNoChargeOrProvider(t *testing.T) {
	for _, minute := range []float64{0, 100} {
		t.Run(fmt.Sprint(minute), func(t *testing.T) {
			prefix := fmt.Sprintf("test-own-hour-%d:", time.Now().UnixNano())
			client := r7AgentRedis(t, prefix)
			guard := spendguard.NewGuard(redisstate.NewSpendGuardStore(client, prefix), config.SpendGuardConfig{MaxUSDPerMinute: minute, MaxUSDPerHour: 1})
			estimator := &spendguard.Estimator{Settings: creditsplugin.DefaultSettings()}
			g := newManagedTestGatewayWith(t, managedOptIn(), managedGatewayOptions{
				extraPlugins: []pipeline.Plugin{spendguard.New(guard, estimator.Estimate)},
			})
			estimator.Prices = g.srv.handlers.Deps().Prices
			g.grant(t, "org-m", 1_000_000_000)
			control := g.chat(t, chatBody("anthropic/claude-sonnet-5.5"), nil)
			if control.Code != http.StatusOK || g.managed.calls() != 1 {
				t.Fatalf("loopback control not reached: %d %s", control.Code, control.Body)
			}
			before, entries := g.account(t, "org-m"), g.outbox("org-m")
			if before.SpentMicros <= 0 || len(entries) == 0 {
				t.Fatal("control did not charge")
			}
			w := g.chat(t, `{"model":"anthropic/claude-sonnet-5.5","max_tokens":100000,"messages":[{"role":"user","content":"hi"}]}`, nil)
			var got map[string]any
			if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
				t.Fatal(err)
			}
			want := map[string]any{"error": map[string]any{
				"type": "rate_limit_error", "code": "managed_spend_velocity", "param": nil,
				"message":  "This request's worst-case cost exceeds the per-hour spending limit. Lower max_tokens to fit within the limit.",
				"metadata": map[string]any{"reason": "request_exceeds_hour_cap"},
			}}
			if w.Code != http.StatusTooManyRequests || !reflect.DeepEqual(got, want) || w.Header().Get("Retry-After") != "" {
				t.Errorf("own-hour contract: %d %s %v", w.Code, w.Body, w.Header())
			}
			after := g.account(t, "org-m")
			if !reflect.DeepEqual(entries, g.outbox("org-m")) || after.SpentMicros != before.SpentMicros || after.HeldMicros != before.HeldMicros || g.managed.calls() != 1 {
				t.Fatal("refusal changed credit ledger or reached provider")
			}
		})
	}
}
