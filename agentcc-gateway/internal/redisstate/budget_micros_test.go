package redisstate

import (
	"testing"
	"time"
)

// The shared budget ledger never records a positive cost as 0 micro-dollars:
// a reported $0.0000004 deducts exactly one micro-dollar, a stated $0 deducts
// nothing, and larger costs round to the nearest micro-dollar as before. Both
// RecordSpend and CheckAndRecordSpend, against a real Redis (TEST_REDIS_ADDR).
func TestBudgetStore_Redis_TinyCostsInMicros(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()
	for _, tc := range []struct {
		name string
		cost float64
		want float64 // USD, whole micro-dollars
	}{
		{"4e-7 deducts one micro", 4e-7, 1e-6},
		{"zero deducts nothing", 0, 0},
		{"1.4e-6 rounds to one micro", 1.4e-6, 1e-6},
		{"1.5e-6 rounds to two micros", 1.5e-6, 2e-6},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ts := time.Now().Format("150405.000000")
			bs := NewBudgetStore(client, "test:budget:micros:"+ts+":")

			total, ok := bs.RecordSpend("org-record-"+ts, "org", "", "daily", "m", tc.cost)
			if !ok || total != tc.want {
				t.Errorf("RecordSpend(%g): total %g ok=%v; want %g", tc.cost, total, ok, tc.want)
			}
			if spent, _, ok := bs.GetSpend("org-record-"+ts, "org", "", "daily"); !ok || spent != tc.want {
				t.Errorf("RecordSpend(%g): GetSpend %g ok=%v; want %g", tc.cost, spent, ok, tc.want)
			}

			total, allowed, ok := bs.CheckAndRecordSpend("org-check-"+ts, "org", "", "daily", "m", tc.cost, 1, 0)
			if !ok || !allowed || total != tc.want {
				t.Errorf("CheckAndRecordSpend(%g): total %g allowed=%v ok=%v; want %g", tc.cost, total, allowed, ok, tc.want)
			}
			if spent, _, ok := bs.GetSpend("org-check-"+ts, "org", "", "daily"); !ok || spent != tc.want {
				t.Errorf("CheckAndRecordSpend(%g): GetSpend %g ok=%v; want %g", tc.cost, spent, ok, tc.want)
			}
		})
	}
}
