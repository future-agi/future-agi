package models

import (
	"math"
	"testing"
)

func TestSpendMicrosNeverRecordsAPositiveCostAsZero(t *testing.T) {
	for _, tc := range []struct {
		usd  float64
		want int64
	}{
		{0, 0},
		{4e-7, 1},
		{5e-7, 1},
		{1e-12, 1},
		{1.4e-6, 1},
		{1.5e-6, 2},
		{0.003776, 3776},
		{-1e-6, -1},
	} {
		if got := SpendMicros(tc.usd); got != tc.want {
			t.Errorf("SpendMicros(%g) = %d, want %d", tc.usd, got, tc.want)
		}
	}
	if got := SpendUSD(4e-7); got != 1e-6 {
		t.Errorf("SpendUSD(4e-7) = %g, want 1e-6", got)
	}
}

// A cost outside the int64 micro-dollar range saturates; it never wraps into a
// negative amount (a refund) that would stop a budget from blocking.
func TestSpendMicrosSaturates(t *testing.T) {
	for _, tc := range []struct {
		usd  float64
		want int64
	}{
		{1e13, MaxSpendMicros},
		{9.3e12, MaxSpendMicros},
		{9.1e9, MaxSpendMicros},
		{math.Inf(1), MaxSpendMicros},
		{math.MaxFloat64, MaxSpendMicros},
		{-1e13, -MaxSpendMicros},
		{math.Inf(-1), -MaxSpendMicros},
		{math.NaN(), 0},
		{9e9, 9_000_000_000_000_000},
	} {
		if got := SpendMicros(tc.usd); got != tc.want {
			t.Errorf("SpendMicros(%g) = %d, want %d", tc.usd, got, tc.want)
		}
	}
	if got := SpendUSD(1e13); got <= 0 || got != float64(MaxSpendMicros)/1_000_000 {
		t.Errorf("SpendUSD(1e13) = %g, want the saturated, positive %g", got, float64(MaxSpendMicros)/1_000_000)
	}
	// A thousand saturated charges still add up without overflowing int64.
	if MaxSpendMicros > math.MaxInt64/1000 {
		t.Errorf("MaxSpendMicros %d leaves no room for 1000 charges", int64(MaxSpendMicros))
	}
	if got := SpendUSD(math.NaN()); got != 0 {
		t.Errorf("SpendUSD(NaN) = %g, want 0", got)
	}
}
