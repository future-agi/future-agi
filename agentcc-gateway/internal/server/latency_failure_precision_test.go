package server

import (
	"context"
	"errors"
	"math"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestLeastLatencyFailurePrecisionObserver(t *testing.T) {
	for _, tc := range []struct {
		name        string
		first       time.Duration
		firstSample bool
		previous    time.Duration
		hasPrevious bool
		failure     time.Duration
		hasFailure  bool
		wantAverage float64
		wantTarget  string
	}{
		{"first_fractional_failure", 100 * time.Microsecond, true, 0, false, 900 * time.Microsecond, true, 0.9, "first"},
		{"first_nanosecond_failure", time.Nanosecond, true, 0, false, 9 * time.Nanosecond, true, 0.000009, "first"},
		{"fast_failure_preserves_average", 100 * time.Microsecond, true, 900 * time.Microsecond, true, 100 * time.Microsecond, true, 0.9, "first"},
		{"slow_failure_raises_average", 300 * time.Microsecond, true, 100 * time.Microsecond, true, 900 * time.Microsecond, true, 0.34, "first"},
		{"fractional_milliseconds", 1200 * time.Microsecond, true, 0, false, 1900 * time.Microsecond, true, 1.9, "first"},
		{"whole_milliseconds", 2 * time.Millisecond, true, 0, false, 4 * time.Millisecond, true, 4, "first"},
		{"equal_fractional_keeps_first", 900 * time.Microsecond, true, 0, false, 900 * time.Microsecond, true, 0.9, "first"},
		{"unknown_remains_favored", 100 * time.Microsecond, true, 0, false, 0, false, 0, "second"},
		{"unsampled_keeps_first", 0, false, 0, false, 0, false, 0, "first"},
		{"zero_failure_keeps_tie", 0, true, 0, false, 0, true, 0, "first"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cfg := config.DefaultConfig()
			cfg.Providers = map[string]config.ProviderConfig{
				"first":  {APIFormat: "openai", BaseURL: "http://localhost:1", Models: []string{"precision-sample"}},
				"second": {APIFormat: "openai", BaseURL: "http://localhost:1", Models: []string{"precision-sample"}},
			}
			cfg.Routing = config.RoutingConfig{
				DefaultStrategy: "least-latency",
				Targets: map[string][]config.RoutingTargetConfig{
					"precision-sample": {{Provider: "first"}, {Provider: "second"}},
				},
			}
			srv, _ := adaptiveServer(t, cfg)
			router := srv.registry.Router()
			if router == nil || srv.handlers.healthMonitor != router.GetHealthMonitor() {
				t.Fatal("production handlers and router must share the health monitor")
			}
			first, ok := srv.registry.GetProvider("first")
			if !ok {
				t.Fatal("first provider missing")
			}
			second, ok := srv.registry.GetProvider("second")
			if !ok {
				t.Fatal("second provider missing")
			}
			// Feed deterministic durations through the real production observer;
			// no sleeps, upstream requests, or transport timing assumptions.
			if tc.firstSample {
				srv.handlers.recordProviderOutcome(context.Background(), first, tc.first, nil)
			}
			if tc.hasPrevious {
				srv.handlers.recordProviderOutcome(context.Background(), second, tc.previous, nil)
			}
			if tc.hasFailure {
				srv.handlers.recordProviderOutcome(context.Background(), second, tc.failure, errors.New("synthetic upstream failure"))
			}
			tracker := router.GetLatencyTracker()
			if got := tracker.Get("second"); math.Abs(got-tc.wantAverage) > 1e-12 {
				t.Errorf("failed provider EWMA = %.12f ms, want %.12f ms", got, tc.wantAverage)
			}
			if got := tracker.Get("never-observed"); got != 0 {
				t.Errorf("unknown provider EWMA = %v, want 0", got)
			}
			selected, err := router.Select("precision-sample")
			if err != nil {
				t.Fatal(err)
			}
			if got := selected.Target.ProviderID; got != tc.wantTarget {
				t.Errorf("selected %q, want %q", got, tc.wantTarget)
			}
		})
	}
}
