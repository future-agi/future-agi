package routing

import (
	"testing"
	"time"
)

func TestLeastLatency_PreservesSubmillisecondOrdering(t *testing.T) {
	tracker := NewLatencyTracker(0.5)
	tracker.Record("slow", 900*time.Microsecond)
	tracker.Record("fast", 100*time.Microsecond)
	tracker.Record("fast", 300*time.Microsecond)

	if got := tracker.Get("fast"); got < 0.199999 || got > 0.200001 {
		t.Errorf("EWMA = %v ms, want 0.2 ms", got)
	}
	idx, err := (&LeastLatencyStrategy{}).Select([]RoutingTarget{
		{ProviderID: "slow", Healthy: true},
		{ProviderID: "fast", Healthy: true},
	}, tracker)
	if err != nil {
		t.Fatal(err)
	}
	if idx != 1 {
		t.Errorf("selected target %d, want faster target 1", idx)
	}
}
