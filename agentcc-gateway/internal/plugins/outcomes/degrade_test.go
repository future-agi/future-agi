package outcomes

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

func TestOutcomeDegradesModelAt20Of25FailuresAndRecovers(t *testing.T) {
	h := newHarness(t)
	state := func() string {
		m, _ := h.catalog.Load().Get("openrouter", "vendor/flaky")
		return m.State
	}
	fail := func(n int) {
		for range n {
			h.health.Record("openrouter", "vendor/flaky", 30*time.Millisecond, models.ErrClassUpstream5xx)
			h.run(call{id: "req", model: "vendor/flaky", outcome: models.OutcomeError, errClass: models.ErrClassUpstream5xx}).Release()
		}
	}
	succeed := func(n int) {
		for range n {
			h.health.Record("openrouter", "vendor/flaky", 30*time.Millisecond, "")
			h.run(call{id: "req", model: "vendor/flaky", resp: answered("x", 3)}).Release()
		}
	}
	succeed(5)
	fail(14)
	if s := state(); s != catalog.StateActive {
		t.Fatalf("after 19 calls: state %q, want active (too few samples)", s)
	}
	fail(6) // 25 calls, 20 failed
	if m, _ := h.catalog.Load().Get("openrouter", "vendor/flaky"); m.State != catalog.StateDegraded || m.StateReason != ReasonLowSuccessRate {
		t.Fatalf("after 20 failures of 25: state %q (%q), want degraded", m.State, m.StateReason)
	}
	if m, _ := h.catalog.Load().Get("openrouter", "vendor/ok"); m.State != catalog.StateActive {
		t.Errorf("a healthy model was degraded too: %q", m.State)
	}
	succeed(100) // 105 of 125: 0.84, still below the recovery rate
	if s := state(); s != catalog.StateDegraded {
		t.Fatalf("at 84%% success: state %q, want still degraded", s)
	}
	succeed(75) // 180 of 200: 0.9
	if s := state(); s != catalog.StateActive {
		t.Fatalf("at 90%% success: state %q, want active", s)
	}
}

// fakeHealth reports one snapshot for every model.
type fakeHealth struct {
	mu   sync.Mutex
	snap routing.ModelHealthSnapshot
}

func (f *fakeHealth) Snapshot(string, string) routing.ModelHealthSnapshot {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.snap
}

func (f *fakeHealth) set(s routing.ModelHealthSnapshot) {
	f.mu.Lock()
	f.snap = s
	f.mu.Unlock()
}

func TestOutcomeDegradedModelRecoversWhenItsHealthAgesOut(t *testing.T) {
	h := newHarness(t)
	health := &fakeHealth{snap: routing.ModelHealthSnapshot{N: 30, SuccessRate: 0.1}}
	p := New(Deps{Outcomes: h.out, Catalog: h.catalog, Health: func() routing.ModelHealthReader { return health }})
	rc := models.AcquireRequestContext()
	rc.AddAttempt(models.Attempt{Provider: "openrouter", Model: "vendor/flaky", Outcome: models.OutcomeError})
	p.ProcessResponse(bg, rc)
	rc.Release()
	if m, _ := h.catalog.Load().Get("openrouter", "vendor/flaky"); m.State != catalog.StateDegraded {
		t.Fatalf("state %q, want degraded", m.State)
	}
	// No traffic reaches the model; its window empties, and the sweep alone
	// recovers it.
	health.set(routing.ModelHealthSnapshot{})
	ctx, cancel := context.WithCancel(bg)
	defer cancel()
	go p.degrader.run(ctx, 5*time.Millisecond)
	deadline := time.Now().Add(2 * time.Second)
	for {
		if m, _ := h.catalog.Load().Get("openrouter", "vendor/flaky"); m.State == catalog.StateActive {
			return
		}
		if time.Now().After(deadline) {
			t.Fatal("the sweep never recovered the degraded model")
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func TestOutcomeUnmanagedModelsNeverDegrade(t *testing.T) {
	h := newHarness(t)
	health := &fakeHealth{snap: routing.ModelHealthSnapshot{N: 30, SuccessRate: 0}}
	p := New(Deps{Outcomes: h.out, Catalog: h.catalog, Health: func() routing.ModelHealthReader { return health }})
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.AddAttempt(models.Attempt{Provider: "byok-openai", Model: "vendor/flaky", Outcome: models.OutcomeError})
	p.ProcessResponse(bg, rc)
	if m, _ := h.catalog.Load().Get("openrouter", "vendor/flaky"); m.State != catalog.StateActive {
		t.Fatalf("a failing BYOK provider degraded the managed catalog's model: %q", m.State)
	}
}
