package cost

import (
	"context"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"sync/atomic"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	// CostSourceUnpriced is written to Metadata["cost_source"] when the provider
	// reported token usage but no price is known for the model. The request is
	// recorded without a cost, so spend counters cannot meter it.
	CostSourceUnpriced = "unpriced"

	// maxUnpricedWarnings bounds the warn-once set so a caller cycling through
	// model names cannot grow it without limit.
	maxUnpricedWarnings = 1024
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
	var billedTokens int // tokens the provider reported for the priced endpoints

	switch rc.EndpointType {
	case "embedding":
		if rc.EmbeddingResponse != nil && rc.EmbeddingResponse.Usage != nil {
			billedTokens = rc.EmbeddingResponse.Usage.PromptTokens
			cost, ok = db.CalculateCost(model, rc.EmbeddingResponse.Usage.PromptTokens, 0, opts)
		}
	case "image":
		// Image cost varies by provider; skip for now.
	case "speech", "transcription":
		// Audio cost varies by provider; skip for now.
	case "rerank":
		// Rerank billing varies by provider; skip for now.
	default: // "chat" or empty
		if rc.Response == nil || rc.Response.Usage == nil {
			return pipeline.ResultContinue()
		}
		usage := rc.Response.Usage
		billedTokens = usage.PromptTokens + usage.CompletionTokens
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
	} else if billedTokens > 0 {
		// Tokens were consumed but there is no price for this model. Leaving
		// this silent made the request look free: spend counters stayed at
		// zero and budgets never tripped. Mark the request so the log row and
		// analytics can surface it, and tell the operator once per model.
		rc.Metadata["cost_source"] = CostSourceUnpriced
		p.warnUnpriced(rc, model)
	}

	return pipeline.ResultContinue()
}

// warnUnpriced logs, once per model, that traffic is being recorded without a cost.
func (p *Plugin) warnUnpriced(rc *models.RequestContext, model string) {
	if p.unpricedWarnedCount.Load() >= maxUnpricedWarnings {
		return
	}
	if _, seen := p.unpricedWarned.LoadOrStore(model, struct{}{}); seen {
		return
	}
	p.unpricedWarnedCount.Add(1)
	slog.Warn("cost: no pricing for model; its requests are recorded without a cost and are not counted against budgets",
		"model", model,
		"provider", rc.Provider,
		"request_id", rc.RequestID,
		"hint", "set model_database.overrides.<model>.pricing (input_per_token / output_per_token, USD per token)",
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
