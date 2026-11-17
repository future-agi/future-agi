package budget

import "testing"

// A cost beyond the int64 micro-dollar range (org custom pricing has no upper
// bound) is recorded as the largest spend, never as a negative one: the
// tracker keeps blocking.
func TestTrackerBlocksAfterAHugeCharge(t *testing.T) {
	tr := NewTracker(testConfig())
	tr.RecordSpend("", "", "", "gpt-3.5-turbo", nil, 1e13)
	if total, _ := tr.GetSpend("org", "org"); total <= 0 {
		t.Fatalf("org spend after a 1e13 charge = %g; want positive", total)
	}
	if result := tr.CheckBudget("", "", "", "gpt-3.5-turbo", nil); result.Allowed {
		t.Fatal("org budget did not block after a 1e13 charge")
	}
}
