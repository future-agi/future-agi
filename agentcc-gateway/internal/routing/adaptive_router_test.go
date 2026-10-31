package routing

import (
	"errors"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func adaptiveTestConfig() config.RoutingConfig {
	return config.RoutingConfig{
		DefaultStrategy: "adaptive",
		Adaptive: config.AdaptiveConfig{
			Enabled:          true,
			LearningRequests: 2,
			UpdateInterval:   time.Hour,
			SignalWeights: config.SignalWeightsConfig{
				Latency:   0.5,
				ErrorRate: 0.5,
			},
		},
	}
}

func TestAdaptiveFactoryDoesNotSilentlyUseRoundRobin(t *testing.T) {
	strategy, err := NewStrategy("adaptive")
	if err == nil || strategy != nil {
		t.Fatalf("NewStrategy(adaptive) = (%v, %v), want an explicit dependency error", strategy, err)
	}
}

func TestAdaptiveRouterLearnsFromLiveHealth(t *testing.T) {
	cfg := adaptiveTestConfig()
	targets := map[string][]string{"model": {"fast", "slow"}}
	r, err := NewRouter(cfg, map[string]bool{"fast": true, "slow": true}, targets)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()

	if got := r.Strategy(); got != "adaptive" {
		t.Fatalf("strategy = %q, want adaptive", got)
	}
	hm := r.GetHealthMonitor()
	if hm == nil || hm.tracker != r.GetLatencyTracker() {
		t.Fatal("router must expose the monitor backed by its latency tracker")
	}

	for i, want := range []string{"fast", "slow"} {
		selected, err := r.Select("model")
		if err != nil {
			t.Fatal(err)
		}
		if selected.Target.ProviderID != want {
			t.Fatalf("learning selection %d: got %q, want %q", i, selected.Target.ProviderID, want)
		}
		if selected.StrategyName != "adaptive" {
			t.Fatalf("learning phase reported %q, want adaptive", selected.StrategyName)
		}
	}

	hm.RecordSuccess("fast")
	hm.RecordError("slow", errors.New("upstream failed"))
	r.RecordLatency("fast", 15*time.Millisecond)
	r.RecordLatency("slow", 750*time.Millisecond)

	adaptive, ok := r.defaultStrategy.(*AdaptiveStrategy)
	if !ok {
		t.Fatalf("default strategy type = %T, want *AdaptiveStrategy", r.defaultStrategy)
	}
	// Advance deterministically without sleeping for the periodic ticker.
	adaptive.phase.Store(1)
	adaptive.recalculateWeights()
	weights := adaptive.GetWeights()
	if weights["fast"] <= weights["slow"] {
		t.Fatalf("adaptive weights did not favor the healthy/fast provider: %v", weights)
	}

	fastSelections := 0
	for i := 0; i < 300; i++ {
		selected, err := r.Select("model")
		if err != nil {
			t.Fatal(err)
		}
		if selected.Target.ProviderID == "fast" {
			fastSelections++
		}
	}
	if fastSelections <= 200 {
		t.Fatalf("adaptive selection ignored healthy provider weighting: %d/300", fastSelections)
	}
}

func TestAdaptiveRouterReusesMonitorAfterRebuild(t *testing.T) {
	cfg := adaptiveTestConfig()
	providers := map[string]bool{"fast": true, "slow": true}
	models := map[string][]string{"model": {"fast", "slow"}}
	original, err := NewRouter(cfg, providers, models)
	if err != nil {
		t.Fatal(err)
	}
	defer original.Close()
	monitor := original.GetHealthMonitor()
	monitor.RecordSuccess("fast")
	original.RecordLatency("fast", 20*time.Millisecond)

	replacement, err := NewRouter(cfg, providers, models, monitor)
	if err != nil {
		t.Fatal(err)
	}
	defer replacement.Close()

	if replacement.GetHealthMonitor() != monitor ||
		replacement.GetLatencyTracker() != original.GetLatencyTracker() {
		t.Fatal("reloaded router must keep the active monitor and latency signals")
	}
	if replacement.GetHealthMonitor().GetHealth("fast").RequestCount != 1 {
		t.Fatal("provider request history lost on routing reload")
	}
	replacement.Close()
	replacement.Close()
}

func TestAdaptiveRouterRejectsMonitorWithNoLatencyTracker(t *testing.T) {
	cfg := adaptiveTestConfig()
	monitor := NewHealthMonitor(nil, nil)
	_, err := NewRouter(cfg, map[string]bool{}, map[string][]string{}, monitor)
	if err == nil {
		t.Fatal("router should reject an unusable health monitor")
	}
}
