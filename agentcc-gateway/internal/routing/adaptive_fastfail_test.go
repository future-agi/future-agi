package routing

import (
	"errors"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func newScoringStrategy(t *testing.T) (*AdaptiveStrategy, *HealthMonitor) {
	t.Helper()
	tracker := NewLatencyTracker(0.3)
	hm := NewHealthMonitor(tracker, nil)
	a := NewAdaptiveStrategy(config.AdaptiveConfig{UpdateInterval: time.Hour}, tracker, hm)
	t.Cleanup(a.Stop)
	return a, hm
}

// A target that fails every call scores below a healthy one however fast it
// fails, with the default signal weights (latency 0.5, error 0.4).
func TestAdaptiveFastFailuresScoreBelowSlowSuccesses(t *testing.T) {
	a, hm := newScoringStrategy(t)
	revoked := errors.New("synthetic 401: revoked key")
	for i := 0; i < 50; i++ {
		// The server records latency for successful calls only.
		hm.RecordError("failing-fast", revoked)
		hm.RecordLatency("healthy-slow", 800*time.Millisecond)
		hm.RecordSuccess("healthy-slow")
	}
	a.recalculateWeights()
	w := a.GetWeights()
	if w["failing-fast"] >= w["healthy-slow"] || w["failing-fast"] > 0.06 {
		t.Fatalf("failing target outweighs or exceeds min weight: failing=%.3f healthy=%.3f", w["failing-fast"], w["healthy-slow"])
	}
}

// Old successes fade: a target with a long fast, healthy history that starts
// failing loses its weight within tens of calls, not after its history.
func TestAdaptiveSuccessRateDecays(t *testing.T) {
	a, hm := newScoringStrategy(t)
	for i := 0; i < 200; i++ {
		hm.RecordLatency("was-fast", 30*time.Millisecond)
		hm.RecordSuccess("was-fast")
		hm.RecordLatency("healthy-slow", 800*time.Millisecond)
		hm.RecordSuccess("healthy-slow")
	}
	for i := 0; i < 30; i++ {
		hm.RecordError("was-fast", errors.New("synthetic 503"))
	}
	if got := hm.GetHealth("was-fast"); got.SuccessRate < 0.8 || got.RecentSuccessRate > 0.1 {
		t.Fatalf("lifetime=%.3f recent=%.3f, want lifetime kept for the health endpoint and recent decayed", got.SuccessRate, got.RecentSuccessRate)
	}
	a.recalculateWeights()
	w := a.GetWeights()
	if w["was-fast"] >= w["healthy-slow"] {
		t.Fatalf("stale successes kept a failing target ahead: was_fast=%.3f healthy=%.3f", w["was-fast"], w["healthy-slow"])
	}
	for i := 0; i < 30; i++ {
		hm.RecordLatency("was-fast", 30*time.Millisecond)
		hm.RecordSuccess("was-fast")
	}
	if got := hm.GetHealth("was-fast").RecentSuccessRate; got < 0.9 {
		t.Fatalf("recent success rate did not recover: %.3f", got)
	}
}

// Reloading the routing config keeps learned weights for providers that still
// exist and the active phase, and drops weights for removed providers.
func TestAdaptiveReloadKeepsLearnedWeights(t *testing.T) {
	cfg := adaptiveTestConfig()
	ids := map[string]bool{"fast": true, "slow": true, "gone": true}
	first, err := NewRouter(cfg, ids, map[string][]string{"model": {"fast", "slow", "gone"}})
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	hm := first.GetHealthMonitor()
	for _, id := range []string{"fast", "slow", "gone"} {
		hm.RecordLatency(id, 10*time.Millisecond)
		hm.RecordSuccess(id)
	}
	hm.RecordError("slow", errors.New("synthetic 503"))
	prev := first.defaultStrategy.(*AdaptiveStrategy)
	prev.phase.Store(1)
	prev.recalculateWeights()
	learned := prev.GetWeights()

	kept := map[string]bool{"fast": true, "slow": true}
	second, err := NewRouter(cfg, kept, map[string][]string{"model": {"fast", "slow"}}, hm)
	if err != nil {
		t.Fatal(err)
	}
	defer second.Close()
	second.InheritAdaptiveState(first, kept)
	next := second.defaultStrategy.(*AdaptiveStrategy)
	got := next.GetWeights()
	if next.GetPhase() != "active" || len(got) != 2 || got["fast"] != learned["fast"] || got["slow"] != learned["slow"] {
		t.Fatalf("reload lost learned state: phase=%s learned=%v got=%v", next.GetPhase(), learned, got)
	}
}

// Failed calls raise the least-latency average when they are slow and never
// lower it when they are fast; adaptive's success-only latency ignores them.
func TestFailedCallLatencyIsAPenaltyForLeastLatencyOnly(t *testing.T) {
	_, hm := newScoringStrategy(t)
	hm.RecordLatency("p", 20*time.Millisecond)
	hm.RecordSuccess("p")
	hm.RecordFailureLatency("p", 2*time.Millisecond)
	hm.RecordError("p", errors.New("synthetic 401"))
	if got := hm.GetHealth("p"); got.LatencyEWMAMs != 20 || got.SuccessLatencyEWMAMs != 20 {
		t.Fatalf("fast failure changed latency: least_latency=%dms success=%.0fms, want 20 and 20", got.LatencyEWMAMs, got.SuccessLatencyEWMAMs)
	}
	hm.RecordFailureLatency("p", 1020*time.Millisecond)
	hm.RecordError("p", errors.New("synthetic 503"))
	if got := hm.GetHealth("p"); got.LatencyEWMAMs != 320 || got.SuccessLatencyEWMAMs != 20 {
		t.Fatalf("slow failure: least_latency=%dms success=%.0fms, want 320 and 20", got.LatencyEWMAMs, got.SuccessLatencyEWMAMs)
	}
	hm.RecordFailureLatency("never-succeeded", 50*time.Millisecond)
	if got := hm.GetHealth("never-succeeded"); got.LatencyEWMAMs != 50 || got.SuccessLatencyEWMAMs != 0 {
		t.Fatalf("first failure: least_latency=%dms success=%.0fms, want 50 and 0", got.LatencyEWMAMs, got.SuccessLatencyEWMAMs)
	}
}
