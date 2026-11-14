package budget

import "testing"

// The in-memory tracker records spend as the shared ledger does: a positive
// cost below half a micro-dollar counts as one micro-dollar, not as its raw
// float, so the replicas' local counters agree with Redis and credits.
func TestTrackerRecordsATinyCostAsOneMicro(t *testing.T) {
	tr := NewTracker(testConfig())
	tr.RecordSpend("", "", "", "gpt-4o", nil, 4e-7)
	total, perModel := tr.GetSpend("org", "")
	if total != 1e-6 || perModel["gpt-4o"] != 1e-6 {
		t.Fatalf("org spend = %g (model %g), want 1e-6", total, perModel["gpt-4o"])
	}
	tr.RecordSpend("", "", "", "gpt-4o", nil, 0)
	if total, _ := tr.GetSpend("org", ""); total != 1e-6 {
		t.Fatalf("a $0 request changed spend to %g", total)
	}
}
