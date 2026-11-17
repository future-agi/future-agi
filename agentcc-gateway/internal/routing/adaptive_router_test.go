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

// bindRouterHealth gives r's adaptive strategy the router's own latency
// tracker and health monitor, as the server does at startup.
func bindRouterHealth(r *Router) {
	BindStrategyDeps(r, func() StrategyDeps {
		return StrategyDeps{Latency: r.GetLatencyTracker(), Health: r.GetHealthMonitor()}
	})
}

// The factory never hands back round-robin for "adaptive": it returns the
// adaptive strategy itself, which waits for its deps and says so.
func TestAdaptiveFactoryDoesNotSilentlyUseRoundRobin(t *testing.T) {
	strategy, err := NewStrategy("adaptive")
	if err != nil {
		t.Fatalf("NewStrategy(adaptive): %v", err)
	}
	a, ok := strategy.(*AdaptiveStrategy)
	if !ok || a.Name() != "adaptive" {
		t.Fatalf("NewStrategy(adaptive) = %T named %q, want *AdaptiveStrategy named adaptive", strategy, strategy.Name())
	}
	if !a.strategyDepsCell().awaits() {
		t.Fatal("an adaptive strategy built without deps must report that it awaits them")
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
	if !StrategyAwaitsDeps(r) {
		t.Fatal("the router's adaptive strategy must await its deps until the server binds them")
	}
	bindRouterHealth(r)

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

	if _, ok := r.defaultStrategy.(*AdaptiveStrategy); !ok {
		t.Fatalf("default strategy type = %T, want *AdaptiveStrategy", r.defaultStrategy)
	}
	// The strategy reads the live health at every selection: no update tick
	// is needed between recording the results and ranking by them.
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
		t.Fatalf("adaptive selection ignored the healthy, fast provider: %d/300", fastSelections)
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

// Close never waits on anything: adaptive routing owns no background updater,
// so closing a router returns at once, also while the health monitor is busy
// and also when called again.
func TestAdaptiveCloseWaitsForUpdater(t *testing.T) {
	cfg := adaptiveTestConfig()
	cfg.Adaptive.UpdateInterval = time.Millisecond
	r, err := NewRouter(cfg, map[string]bool{}, map[string][]string{})
	if err != nil {
		t.Fatal(err)
	}
	r.healthMonitor.mu.Lock()
	stopped := make(chan struct{})
	go func() {
		r.Close()
		r.Close()
		close(stopped)
	}()
	select {
	case <-stopped:
		r.healthMonitor.mu.Unlock()
	case <-time.After(5 * time.Second):
		r.healthMonitor.mu.Unlock()
		t.Fatal("Close waited on the health monitor: adaptive routing has no updater to join")
	}
}

func TestAdaptiveRouterRejectsMonitorWithNoLatencyTracker(t *testing.T) {
	cfg := adaptiveTestConfig()
	monitor := NewHealthMonitor(nil, nil)
	_, err := NewRouter(cfg, map[string]bool{}, map[string][]string{}, monitor)
	if err == nil {
		t.Fatal("router should reject an unusable health monitor")
	}
}
