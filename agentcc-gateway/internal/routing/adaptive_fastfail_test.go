package routing

import (
	"errors"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// newScoringStrategy is an adaptive strategy that reads a fresh latency
// tracker and health monitor at every selection. It keeps no weights and runs
// no loop, so there is nothing to stop.
func newScoringStrategy(t *testing.T) (*AdaptiveStrategy, *HealthMonitor) {
	t.Helper()
	tracker := NewLatencyTracker(0.3)
	hm := NewHealthMonitor(tracker, nil)
	a := NewAdaptiveStrategy(config.AdaptiveConfig{}, &StrategyDeps{Latency: tracker, Health: hm})
	return a, hm
}

// scoringTargets are two healthy targets with no model override, so adaptive
// ranks them by their providers' health.
func scoringTargets(first, second string) []RoutingTarget {
	return []RoutingTarget{{ProviderID: first, Healthy: true}, {ProviderID: second, Healthy: true}}
}

// A target that fails every call ranks below a healthy one however fast it
// fails, with the default signal weights (latency 0.5, error 0.4): it gets
// only the share adaptive spends on noticing a recovery.
func TestAdaptiveFastFailuresScoreBelowSlowSuccesses(t *testing.T) {
	a, hm := newScoringStrategy(t)
	revoked := errors.New("synthetic 401: revoked key")
	for i := 0; i < 50; i++ {
		// The server records latency for successful calls only.
		hm.RecordError("failing-fast", revoked)
		hm.RecordLatency("healthy-slow", 800*time.Millisecond)
		hm.RecordSuccess("healthy-slow")
	}
	if failing := share(t, a, scoringTargets("failing-fast", "healthy-slow"), 0, 4000); failing > 0.1 {
		t.Fatalf("failing target share = %.3f, want only the exploration share (0.05)", failing)
	}
}

// Old successes fade: a target with a long fast, healthy history that starts
// failing loses its rank within tens of calls, not after its history.
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
	if healthy := share(t, a, scoringTargets("was-fast", "healthy-slow"), 1, 4000); healthy < 0.9 {
		t.Fatalf("stale successes kept a failing target ahead: healthy share = %.3f, want >= 0.9", healthy)
	}
	for i := 0; i < 30; i++ {
		hm.RecordLatency("was-fast", 30*time.Millisecond)
		hm.RecordSuccess("was-fast")
	}
	if got := hm.GetHealth("was-fast").RecentSuccessRate; got < 0.9 {
		t.Fatalf("recent success rate did not recover: %.3f", got)
	}
}

// Reloading the routing config keeps what adaptive learned for providers that
// still exist: a router rebuilt on the old one's health monitor ranks at once,
// with no new learning round, and a removed provider is no target.
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

	kept := map[string]bool{"fast": true, "slow": true}
	second, err := NewRouter(cfg, kept, map[string][]string{"model": {"fast", "slow"}}, hm)
	if err != nil {
		t.Fatal(err)
	}
	defer second.Close()
	second.InheritAdaptiveState(first, kept)
	if second.GetHealthMonitor() != hm {
		t.Fatal("the rebuilt router must keep the monitor adaptive learned from")
	}
	BindStrategyDeps(second, func() StrategyDeps {
		return StrategyDeps{Latency: second.GetLatencyTracker(), Health: second.GetHealthMonitor()}
	})
	fast := 0
	for i := 0; i < 300; i++ {
		selected, err := second.Select("model")
		if err != nil {
			t.Fatal(err)
		}
		switch selected.Target.ProviderID {
		case "fast":
			fast++
		case "gone":
			t.Fatal("a removed provider was selected after the reload")
		}
	}
	if fast <= 200 {
		t.Fatalf("reload lost what adaptive learned: fast selected %d/300, want it favoured from the first selection", fast)
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
