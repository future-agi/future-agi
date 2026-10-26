package cost

import (
	"context"
	"fmt"
	"log/slog"
	"strconv"
	"strings"
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
	// the model database has no price for the model. The request is recorded
	// without a cost. Budgets price requests from their own table, so this flag
	// says nothing about whether a budget counted the request.
	CostSourceUnpriced = "unpriced"

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

// Plugin is a post-response pipeline plugin that calculates per-request cost.
type Plugin struct {
	modelDB          func() *modeldb.ModelDB // getter for hot-reloadable model database
	enabled          bool
	tenantStore      *tenant.Store
	aliasCostFactors map[string]float64 // alias prefix → cost multiplier

	unpricedWarned      sync.Map     // model → struct{}: models already warned about
	unpricedWarnedCount atomic.Int32 // size of unpricedWarned, for the cap
}

// New creates a new cost tracking plugin.
func New(enabled bool, modelDBGetter func() *modeldb.ModelDB, tenantStore *tenant.Store, aliasCostFactors map[string]float64) *Plugin {
	return &Plugin{
		modelDB:          modelDBGetter,
		enabled:          enabled,
		tenantStore:      tenantStore,
		aliasCostFactors: aliasCostFactors,
	}
}

func (p *Plugin) Name() string           { return "cost" }
func (p *Plugin) Priority() int          { return 500 }
func (p *Plugin) ShouldSkipOnCacheHit() bool { return true } // No cost to calculate on cache hits.

// ProcessRequest is a no-op for the cost plugin.
func (p *Plugin) ProcessRequest(_ context.Context, _ *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// ProcessResponse calculates request cost from usage data and stores it in metadata.
func (p *Plugin) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !p.enabled {
		return pipeline.ResultContinue()
	}

	db := p.modelDB()
	if db == nil {
		return pipeline.ResultContinue()
	}

	// Try resolved model first (actual model used), then requested model.
	model := rc.ResolvedModel
	if model == "" {
		model = rc.Model
	}

	// Check for per-org custom pricing.
	if p.tenantStore != nil {
		orgID := rc.Metadata[tenant.MetadataKeyOrgID]
		if orgID != "" {
			orgCfg := p.tenantStore.Get(orgID)
			if orgCfg != nil && orgCfg.CostTracking != nil && orgCfg.CostTracking.Enabled && len(orgCfg.CostTracking.CustomPricing) > 0 {
				if orgCost, done := p.tryOrgPricing(rc, model, orgCfg.CostTracking); done {
					if orgCost > 0 {
						rc.Metadata["cost"] = fmt.Sprintf("%.6f", orgCost)
						rc.Metadata["cost_source"] = "org_custom"
					}
					return pipeline.ResultContinue()
				}
			}
		}
	}

	opts := modeldb.CostOptions{
		Cached: rc.Metadata["cache_status"] == "hit",
	}

	var cost float64
	var ok bool
	// billed is how much the request used, in the unit its endpoint is priced
	// by: tokens, seconds of audio, or characters of text. A positive amount
	// with no price is what goes unpriced.
	var billed float64
	priceHint := tokenPriceHint

	switch rc.EndpointType {
	case "embedding":
		if rc.EmbeddingResponse != nil && rc.EmbeddingResponse.Usage != nil {
			billed = float64(rc.EmbeddingResponse.Usage.PromptTokens)
			cost, ok = db.CalculateCost(model, rc.EmbeddingResponse.Usage.PromptTokens, 0, opts)
		}
	case "image":
		// Image cost varies by provider; skip for now.
	case "transcription", "translation", "transcription_relay":
		priceHint = audioPriceHint
		if seconds, err := strconv.ParseFloat(rc.Metadata["audio_seconds"], 64); err == nil {
			billed = seconds
			cost, ok = db.AudioCost(model, seconds)
		}
	case "speech", "speech_stream", "speech_relay":
		priceHint = characterPriceHint
		if characters, err := strconv.Atoi(rc.Metadata["input_characters"]); err == nil {
			billed = float64(characters)
			cost, ok = db.CharacterCost(model, characters)
		}
	case "rerank":
		// Rerank billing varies by provider; skip for now.
	default: // "chat" or empty
		if rc.Response == nil || rc.Response.Usage == nil {
			return pipeline.ResultContinue()
		}
		usage := rc.Response.Usage
		billed = float64(usage.PromptTokens + usage.CompletionTokens)
		cost, ok = db.CalculateCost(model, usage.PromptTokens, usage.CompletionTokens, opts)
		if !ok && model == rc.ResolvedModel && rc.Model != "" {
			cost, ok = db.CalculateCost(rc.Model, usage.PromptTokens, usage.CompletionTokens, opts)
		}
	}

	if ok {
		// Apply alias-based cost factors from config.
		if alias := rc.Metadata["model_alias"]; alias != "" && len(p.aliasCostFactors) > 0 {
			for prefix, factor := range p.aliasCostFactors {
				if strings.HasPrefix(alias, prefix) {
					cost *= factor
					break
				}
			}
		}
		rc.Metadata["cost"] = fmt.Sprintf("%.6f", cost)
	} else if billed > 0 {
		// The request used something billable but there is no price for this
		// model. Leaving this silent made the request look free in the request
		// log and analytics. Mark the request so they can surface it, and tell
		// the operator once per model.
		rc.Metadata["cost_source"] = CostSourceUnpriced
		p.warnUnpriced(rc, model, priceHint)
	}

	return pipeline.ResultContinue()
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

// tryOrgPricing checks per-org custom pricing for the model.
// Returns (cost, true) if org pricing was found and applied; (0, false) to fall through to global.
func (p *Plugin) tryOrgPricing(rc *models.RequestContext, model string, ct *tenant.CostTrackingConfig) (float64, bool) {
	pricing := ct.CustomPricing[model]
	if pricing == nil && model == rc.ResolvedModel && rc.Model != "" {
		pricing = ct.CustomPricing[rc.Model]
	}
	if pricing == nil {
		return 0, false // No org pricing for this model, fall through to global.
	}

	var promptTokens, completionTokens int
	switch rc.EndpointType {
	case "embedding":
		if rc.EmbeddingResponse != nil && rc.EmbeddingResponse.Usage != nil {
			promptTokens = rc.EmbeddingResponse.Usage.PromptTokens
		}
	default:
		if rc.Response == nil || rc.Response.Usage == nil {
			return 0, true
		}
		promptTokens = rc.Response.Usage.PromptTokens
		completionTokens = rc.Response.Usage.CompletionTokens
	}

	cost := float64(promptTokens)*pricing.InputPerMTok/1_000_000 +
		float64(completionTokens)*pricing.OutputPerMTok/1_000_000
	return cost, true
}
