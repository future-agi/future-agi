package cost

import (
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"slices"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

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

// cachedTokensKey records how many prompt tokens a chat request was priced
// at the cached rate for.
const cachedTokensKey = "cost_cached_tokens"

// Where an operator sets a missing price, by the unit the endpoint is billed
// in. The budget plugin does not read the model database: it prices chat
// tokens from cost_tracking.custom_pricing, so every hint names that key too,
// or an operator who sets one of the two would think the gap is closed.
const (
	tokenPriceHint     = "set model_database.overrides.<model>.pricing (input_per_token / output_per_token, USD per token)"
	audioPriceHint     = "set model_database.overrides.<model>.pricing.input_per_second (USD per second of audio)"
	characterPriceHint = "set model_database.overrides.<model>.pricing.input_per_character (USD per character of text)"
	imagePriceHint     = "set model_database.overrides.<model>.pricing.output_per_image (USD per image)"
	queryPriceHint     = "set model_database.overrides.<model>.pricing.input_per_query (USD per query)"
	pagePriceHint      = "set model_database.overrides.<model>.pricing.per_page (USD per page)"
	budgetPriceHint    = "; budgets price chat tokens separately, from cost_tracking.custom_pricing.<model> (input_per_mtok / output_per_mtok, USD per million tokens)"
)

// Decision prices are read only from "decision:" keys (see
// modeldb.DecisionPricing), never from the chat override of the same name.
const decisionPriceHint = "set model_database.overrides.\"decision:<model>\".pricing (input_per_token / output_per_token, USD per token)"

// Plugin is a post-response pipeline plugin that calculates per-request cost.
type Plugin struct {
	modelDB          func() *modeldb.ModelDB // getter for hot-reloadable model database
	enabled          bool
	tenantStore      *tenant.Store
	aliasCostFactors map[string]float64 // alias prefix → cost multiplier
	priceTables      func(orgID, providerID string) string
	// now is the costing clock: time.Now unless a test sets it.
	now func() time.Time
	// providerTypes gives the type a provider's config records, "" for none.
	providerTypes func(orgID, providerID string) string
	// cachedInPrompt says whether a provider's usage counts the prompt tokens
	// it read from its prompt cache inside prompt_tokens.
	cachedInPrompt func(orgID, providerID string) bool

	unpricedWarned      sync.Map     // price hint + model → struct{}: already warned about
	unpricedWarnedCount atomic.Int32 // size of unpricedWarned, for the cap
	usageWarned         sync.Map     // key → struct{}: odd usage already warned about
	usageWarnedCount    atomic.Int32 // size of usageWarned, for the cap
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

// SetPriceTables gives the plugin the name the price table keys a provider's
// models under, as the provider that served the request states it.
func (p *Plugin) SetPriceTables(f func(orgID, providerID string) string) {
	p.priceTables = f
}

// clock is when the plugin costs a request; dated prices are chosen by it.
func (p *Plugin) clock() time.Time {
	if p.now != nil {
		return p.now()
	}
	return time.Now()
}

// SetProviderTypes gives the plugin the type each provider's config records,
// so a reseller configured under another ID is still known by its type.
func (p *Plugin) SetProviderTypes(f func(orgID, providerID string) string) {
	p.providerTypes = f
}

// SetCachedTokensInPrompt gives the plugin, per provider, whether its usage
// counts cached prompt tokens inside prompt_tokens. Only those are priced at
// the cached rate.
func (p *Plugin) SetCachedTokensInPrompt(f func(orgID, providerID string) bool) {
	p.cachedInPrompt = f
}

// providerType is the type rc's provider's config records, or "".
func (p *Plugin) providerType(rc *models.RequestContext) string {
	if p.providerTypes == nil || rc.Provider == "" {
		return ""
	}
	return p.providerTypes(rc.Metadata[tenant.MetadataKeyOrgID], rc.Provider)
}

func (p *Plugin) Name() string               { return "cost" }
func (p *Plugin) Priority() int              { return 500 }
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

	// After org pricing, which is keyed by the name the org configured.
	table := p.tableName(rc)
	model = priceKey(db, model, rc.Provider, table)

	opts := modeldb.CostOptions{At: p.clock()}

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
		priceHint = imagePriceHint
		cost, ok, billed = imageCost(db, model, rc)
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
		priceHint = queryPriceHint
		if rc.RerankResponse != nil {
			units := searchUnits(rc.RerankResponse)
			billed = float64(units)
			cost, ok = db.QueryCost(model, units)
		}
	case "search":
		// A search has no model: the price table lists it under
		// "<search provider>/search", per request.
		priceHint = queryPriceHint
		if rc.SearchResponse != nil && rc.SearchRequest != nil {
			model = rc.SearchRequest.SearchProvider + "/search"
			billed = 1
			cost, ok = db.QueryCost(model, 1)
		}
	case "ocr":
		priceHint = pagePriceHint
		if pages := ocrPages(rc.OCRResponse); pages > 0 {
			billed = float64(pages)
			cost, ok = db.PageCost(model, pages)
		}
	case "genai_embed":
		// The response carries no usage, so a model priced per token cannot
		// be priced here; it is recorded as unpriced rather than as free.
		priceHint = characterPriceHint
		if characters, err := strconv.Atoi(rc.Metadata["input_characters"]); err == nil {
			billed = float64(characters)
			cost, ok = db.CharacterCost(model, characters)
		}
	case "decision":
		// Decision prices live under their own "decision:" keys: a decision
		// model can share a chat model's name at another price.
		if rc.Response == nil || rc.Response.Usage == nil {
			return pipeline.ResultContinue()
		}
		priceHint = decisionPriceHint
		usage := rc.Response.Usage
		// An unpriced decision model is marked unpriced like any other, never
		// recorded as free.
		billed = float64(usage.PromptTokens + usage.CompletionTokens)
		for _, m := range decisionModelNames(rc) {
			if cost, ok = db.CalculateDecisionCost(m, usage.PromptTokens, usage.CompletionTokens); ok {
				break
			}
		}
	default: // "chat" or empty
		if rc.Response == nil || rc.Response.Usage == nil {
			return pipeline.ResultContinue()
		}
		usage := rc.Response.Usage
		billed = float64(usage.PromptTokens + usage.CompletionTokens)
		opts.CachedTokens = p.cachedTokens(rc, model, usage)
		providerType := p.providerType(rc)
		ownKey, keep := resellerPrice(db, model, rc.Provider, table, providerType, opts.At)
		opts.TablePrice = keep
		cost, ok = db.CalculateCost(cmp.Or(ownKey, model), usage.PromptTokens, usage.CompletionTokens, opts)
		if !ok && rc.ResolvedModel != "" && rc.Model != "" {
			fallback := priceKey(db, rc.Model, rc.Provider, table)
			ownKey, opts.TablePrice = resellerPrice(db, fallback, rc.Provider, table, providerType, opts.At)
			cost, ok = db.CalculateCost(cmp.Or(ownKey, fallback), usage.PromptTokens, usage.CompletionTokens, opts)
		}
		if ok && opts.CachedTokens > 0 {
			rc.Metadata[cachedTokensKey] = strconv.Itoa(opts.CachedTokens)
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

// priceKey is the key the price table lists a model under.
//
// The table keys many models by provider, as "mistral/mistral-ocr-latest" or
// "perplexity/sonar", while a request names the bare model, and a lookup
// strips a provider prefix but never adds one.
//
// A provider that resells other vendors' models names them with the vendor's
// prefix, as Perplexity's "google/gemini-3.6-flash". The lookup would strip
// that to the vendor's own price, so the provider's entry
// ("perplexity/google/gemini-3.6-flash") is tried first.
//
// table is the name the provider states for itself, which its ID need not
// match: the table lists new Gemini models only under "gemini/" and
// "vertex_ai/", and a Gemini provider may be named "vertex_ai_global".
func priceKey(db *modeldb.ModelDB, model, provider, table string) string {
	if provider == "" || model == "" {
		return model
	}
	if _, ok := db.Get(model); ok && !strings.Contains(model, "/") {
		return model
	}
	for _, name := range []string{provider, table, tableProviderNames[provider]} {
		if name == "" {
			continue
		}
		if prefixed := name + "/" + model; listsExactly(db, prefixed) {
			return prefixed
		}
	}
	return model
}

// tableProviderNames spells a provider the way the price table keys it, where
// that differs from the gateway's name: "fireworks_ai/accounts/fireworks/models/glm-5p3".
var tableProviderNames = map[string]string{
	"fireworks": "fireworks_ai",
	"together":  "together_ai",
}

// tableName is the name the price table keys rc's provider's models under, or
// "" when the provider states none.
func (p *Plugin) tableName(rc *models.RequestContext) string {
	if p.priceTables == nil || rc.Provider == "" {
		return ""
	}
	return p.priceTables(rc.Metadata[tenant.MetadataKeyOrgID], rc.Provider)
}

// listsExactly says whether the table has an entry under id itself, not
// under id with its prefix stripped.
func listsExactly(db *modeldb.ModelDB, id string) bool {
	m, ok := db.Get(id)
	return ok && m.ID == id
}

// googleTables are the names Google's own providers key the table under.
var googleTables = map[string]bool{"vertex_ai": true, "gemini": true}

// keepsTablePrice says whether a request is priced at the table's own rate
// rather than a dated one. A reseller with its own entry for the model keeps
// its price; Google's own providers, and resellers with no entry of their
// own (which already take Google's price), follow the date. A provider is
// known by its ID, the table name it states, and the type its config records.
func keepsTablePrice(db *modeldb.ModelDB, model, provider, table, configuredType string) bool {
	if provider == "" {
		return false
	}
	m, ok := db.Get(model)
	if !ok {
		return false
	}
	names := []string{provider, table, tableProviderNames[provider], configuredType}
	for _, name := range names {
		if googleTables[name] {
			return false
		}
	}
	base := m.ID[strings.LastIndexByte(m.ID, '/')+1:]
	for _, name := range names {
		if name == "" {
			continue
		}
		if listsExactly(db, name+"/"+base) || listsExactly(db, name+"/google/"+base) {
			return true
		}
	}
	return false
}

// googleFlashKeys are the keys Google's own Gemini 3.6/3.7/3.8 Flash entries
// are listed under. A fixed list, not "has dated rows", so the rule below
// outlives those rows.
var googleFlashKeys = map[string]bool{
	"gemini-3.6-flash":           true,
	"gemini-3.7-flash":           true,
	"gemini-3.8-flash":           true,
	"gemini/gemini-3.6-flash":    true,
	"gemini/gemini-3.7-flash":    true,
	"gemini/gemini-3.8-flash":    true,
	"vertex_ai/gemini-3.6-flash": true,
	"vertex_ai/gemini-3.7-flash": true,
	"vertex_ai/gemini-3.8-flash": true,
}

// ownPriceFrom is when a reseller's own price for those models wins whatever
// it is. Before it, the own price wins only where it equals the price the
// request is billed already, so no bill moves before Google's price does.
var ownPriceFrom = time.Date(2027, time.January, 1, 0, 0, 0, 0, time.UTC)

// resellerPrice says how a chat request is priced when its provider resells
// the model. ownKey is the provider's own entry to bill instead of model, or
// ""; keepTablePrice bills model at the table's own rate, never a dated one.
// Only Google's Gemini Flash keys take the provider's own entry: the first one
// listed with an input and an output price, from ownPriceFrom on, or before it
// when it equals the price in force for model. Anything else is priced as
// keepsTablePrice says.
func resellerPrice(db *modeldb.ModelDB, model, provider, table, configuredType string, at time.Time) (ownKey string, keepTablePrice bool) {
	if !keepsTablePrice(db, model, provider, table, configuredType) {
		return "", false
	}
	m, ok := db.Get(model)
	if !ok {
		return "", false
	}
	if !googleFlashKeys[m.ID] {
		return "", true
	}
	base := m.ID[strings.LastIndexByte(m.ID, '/')+1:]
	for _, name := range []string{provider, table, tableProviderNames[provider], configuredType} {
		if name == "" {
			continue
		}
		for _, key := range []string{name + "/" + base, name + "/google/" + base} {
			own, ok := db.Get(key)
			if !ok || own.ID != key {
				continue
			}
			p := own.PricingAt(at)
			if p.InputPerToken <= 0 || p.OutputPerToken <= 0 {
				continue
			}
			if at.Before(ownPriceFrom) {
				g := m.PricingAt(at)
				if p.InputPerToken != g.InputPerToken || p.OutputPerToken != g.OutputPerToken {
					return "", true
				}
			}
			return key, false
		}
	}
	return "", true
}

// cachedTokens is how many of a chat request's prompt tokens to price at the
// cached rate: the count its usage reports, when the provider that served it
// counts them inside the prompt. A count above the prompt means the upstream
// counts them some other way, so none is discounted.
func (p *Plugin) cachedTokens(rc *models.RequestContext, model string, usage *models.Usage) int {
	if p.cachedInPrompt == nil || rc.Provider == "" || !p.cachedInPrompt(rc.Metadata[tenant.MetadataKeyOrgID], rc.Provider) {
		return 0
	}
	cached := cachedPromptTokens(usage)
	if cached > usage.PromptTokens {
		p.warnOnce("cached\x00"+rc.Provider+"\x00"+model,
			"cost: provider reported more cached prompt tokens than prompt tokens; pricing the whole prompt at the input rate",
			"provider", rc.Provider,
			"model", model,
			"request_id", rc.RequestID,
			"prompt_tokens", usage.PromptTokens,
			"cached_tokens", cached,
		)
		return 0
	}
	return cached
}

// cachedPromptTokens is the prompt_tokens_details.cached_tokens count usage
// carries; 0 when it carries none, or one that is not a whole number of
// tokens.
func cachedPromptTokens(u *models.Usage) int {
	if u == nil || u.PromptTokensDetails == nil {
		return 0
	}
	var details struct {
		CachedTokens int `json:"cached_tokens"`
	}
	if err := json.Unmarshal(*u.PromptTokensDetails, &details); err != nil || details.CachedTokens < 0 {
		return 0
	}
	return details.CachedTokens
}

// warnOnce logs msg the first time key is seen, for up to
// maxUnpricedWarnings keys, so a caller cannot grow the set without limit.
func (p *Plugin) warnOnce(key, msg string, args ...any) {
	if p.usageWarnedCount.Load() >= maxUnpricedWarnings {
		return
	}
	if _, seen := p.usageWarned.LoadOrStore(key, struct{}{}); seen {
		return
	}
	p.usageWarnedCount.Add(1)
	slog.Warn(msg, args...)
}

// imageCost prices an image call: by tokens when the upstream reported them,
// as the gpt-image models do, otherwise per image at the size and quality the
// request asked for. billed is what was used, priced or not.
func imageCost(db *modeldb.ModelDB, model string, rc *models.RequestContext) (cost float64, ok bool, billed float64) {
	resp := rc.ImageResponse
	if resp == nil {
		return 0, false, 0
	}
	if u := resp.Usage; u != nil && u.TotalTokens > 0 {
		textIn, imageIn := u.InputTokens, 0
		if d := u.InputTokensDetails; d != nil {
			textIn, imageIn = d.TextTokens, d.ImageTokens
		}
		cost, ok = db.ImageTokenCost(model, textIn, imageIn, u.OutputTokens)
		return cost, ok, float64(u.TotalTokens)
	}
	n := len(resp.Data)
	if n == 0 {
		return 0, false, 0
	}
	var size, quality string
	if req := rc.ImageRequest; req != nil {
		size, quality = req.Size, req.Quality
	}
	cost, ok = db.ImageCost(model, size, quality, n)
	return cost, ok, float64(n)
}

// searchUnits is how many search units a rerank call is billed for: the
// count the upstream reports, or one call when it reports none.
func searchUnits(resp *models.RerankResponse) int {
	if resp.Meta != nil {
		if units := resp.Meta.BilledUnits["search_units"]; units > 0 {
			return units
		}
	}
	return 1
}

// ocrPages is how many pages an OCR call read.
func ocrPages(resp *models.OCRResponse) int {
	if resp == nil {
		return 0
	}
	if resp.UsageInfo != nil && resp.UsageInfo.PagesProcessed > 0 {
		return resp.UsageInfo.PagesProcessed
	}
	return len(resp.Pages)
}

// warnUnpriced logs, once per model and price to set, that traffic is being
// recorded without a cost. priceHint names the model-database price to set for
// the endpoint. A decision model can share a chat model's name, so each warns
// with its own hint: the decision one is never swallowed by the chat one.
func (p *Plugin) warnUnpriced(rc *models.RequestContext, model, priceHint string) {
	if p.unpricedWarnedCount.Load() >= maxUnpricedWarnings {
		return
	}
	if _, seen := p.unpricedWarned.LoadOrStore(priceHint+"\x00"+model, struct{}{}); seen {
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
	if pricing == nil && rc.EndpointType == "decision" {
		for _, m := range decisionModelNames(rc) {
			if pricing = ct.CustomPricing[m]; pricing != nil {
				break
			}
		}
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

// decisionModelNames lists the names a decision call's model goes by: the
// model the vendor reports, the one it was sent, the one the caller named,
// and the provider-qualified id GET /v1/decisions/models lists and prices
// ("typesafe/jev-latest"), so a bare "jev-latest" is charged the price the
// listing shows.
func decisionModelNames(rc *models.RequestContext) []string {
	names := make([]string, 0, 5)
	add := func(name string) {
		if name != "" && !slices.Contains(names, name) {
			names = append(names, name)
		}
	}
	add(rc.ResolvedModel)
	upstream := ""
	if rc.DecisionRequest != nil {
		upstream = rc.DecisionRequest.UpstreamModel
		add(upstream)
	}
	add(rc.Model)
	if rc.Provider != "" {
		for _, m := range []string{upstream, rc.Model} {
			if m != "" && !strings.HasPrefix(m, rc.Provider+"/") {
				add(rc.Provider + "/" + m)
			}
		}
	}
	return names
}
