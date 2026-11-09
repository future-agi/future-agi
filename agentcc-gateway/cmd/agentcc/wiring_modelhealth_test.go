package main

import (
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// The modelhealth wiring replaces the no-op default with a store that both
// records attempts and reports them, and that store is what the handlers
// receive.
func TestModelHealthWiring(t *testing.T) {
	d := &wiringDeps{cfg: config.DefaultConfig()}
	d.seedInjected(func() *modeldb.ModelDB { return nil })
	if err := initModelHealth(d); err != nil {
		t.Fatalf("initModelHealth: %v", err)
	}
	if d.modelHealth == routing.NoModelHealth {
		t.Fatal("initModelHealth left the no-op default")
	}
	rec, ok := d.injected().ModelHealth.(routing.ModelHealthRecorder)
	if !ok {
		t.Fatalf("the injected model health %T does not take attempt results", d.injected().ModelHealth)
	}
	rec.Record("openrouter", "vendor/a", 120*time.Millisecond, models.ErrClassRateLimited)
	if snap := d.injected().ModelHealth.Snapshot("openrouter", "vendor/a"); snap.N != 1 || snap.LastErrorClass != string(models.ErrClassRateLimited) {
		t.Fatalf("snapshot after one rate-limited attempt = %+v", snap)
	}
}
