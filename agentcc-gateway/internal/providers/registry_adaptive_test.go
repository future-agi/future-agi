package providers

import (
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

func TestReloadRouterRetainsAdaptiveHealthMonitor(t *testing.T) {
	cfg := config.RoutingConfig{
		DefaultStrategy: "adaptive",
		Adaptive: config.AdaptiveConfig{
			Enabled:        true,
			UpdateInterval: time.Hour,
		},
	}
	models := map[string][]string{"sample": {"fast", "slow"}}
	registry := &Registry{
		providers:      map[string]Provider{"fast": nil, "slow": nil},
		modelProviders: models,
	}
	// A router may not exist during initial startup when routing is enabled
	// later through the admin config reload endpoint.
	monitor := routing.NewHealthMonitor(routing.NewLatencyTracker(0.3), nil)
	monitor.RecordSuccess("fast")
	if err := registry.ReloadRouter(cfg, monitor); err != nil {
		t.Fatal(err)
	}
	first := registry.Router()
	defer func() {
		if active := registry.Router(); active != nil {
			active.Close()
		}
	}()
	if first == nil || first.Strategy() != "adaptive" {
		t.Fatal("reload should initialize the actual adaptive strategy")
	}
	if first.GetHealthMonitor() != monitor {
		t.Fatal("initial routing reload did not reuse the server health monitor")
	}
	first.RecordLatency("fast", 25*time.Millisecond)

	if err := registry.ReloadRouter(cfg, monitor); err != nil {
		t.Fatal(err)
	}
	second := registry.Router()
	if second == nil || second == first {
		t.Fatal("routing reload did not replace the router")
	}
	if second.GetHealthMonitor() != monitor ||
		second.GetLatencyTracker() != first.GetLatencyTracker() {
		t.Fatal("reload lost the server's active monitor or latency tracker")
	}
	if got := monitor.GetHealth("fast").RequestCount; got != 1 {
		t.Fatalf("reload lost request history: got %d, want 1", got)
	}
}
