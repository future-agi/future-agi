package models

import "testing"

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
