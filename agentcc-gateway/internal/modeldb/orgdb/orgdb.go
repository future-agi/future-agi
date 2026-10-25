// Package orgdb is an org's view of the model database: the org's
// Model Database overrides (model_database.overrides: token limits, prices,
// capability switches) merged on top of the gateway's model database, and its
// Cost Tracking prices (cost_tracking.custom_pricing).
//
// Every reader uses the same match rule (Override, CustomPrice) so the cost
// plugin, the validation plugin, auto routing, /v1/count_tokens,
// /v1/capabilities and /v1/models agree on which entry of the org's applies to
// a model.
//
// Precedence of prices for a call on the org's own provider (BYOK): an
// explicit custom_pricing entry (while cost tracking is on) wins; else the
// model_database override's prices, merged field by field on top of the
// model database's; else the model database's. A managed call is always
// priced on FutureAGI's card: no org entry changes a managed price. Limits
// and capability switches apply to every call of the org (they only narrow
// what the org's own traffic may do).
package orgdb

import (
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// lookup is the org entry of m for model as providerID serves it: the entry
// keyed by model itself, else by model without its first "<prefix>/" ("openai/
// gpt-4o" finds "gpt-4o"), else by "<providerID>/<model>" ("gpt-4o" served by
// openai finds "openai/gpt-4o").
func lookup[T any](m map[string]*T, providerID, model string) *T {
	if len(m) == 0 || model == "" {
		return nil
	}
	if v := m[model]; v != nil {
		return v
	}
	if i := strings.IndexByte(model, '/'); i > 0 && i < len(model)-1 {
		if v := m[model[i+1:]]; v != nil {
			return v
		}
	}
	if providerID != "" {
		if v := m[providerID+"/"+model]; v != nil {
			return v
		}
	}
	return nil
}

// Override is the org's model_database override for model as providerID
// serves it, or nil.
func Override(cfg *tenant.OrgConfig, providerID, model string) *tenant.ModelOverrideOrgConfig {
	if cfg == nil || cfg.ModelDatabase == nil {
		return nil
	}
	return lookup(cfg.ModelDatabase.Overrides, providerID, model)
}

// CustomPrice is the org's cost_tracking.custom_pricing entry for model as
// providerID serves it, or nil. It is the org's price only while cost
// tracking is on.
func CustomPrice(cfg *tenant.OrgConfig, providerID, model string) *tenant.CustomPricing {
	if cfg == nil || cfg.CostTracking == nil || !cfg.CostTracking.Enabled {
		return nil
	}
	return lookup(cfg.CostTracking.CustomPricing, providerID, model)
}

// HasPrice reports whether ov sets an input, output or cached input price.
// Batch prices price nothing: the gateway's batch jobs call the provider's
// regular API.
func HasPrice(ov *tenant.ModelOverrideOrgConfig) bool {
	if ov == nil || ov.Pricing == nil {
		return false
	}
	p := ov.Pricing
	return p.InputPerToken != nil || p.OutputPerToken != nil || p.CachedInputPerToken != nil
}

// PriceCard is base with ov's prices merged on top, field by field: input to
// Prompt, output to Completion, cached input to CacheRead. ok is false when ov
// sets none of them. The card's source is org_custom: the org's own price.
func PriceCard(ov *tenant.ModelOverrideOrgConfig, base models.PriceCard) (models.PriceCard, bool) {
	if !HasPrice(ov) {
		return base, false
	}
	p := ov.Pricing
	c := base
	if p.InputPerToken != nil {
		c.Prompt = *p.InputPerToken
	}
	if p.OutputPerToken != nil {
		c.Completion = *p.OutputPerToken
	}
	if p.CachedInputPerToken != nil {
		c.CacheRead = *p.CachedInputPerToken
	}
	c.Source, c.SnapshotID = models.PriceSourceOrgCustom, ""
	return c, true
}

// CustomCard is the org's custom_pricing entry as a price card (it prices
// input and output per million tokens).
func CustomCard(cp *tenant.CustomPricing) models.PriceCard {
	return models.PriceCard{Prompt: cp.InputPerMTok / 1e6, Completion: cp.OutputPerMTok / 1e6,
		Source: models.PriceSourceOrgCustom}
}

// Info is model's entry as the org sees it through providerID: base (the
// model database's or the catalog's entry, nil when neither knows the model)
// with the org's override merged on top: limits and capability switches
// always, prices only when byok. With byok, a custom_pricing entry then sets
// the input and output prices (it wins over the override's). applied is false,
// and base is returned as is, when the org has nothing for the model. base is
// never modified.
func Info(cfg *tenant.OrgConfig, providerID, model string, base *modeldb.ModelInfo, byok bool) (info *modeldb.ModelInfo, applied bool) {
	ov := Override(cfg, providerID, model)
	var cp *tenant.CustomPricing
	if byok {
		cp = CustomPrice(cfg, providerID, model)
	}
	if ov == nil && cp == nil {
		return base, false
	}
	id := model
	if base != nil && base.ID != "" {
		id = base.ID
	}
	info = modeldb.Overlaid(base, id, toModelOverride(ov, byok))
	if cp != nil {
		info.Pricing.InputPerToken = cp.InputPerMTok / 1e6
		info.Pricing.OutputPerToken = cp.OutputPerMTok / 1e6
	}
	return info, true
}

// Apply is base, filed under id, with ov's token limits and capability
// switches merged on top and no prices: what one of the org's calls is checked
// against, managed or not. base is never modified.
func Apply(base *modeldb.ModelInfo, id string, ov *tenant.ModelOverrideOrgConfig) *modeldb.ModelInfo {
	return modeldb.Overlaid(base, id, toModelOverride(ov, false))
}

// toModelOverride is ov as a model database override; withPrices false
// leaves the prices out (a managed call keeps FutureAGI's).
func toModelOverride(ov *tenant.ModelOverrideOrgConfig, withPrices bool) modeldb.ModelOverride {
	var out modeldb.ModelOverride
	if ov == nil {
		return out
	}
	out.MaxInputTokens, out.MaxOutputTokens = positive(ov.MaxInputTokens), positive(ov.MaxOutputTokens)
	if withPrices && HasPrice(ov) {
		p := ov.Pricing
		out.Pricing = &modeldb.PricingOverride{InputPerToken: p.InputPerToken, OutputPerToken: p.OutputPerToken,
			CachedInputPerToken: p.CachedInputPerToken}
	}
	if c := ov.Capabilities; c != nil {
		out.Capabilities = &modeldb.CapOverride{
			FunctionCalling: c.FunctionCalling, ParallelToolCalls: c.ParallelToolCalls, Vision: c.Vision,
			AudioInput: c.AudioInput, AudioOutput: c.AudioOutput, PDFInput: c.PDFInput, Streaming: c.Streaming,
			ResponseSchema: c.ResponseSchema, SystemMessages: c.SystemMessages, PromptCaching: c.PromptCaching,
			Reasoning: c.Reasoning,
		}
	}
	return out
}

// MaxInputTokens and MaxOutputTokens are ov's token limits, 0 when it sets
// none (a limit of 0 or below is no limit, as in the console).
func MaxInputTokens(ov *tenant.ModelOverrideOrgConfig) int {
	if ov == nil {
		return 0
	}
	return deref(positive(ov.MaxInputTokens))
}

func MaxOutputTokens(ov *tenant.ModelOverrideOrgConfig) int {
	if ov == nil {
		return 0
	}
	return deref(positive(ov.MaxOutputTokens))
}

func positive(p *int) *int {
	if p == nil || *p <= 0 {
		return nil
	}
	return p
}

func deref(p *int) int {
	if p == nil {
		return 0
	}
	return *p
}
