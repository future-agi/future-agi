package cost

import (
	"context"
	"log/slog"
	"sync"
	"sync/atomic"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	// CostSourceUnpriced is written to Metadata["cost_source"] when the request
	// used something billable (tokens, seconds of audio, characters of text) but
	// no price is known for the model. The request is recorded without a cost.
	// Budgets price requests from their own table, so this flag says nothing
	// about whether a budget counted the request.
	CostSourceUnpriced = string(models.CostSourceUnpriced)

	// maxUnpricedWarnings bounds the warn-once set so a caller cycling through
	// model names cannot grow it without limit.
	maxUnpricedWarnings = 1024
)

// Where an operator sets a missing price, by the unit the endpoint is billed
// in. The budget plugin does not read the model database: it prices chat
// tokens from cost_tracking.custom_pricing, so every hint names that key too,
// or an operator who sets one of the two would think the gap is closed.
const (
	tokenPriceHint     = "set model_database.overrides.<model>.pricing (input_per_token / output_per_token, USD per token)"
	audioPriceHint     = "set model_database.overrides.<model>.pricing.input_per_second (USD per second of audio)"
	characterPriceHint = "set model_database.overrides.<model>.pricing.input_per_character (USD per character of text)"
	budgetPriceHint    = "; budgets price chat tokens separately, from cost_tracking.custom_pricing.<model> (input_per_mtok / output_per_mtok, USD per million tokens)"
)

// Plugin is a post-response pipeline plugin that prices every attempt of a
// request in integer micros and fills rc.Billing (FinalizeRequest).
type Plugin struct {
	modelDB          func() *modeldb.ModelDB // getter for hot-reloadable model database
	enabled          bool
	tenantStore      *tenant.Store
	aliasCostFactors map[string]float64 // alias prefix → cost multiplier

	// managedMarkupPercent (managed.markup_percent) is added to the upstream
	// cost of managed attempts, as markupBps basis points.
	managedMarkupPercent float64
	markupBps            int

	pricing  atomic.Pointer[Pricer]
	feesHeld atomic.Pointer[Fees]

	unpricedWarned      sync.Map     // model → struct{}: models already warned about
	unpricedWarnedCount atomic.Int32 // size of unpricedWarned, for the cap
}

// Fees are the landed-cost rates.
type Fees struct {
	// ProcessingFeeBps is managed.processing_fee_bps: card processing as a
	// share of billed revenue.
	ProcessingFeeBps int
	// UpstreamFeeBps is each provider's effective
	// providers.<id>.upstream_fee_bps: what the upstream adds on top of
	// model prices (550 for OpenRouter, 0 for a direct source).
	UpstreamFeeBps map[string]int
}

// New creates a new cost tracking plugin. It prices from the model database
// until SetPricer installs the gateway's injected price lookup.
func New(enabled bool, modelDBGetter func() *modeldb.ModelDB, tenantStore *tenant.Store, aliasCostFactors map[string]float64) *Plugin {
	p := &Plugin{
		modelDB:          modelDBGetter,
		enabled:          enabled,
		tenantStore:      tenantStore,
		aliasCostFactors: aliasCostFactors,
	}
	p.pricing.Store(NewPricer(modelDBGetter, nil))
	p.feesHeld.Store(&Fees{})
	return p
}

// SetManagedMarkup sets the percentage added to managed requests' upstream
// cost (managed.markup_percent): 5 bills 10,500 micros for 10,000.
func (p *Plugin) SetManagedMarkup(percent float64) {
	p.managedMarkupPercent = percent
	p.markupBps = markupBpsOf(percent)
}

// SetFees sets the landed-cost rates. Call it before the gateway serves.
func (p *Plugin) SetFees(f Fees) { p.feesHeld.Store(&f) }

// SetPricer replaces the plugin's prices, for the gateway's injected lookup.
func (p *Plugin) SetPricer(pr *Pricer) {
	if pr != nil {
		p.pricing.Store(pr)
	}
}

// Priced reports whether model can be priced on endpoint (see Pricer.Priced).
func (p *Plugin) Priced(endpoint, model string) bool { return p.pricer().Priced(endpoint, model) }

func (p *Plugin) pricer() *Pricer { return p.pricing.Load() }
func (p *Plugin) fees() *Fees     { return p.feesHeld.Load() }

func (p *Plugin) Name() string  { return "cost" }
func (p *Plugin) Priority() int { return 500 }

// PostPriority runs cost first among the post plugins: budget (40) records
// the spend it computes, and credits (510) charges it.
func (p *Plugin) PostPriority() int          { return 1 }
func (p *Plugin) ShouldSkipOnCacheHit() bool { return true } // No cost to calculate on cache hits.

// ProcessRequest is a no-op for the cost plugin.
func (p *Plugin) ProcessRequest(_ context.Context, _ *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// ProcessResponse prices the request (FinalizeRequest). It keeps the
// metadata keys every consumer reads: cost (billed for a managed request,
// the upstream cost for a BYOK one), cost_base and cost_markup_percent
// (managed), and cost_source.
func (p *Plugin) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if p.enabled {
		p.FinalizeRequest(rc)
	}
	return pipeline.ResultContinue()
}

// priceHintFor names the model-database price an operator sets for an
// endpoint, by the unit the endpoint is billed in.
func priceHintFor(endpoint string) string {
	switch {
	case isSpeechEndpoint(endpoint):
		return characterPriceHint
	case isAudioSecondsEndpoint(endpoint):
		return audioPriceHint
	default:
		return tokenPriceHint
	}
}

// warnUnpriced logs, once per model, that traffic is being recorded without a
// cost. priceHint names the model-database price to set for the endpoint.
func (p *Plugin) warnUnpriced(rc *models.RequestContext, model, priceHint string) {
	if p.unpricedWarnedCount.Load() >= maxUnpricedWarnings {
		return
	}
	if _, seen := p.unpricedWarned.LoadOrStore(model, struct{}{}); seen {
		return
	}
	p.unpricedWarnedCount.Add(1)
	slog.Warn("cost: no pricing for model; its requests are recorded without a cost",
		"model", model,
		"provider", rc.Provider,
		"request_id", rc.RequestID,
		"hint", priceHint+budgetPriceHint,
	)
}
