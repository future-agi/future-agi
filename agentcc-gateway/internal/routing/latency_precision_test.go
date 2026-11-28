package routing

import (
	"math"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
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

func TestLatencyPrecision_RecordMilliseconds(t *testing.T) {
	for _, tc := range []struct {
		name    string
		samples []time.Duration
		want    float64
	}{
		{"nanosecond", []time.Duration{time.Nanosecond}, 0.000001},
		{"fractional", []time.Duration{1900 * time.Microsecond}, 1.9},
		{"fractional_ewma", []time.Duration{100 * time.Microsecond, 300 * time.Microsecond}, 0.2},
		{"mixed_ewma", []time.Duration{2 * time.Millisecond, 500 * time.Microsecond}, 1.25},
		{"whole_milliseconds", []time.Duration{2 * time.Millisecond, 4 * time.Millisecond}, 3},
		{"zero", []time.Duration{0}, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			tracker := NewLatencyTracker(0.5)
			for _, sample := range tc.samples {
				tracker.Record("provider", sample)
			}
			if got := tracker.Get("provider"); math.Abs(got-tc.want) > 1e-9 {
				t.Errorf("EWMA = %.9f ms, want %.9f ms", got, tc.want)
			}
		})
	}
}

func TestLatencyPrecision_RouterSelection(t *testing.T) {
	for _, tc := range []struct {
		name   string
		first  []time.Duration
		second []time.Duration
		want   string
	}{
		{"fractional_order", []time.Duration{900 * time.Microsecond}, []time.Duration{200 * time.Microsecond}, "second"},
		{"same_whole_millisecond", []time.Duration{1900 * time.Microsecond}, []time.Duration{1200 * time.Microsecond}, "second"},
		{"whole_milliseconds", []time.Duration{9 * time.Millisecond}, []time.Duration{2 * time.Millisecond}, "second"},
		{"equal_fractional_keeps_first", []time.Duration{200 * time.Microsecond}, []time.Duration{200 * time.Microsecond}, "first"},
		{"equal_whole_keeps_first", []time.Duration{2 * time.Millisecond}, []time.Duration{2 * time.Millisecond}, "first"},
		{"no_samples_keeps_first", nil, nil, "first"},
		{"unknown_beats_fractional", []time.Duration{200 * time.Microsecond}, nil, "second"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			router, err := NewRouter(config.RoutingConfig{
				DefaultStrategy: "least-latency",
				Targets: map[string][]config.RoutingTargetConfig{
					"model": {{Provider: "first"}, {Provider: "second"}},
				},
			}, map[string]bool{"first": true, "second": true}, nil)
			if err != nil {
				t.Fatal(err)
			}
			// Exercise the same health-monitor observation path used by the server.
			for _, sample := range tc.first {
				router.healthMonitor.RecordLatency("first", sample)
			}
			for _, sample := range tc.second {
				router.healthMonitor.RecordLatency("second", sample)
			}
			result, err := router.Select("model")
			if err != nil {
				t.Fatal(err)
			}
			if result.Target.ProviderID != tc.want {
				t.Errorf("selected %q, want %q", result.Target.ProviderID, tc.want)
			}
		})
	}
}
