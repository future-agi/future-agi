package billing

import (
	"math"
	"strings"
	"testing"
)

func TestSpendCapMicros(t *testing.T) {
	for _, tc := range []struct {
		name, problem string
		usd           float64
		want          int64
	}{
		{"zero", "", 0, 0}, {"negative_zero", "", math.Copysign(0, -1), 0},
		{"nan", "NaN", math.NaN(), 0}, {"inf", "infinite", math.Inf(1), 0},
		{"negative_inf", "infinite", math.Inf(-1), 0}, {"negative", "negative", -1, 0},
		{"tiny", "sub-micro", 1e-7, 0}, {"tiny4", "sub-micro", 4e-7, 0},
		{"half_micro", "", 5e-7, 1}, {"micro", "", 1e-6, 1}, {"cent", "", 0.01, 10000},
		{"large_finite", "", math.MaxFloat64, math.MaxInt64 / 2},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, problem := SpendCapMicros(tc.usd)
			if got != tc.want || (tc.problem == "" && problem != "") || !strings.Contains(problem, tc.problem) {
				t.Fatalf("got (%d, %q), want (%d, %q)", got, problem, tc.want, tc.problem)
			}
			if got != USDToMicros(tc.usd) {
				t.Fatal("cap conversion drifted from billing micros")
			}
		})
	}
}
