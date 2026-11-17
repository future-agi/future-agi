package spendguard

import (
	"math"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Pin the actual values Redis receives: zero means no cap, not a zero budget.
func TestSpendCapRuntimeConversion(t *testing.T) {
	for _, tc := range []struct {
		name string
		usd  float64
		want int64
	}{
		{"zero", 0, 0}, {"nan", math.NaN(), 0}, {"inf", math.Inf(1), 0},
		{"negative_inf", math.Inf(-1), 0}, {"negative", -1, 0},
		{"tiny", 1e-7, 0}, {"tiny4", 4e-7, 0},
		{"half_micro", 5e-7, 1}, {"micro", 1e-6, 1}, {"cent", 0.01, 10000},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := NewGuard(nil, config.SpendGuardConfig{
				MaxUSDPerMinute: tc.usd, MaxUSDPerHour: tc.usd, NewOrgDailyCapUSD: tc.usd,
			})
			limits := g.settings("synthetic").limits
			if limits.MinuteMicros != tc.want || limits.HourMicros != tc.want || limits.NewOrgDailyMicros != tc.want {
				t.Fatalf("runtime caps = %+v, want %d micros", limits, tc.want)
			}
			if got := billing.USDToMicros(tc.usd); got != tc.want {
				t.Fatalf("billing conversion = %d, want %d", got, tc.want)
			}
		})
	}
}
