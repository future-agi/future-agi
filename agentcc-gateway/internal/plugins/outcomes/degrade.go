package outcomes

import (
	"context"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// A managed model is marked degraded once this node's health window holds at
// least MinHealthSamples calls to it and fewer than DegradeBelow of them
// succeeded. It recovers when the success rate reaches RecoverAtOrAbove, or
// when the window holds fewer than MinHealthSamples calls, so the evidence
// has aged out (auto routing stops sending it traffic). The gap between the
// two rates keeps a model near the threshold from flapping, and each flip
// republishes the catalog.
const (
	MinHealthSamples = 20
	DegradeBelow     = 0.8
	RecoverAtOrAbove = 0.9

	// ReasonLowSuccessRate is the catalog state_reason of a degraded model.
	ReasonLowSuccessRate = "low_success_rate"
)

// ModelStates is the managed catalog the degrader marks (catalog.Store).
type ModelStates interface {
	Load() *catalog.Catalog
	SetModelState(id, state, reason string) error
}

// degrader marks catalog models degraded from this node's per-model health
// and recovers them. Health is kept per (provider, model) while the catalog
// keeps one state per model id, so a model is degraded while any provider
// serving it is unhealthy.
type degrader struct {
	states ModelStates
	health func() routing.ModelHealthReader

	mu       sync.Mutex
	degraded map[string]map[string]bool // model id -> providers whose health degraded it
	count    atomic.Int32               // len(degraded), read without mu on the hot path
}

func newDegrader(states ModelStates, health func() routing.ModelHealthReader) *degrader {
	return &degrader{states: states, health: health, degraded: map[string]map[string]bool{}}
}

// observe re-judges each managed (provider, model) the attempts called.
func (d *degrader) observe(attempts []models.Attempt) {
	if d.states == nil || d.health == nil {
		return
	}
	cat := d.states.Load()
	for i, a := range attempts {
		if a.Model == "" || !cat.Has(a.Provider, a.Model) || calledBefore(attempts[:i], a) {
			continue
		}
		d.judge(a.Provider, a.Model)
	}
}

func calledBefore(earlier []models.Attempt, a models.Attempt) bool {
	for _, e := range earlier {
		if e.Provider == a.Provider && e.Model == a.Model {
			return true
		}
	}
	return false
}

// judge applies the thresholds to provider's health for model and records a
// transition in the catalog. It holds mu across the catalog write so two
// requests cannot publish a degrade and a recovery out of order.
func (d *degrader) judge(provider, model string) {
	h := d.health()
	if h == nil {
		return
	}
	snap := h.Snapshot(provider, model)
	unhealthy := snap.N >= MinHealthSamples && snap.SuccessRate < DegradeBelow
	recovered := snap.N < MinHealthSamples || snap.SuccessRate >= RecoverAtOrAbove
	if !unhealthy && (!recovered || d.count.Load() == 0) {
		return // nothing to degrade, and nothing degraded to recover
	}

	d.mu.Lock()
	defer d.mu.Unlock()
	providers := d.degraded[model]
	switch {
	case unhealthy && !providers[provider]:
		if providers == nil {
			providers = map[string]bool{}
			d.degraded[model] = providers
			d.count.Add(1)
		}
		providers[provider] = true
		if len(providers) == 1 {
			d.setState(model, catalog.StateDegraded, ReasonLowSuccessRate, provider, snap)
		}
	case recovered && providers[provider]:
		delete(providers, provider)
		if len(providers) == 0 {
			delete(d.degraded, model)
			d.count.Add(-1)
			d.setState(model, catalog.StateActive, "", provider, snap)
		}
	}
}

func (d *degrader) setState(model, state, reason, provider string, snap routing.ModelHealthSnapshot) {
	if err := d.states.SetModelState(model, state, reason); err != nil {
		slog.Warn("outcomes: could not set the catalog model state", "model", model, "state", state, "error", err)
		return
	}
	slog.Info("outcomes: catalog model state changed from per-model health", "model", model, "provider", provider,
		"state", state, "calls", snap.N, "success_rate", snap.SuccessRate)
}

// sweep re-judges every degraded model, so one that gets no traffic once it
// is degraded still recovers when its health window empties.
func (d *degrader) sweep() {
	d.mu.Lock()
	var pairs [][2]string
	for model, providers := range d.degraded {
		for provider := range providers {
			pairs = append(pairs, [2]string{provider, model})
		}
	}
	d.mu.Unlock()
	for _, p := range pairs {
		d.judge(p[0], p[1])
	}
}

// run sweeps every interval until ctx ends.
func (d *degrader) run(ctx context.Context, every time.Duration) {
	t := time.NewTicker(every)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			d.sweep()
		}
	}
}
