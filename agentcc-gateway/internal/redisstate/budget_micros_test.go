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

// A huge cost (org custom pricing has no upper bound) is recorded in the
// shared ledger, saturated at MaxInt64 micro-dollars, so the ledger the budget
// checks read blocks the org. Before, the write failed and the ledger, still
// authoritative while readable, kept letting requests through.
func TestBudgetStore_Redis_HugeCostIsRecorded(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()
	ts := time.Now().Format("150405.000000")
	bs := NewBudgetStore(client, "test:budget:huge:"+ts+":")

	// Control: a large cost that fits a double exactly.
	total, ok := bs.RecordSpend("org-a-"+ts, "org", "", "daily", "m", 5e8)
	if !ok || total != 5e8 {
		t.Fatalf("RecordSpend(5e8): total %g ok=%v; want 5e8", total, ok)
	}
	total, allowed, ok := bs.CheckAndRecordSpend("org-b-"+ts, "org", "", "daily", "m", 5e8, 0, 0)
	if !ok || !allowed || total != 5e8 {
		t.Fatalf("CheckAndRecordSpend(5e8, no limit): total %g allowed=%v ok=%v; want 5e8", total, allowed, ok)
	}

	// 1e13 USD saturates at MaxInt64 micro-dollars.
	total, ok = bs.RecordSpend("org-c-"+ts, "org", "", "daily", "m", 1e13)
	if !ok || total <= 9e12 {
		t.Fatalf("RecordSpend(1e13): total %g ok=%v; want the saturated ~9.22e12", total, ok)
	}
	if spent, _, ok := bs.GetSpend("org-c-"+ts, "org", "", "daily"); !ok || spent <= 9e12 {
		t.Fatalf("GetSpend after 1e13: %g ok=%v; want ~9.22e12", spent, ok)
	}
}
