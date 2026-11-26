package ingestbench

import (
	"math"
	"testing"
)

func TestDistributionMeanExactIntegerBounds(t *testing.T) {
	for _, tc := range []struct {
		name   string
		lo, hi int64
		mean   float64
		valid  bool
	}{
		{"constant-rounds-below-min", 1<<53 + 1, 1<<53 + 1, float64(1<<53 + 1), false},
		{"constant-rounds-above-max", 1<<53 + 3, 1<<53 + 3, float64(1<<53 + 3), false},
		{"int64-upper-rounds-outside", 0, math.MaxInt64, float64(math.MaxInt64), false},
		{"last-representable-in-int64", 0, math.MaxInt64, math.Nextafter(float64(math.MaxInt64), 0), true},
		{"large-interior", 1 << 53, 1<<53 + 3, float64(1<<53 + 2), true},
		{"fractional-interior", 100, 101, 100.5, true},
		{"fractional-above-max", 100, 101, math.Nextafter(101, math.Inf(1)), false},
		{"zero", 0, 0, 0, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			d := Distribution{Count: 1, Min: tc.lo, Max: tc.hi, Mean: tc.mean, P50: tc.lo, P95: tc.hi, P99: tc.hi}
			if (measurementNumbers(d, "distribution") == nil) != tc.valid {
				t.Fatalf("mean %.17g in exact integer interval [%d,%d]: want valid=%v", tc.mean, tc.lo, tc.hi, tc.valid)
			}
		})
	}
}
