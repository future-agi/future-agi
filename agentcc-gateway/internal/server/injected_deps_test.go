package server

import (
	"net/http"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// TestInjectedDepsLateBound: a value the wiring sets after the consumer was
// built is the value the consumer's next request sees, because consumers
// read Handlers.deps at call time (the late-bound holder).
func TestInjectedDepsLateBound(t *testing.T) {
	up := startHookUpstream(t, 0)
	ht := newHookOrderTest(t, up.URL, hookPlain, false) // the consumer is built here
	h := ht.srv.handlers

	var mu sync.Mutex
	var seen []models.PriceCard
	h.hookObserver = func(hook, _ string, rc *models.RequestContext) {
		if hook != hookRunBudgetCap {
			return
		}
		card, ok := h.Deps().Prices("openai", "gpt-4o", "")
		if !ok {
			t.Errorf("Prices found no card for gpt-4o")
		}
		mu.Lock()
		seen = append(seen, card)
		mu.Unlock()
	}

	if w := ht.chat(t, hookChatBody); w.Code != http.StatusOK {
		t.Fatalf("first request: status %d", w.Code)
	}

	// A goroutine started before the wiring ran, the way catalog.Run is,
	// that reads the holder each time it needs a price.
	stop := make(chan struct{})
	sawWired := make(chan struct{})
	go func() {
		for {
			select {
			case <-stop:
				return
			default:
			}
			if card, _ := h.Deps().Prices("openai", "gpt-4o", ""); card.Source == models.PriceSourcePriceCard {
				close(sawWired)
				return
			}
			time.Sleep(time.Millisecond)
		}
	}()
	defer close(stop)

	// A wiring init that runs after the consumer was built overwrites prices;
	// main then hands the result over once.
	wired := InjectedDeps{Prices: func(providerID, model, endpointID string) (models.PriceCard, bool) {
		return models.PriceCard{Prompt: 42e-6, Completion: 84e-6, Source: models.PriceSourcePriceCard}, true
	}}
	h.SetInjectedDeps(wired)

	if w := ht.chat(t, hookChatBody); w.Code != http.StatusOK {
		t.Fatalf("second request: status %d", w.Code)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(seen) != 2 {
		t.Fatalf("the consumer read prices %d times, want 2", len(seen))
	}
	if seen[0].Source != models.PriceSourceModelDB || seen[0].Prompt <= 0 {
		t.Errorf("before the wiring: card %+v, want the modeldb default", seen[0])
	}
	if seen[1].Source != models.PriceSourcePriceCard || seen[1].Prompt != 42e-6 {
		t.Errorf("after the wiring: card %+v, want the wired one", seen[1])
	}
	select {
	case <-sawWired:
	case <-time.After(5 * time.Second):
		t.Error("a goroutine started before the wiring never saw the wired prices")
	}

	// Fields the wiring left unset keep their defaults, so none is nil.
	d := h.Deps()
	if d.Priced == nil || d.ModelHealth != routing.NoModelHealth || d.Events == nil {
		t.Errorf("unset injected fields lost their defaults: %+v", d)
	}
}

func TestInjectedDepsDefaults(t *testing.T) {
	// A Handlers built without NewHandlers still has the defaults.
	h := &Handlers{modelDB: testModelDBPtr()}
	d := h.Deps()
	if d == nil || d.Prices == nil || d.Priced == nil || d.ModelHealth == nil || d.Events == nil {
		t.Fatalf("defaults missing: %+v", d)
	}
	if card, ok := d.Prices("openai", "gpt-4o", ""); !ok || card.Source != models.PriceSourceModelDB || card.Prompt <= 0 {
		t.Errorf("modeldb price for gpt-4o = %+v, %v", card, ok)
	}
	if _, ok := d.Prices("openai", "no-such-model-xyz", ""); ok {
		t.Error("an unknown model has a price")
	}
	if !d.Priced("chat", "gpt-4o") || d.Priced("chat", "no-such-model-xyz") {
		t.Error("Priced does not follow modeldb")
	}
	if s := d.ModelHealth.Snapshot("openai", "gpt-4o"); s.N != 0 {
		t.Errorf("default model health has data: %+v", s)
	}
	d.Events(models.EventKindOutcome, models.OutcomeEvent{}) // dropped, never panics
	if (&Handlers{}).Deps().Prices == nil {
		t.Error("a Handlers without a model database has no price lookup")
	}
}
