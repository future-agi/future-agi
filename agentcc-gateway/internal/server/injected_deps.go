package server

import (
	"sync/atomic"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// InjectedDeps are the dependencies the wiring provides and the handlers
// consume without importing their providers. main fills them in the wiring
// and hands them over once with SetInjectedDeps.
type InjectedDeps struct {
	// Prices returns an endpoint's price card (the catalog first, then the
	// configured price sources; modeldb by default).
	Prices models.PriceLookup
	// Priced reports whether a model can be priced on an API endpoint
	// ("chat", "embeddings"...): the wired cost.Priced, modeldb by default.
	Priced func(endpoint, model string) bool
	// ModelHealth is the per-model health; routing.NoModelHealth by default.
	ModelHealth routing.ModelHealthReader
	// Events is the log webhook's event sink (the flusher's EnqueueEvent).
	Events models.EventSink
}

// injectedDepsHolder is the late-bound holder of the injected dependencies,
// embedded in Handlers. It is seeded with safe defaults when the Handlers is
// built, so h.deps.Load() is never nil, and replaced once by main after the
// server wiring has run. Consumers read it at call time, never at
// construction, so a value the wiring overwrote is the value they see.
type injectedDepsHolder struct {
	deps atomic.Pointer[InjectedDeps]
}

// SetInjectedDeps installs d. A nil field keeps its default.
func (h *Handlers) SetInjectedDeps(d InjectedDeps) {
	def := h.defaultInjectedDeps()
	if d.Prices == nil {
		d.Prices = def.Prices
	}
	if d.Priced == nil {
		d.Priced = def.Priced
	}
	if d.ModelHealth == nil {
		d.ModelHealth = def.ModelHealth
	}
	if d.Events == nil {
		d.Events = def.Events
	}
	h.deps.Store(&d)
}

// Deps returns the injected dependencies in force now. Read it per call,
// never at construction. It is never nil: NewHandlers seeds the defaults, and
// a Handlers built without NewHandlers gets them on first use.
func (h *Handlers) Deps() *InjectedDeps {
	if d := h.deps.Load(); d != nil {
		return d
	}
	h.deps.CompareAndSwap(nil, h.defaultInjectedDeps())
	return h.deps.Load()
}

// defaultInjectedDeps are the safe defaults: prices from modeldb, no model
// health, and events dropped.
func (h *Handlers) defaultInjectedDeps() *InjectedDeps {
	return &InjectedDeps{
		Prices:      ModelDBPriceLookup(h.ModelDB),
		Priced:      ModelDBPriced(h.ModelDB),
		ModelHealth: routing.NoModelHealth,
		Events:      models.DiscardEvents,
	}
}

// ModelDBPriceLookup prices a model from the model database db returns at
// call time: the provider-qualified id first, then the bare model. A model
// with no non-zero price has no card.
func ModelDBPriceLookup(db func() *modeldb.ModelDB) models.PriceLookup {
	return func(providerID, model, endpointID string) (models.PriceCard, bool) {
		info := modelDBInfo(db(), providerID, model)
		if info == nil {
			return models.PriceCard{}, false
		}
		p := info.Pricing
		card := models.PriceCard{
			Prompt:     p.InputPerToken,
			Completion: p.OutputPerToken,
			CacheRead:  p.CachedInputPerToken,
			Image:      p.OutputPerImage,
			Source:     models.PriceSourceModelDB,
		}
		return card, card.Prompt > 0 || card.Completion > 0 || card.Image > 0
	}
}

// ModelDBPriced reports whether the model database db returns at call time
// can price model. It knows no per-endpoint prices, so endpoint is not
// consulted.
func ModelDBPriced(db func() *modeldb.ModelDB) func(endpoint, model string) bool {
	lookup := ModelDBPriceLookup(db)
	return func(_, model string) bool {
		_, ok := lookup("", model, "")
		return ok
	}
}

func modelDBInfo(db *modeldb.ModelDB, providerID, model string) *modeldb.ModelInfo {
	if db == nil {
		return nil
	}
	if providerID != "" {
		if info, ok := db.Get(providerID + "/" + model); ok {
			return info
		}
	}
	if info, ok := db.Get(model); ok {
		return info
	}
	return nil
}
