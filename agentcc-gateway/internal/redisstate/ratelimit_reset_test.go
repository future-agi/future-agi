package redisstate

import "testing"

// secondsUntilAdmitted must match the sliding-window script second for second.
// Expected waits are worked by hand from the script's formula,
// floor(prev*(60-elapsed)/60) + curr < limit.
func TestSecondsUntilAdmitted(t *testing.T) {
	const windowStart = int64(1_700_000_040) // a minute boundary
	cases := []struct {
		name       string
		prev, curr int64
		limit      int
		elapsed    int64
		want       int
	}{
		// This window is full: the next window's carried-over share is
		// floor(1*60/60) = 1 at its first second and 0 from its second.
		{"full window, late", 0, 1, 1, 40, 21},
		{"full window, at its start", 0, 1, 1, 0, 61},
		{"full window, last second", 0, 1, 1, 59, 2},
		{"full window, large limit", 3, 100, 100, 10, 51},
		// The previous window's share decays inside this one:
		// floor(100*59/60) = 98 < 100 one second on.
		{"previous window decays at once", 100, 0, 100, 0, 1},
		// floor(10*(60-e)/60) + 5 < 10 first holds at e = 31.
		{"previous window decays mid-window", 10, 5, 10, 0, 31},
		// A lowered limit: floor(5*(60-e)/60) < 1 first holds at e = 49 of
		// the next window, 10 + 49 seconds on.
		{"count above a lowered limit", 0, 5, 1, 50, 59},
		// Too many to drain within the next window: the one after opens empty.
		{"count far above the limit", 0, 200, 1, 59, 61},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			now := windowStart + tc.elapsed
			got := secondsUntilAdmitted(tc.prev, tc.curr, tc.limit, now)
			if got != tc.want {
				t.Fatalf("secondsUntilAdmitted(prev=%d, curr=%d, limit=%d, elapsed=%d) = %d, want %d",
					tc.prev, tc.curr, tc.limit, tc.elapsed, got, tc.want)
			}
		})
	}
}
