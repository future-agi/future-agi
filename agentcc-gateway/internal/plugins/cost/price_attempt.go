package cost

import "github.com/futureagi/agentcc-gateway/internal/models"

// PriceAttempt prices one attempt in integer micros.
//
// The upstream cost is the upstream's own reported charge (u.Cost) when it
// sends one; otherwise the card's prices for every token class u used:
// uncached input, cache reads, cache writes, output, reasoning (when the card
// prices it apart from output), web searches and the per-request fee. It is
// rounded half up once.
//
// A managed attempt is billed upstream × (10,000 + markupBps) / 10,000, and
// lands upstream × (10,000 + upstreamFeeBps) / 10,000 plus billed ×
// a.ProcessingFeeBps / 10,000: the processing rate is the one recorded on
// the attempt (FinalizeRequest sets it from managed.processing_fee_bps). A
// BYOK attempt is billed at upstream and lands 0.
//
// source is "provider_reported", the card's source, or "unpriced" with ok
// false when neither a reported cost nor a price covers the attempt.
func PriceAttempt(a *models.Attempt, u *models.Usage, p models.PriceCard, markupBps, upstreamFeeBps int) (upstreamMicros, billedMicros, landedMicros int64, source string, ok bool) {
	if c, reported := reportedCost(u); reported {
		upstreamMicros, source = usdToMicros(c), string(models.CostSourceProviderReported)
	} else if cost, priced := tokenCost(tokensOf(a, u), p); priced {
		upstreamMicros, source = usdToMicros(cost), string(costSourceOf(p.Source))
	} else {
		return 0, 0, 0, string(models.CostSourceUnpriced), false
	}
	managed, processing := false, 0
	if a != nil {
		managed, processing = a.CredentialType == models.CredentialManaged, a.ProcessingFeeBps
	}
	billedMicros, landedMicros = billedAndLanded(upstreamMicros, managed, markupBps, upstreamFeeBps, processing)
	return upstreamMicros, billedMicros, landedMicros, source, true
}

// reportedCost is the charge an upstream reported in u, in USD.
func reportedCost(u *models.Usage) (float64, bool) {
	if u == nil || u.Cost == nil || *u.Cost < 0 {
		return 0, false
	}
	return *u.Cost, true
}

// tokens are the counts one attempt is priced on. Prompt includes the cached
// and cache-write tokens and completion the reasoning tokens, as OpenAI and
// OpenRouter report them; a prompt smaller than its cache tokens is read as
// Anthropic's exclusive input_tokens (tokenCost).
type tokens struct {
	prompt, completion, cached, cacheWrite, reasoning, webSearches int
}

// tokensOf reads u's counts, its token details parsed from the raw fields
// when no one parsed them yet, and takes from a any detail u lacks.
func tokensOf(a *models.Attempt, u *models.Usage) tokens {
	var t tokens
	if u != nil {
		pd, cd := u.PromptDetails, u.CompletionDetails
		if pd == (models.PromptTokensDetails{}) && cd == (models.CompletionTokensDetails{}) &&
			(u.PromptTokensDetails != nil || u.CompletionTokensDetails != nil) {
			parsed := models.Usage{PromptTokensDetails: u.PromptTokensDetails, CompletionTokensDetails: u.CompletionTokensDetails}
			parsed.ParseDetails()
			pd, cd = parsed.PromptDetails, parsed.CompletionDetails
		}
		t = tokens{prompt: u.PromptTokens, completion: u.CompletionTokens,
			cached: pd.CachedTokens, cacheWrite: pd.CacheWriteTokens, reasoning: cd.ReasoningTokens}
		if u.ServerToolUse != nil {
			t.webSearches = u.ServerToolUse.WebSearchRequests
		}
	} else if a != nil {
		t.prompt, t.completion = a.PromptTokens, a.CompletionTokens
	}
	if a != nil {
		t.cached = firstNonZero(t.cached, a.CachedTokens)
		t.cacheWrite = firstNonZero(t.cacheWrite, a.CacheWriteTokens)
		t.reasoning = firstNonZero(t.reasoning, a.ReasoningTokens)
	}
	return t
}

// tokenCost prices t on card p, in USD. A card with no input, output or
// request price cannot price a call.
func tokenCost(t tokens, p models.PriceCard) (float64, bool) {
	if !(p.Prompt > 0 || p.Completion > 0 || p.Request > 0) {
		return 0, false
	}
	cached, cacheWrite := max(0, t.cached), max(0, t.cacheWrite)
	uncached := t.prompt - cached - cacheWrite
	if uncached < 0 {
		// More cache tokens than prompt tokens: the counts are Anthropic's
		// shape, where input_tokens excludes cache reads and writes, so
		// prompt is the uncached count. Clamping would bill the cache
		// tokens as if they were free.
		uncached = t.prompt
	}
	reasoning, output := 0, t.completion
	if p.Reasoning > 0 {
		reasoning = clamp(t.reasoning, t.completion)
		output -= reasoning
	}
	cost := float64(uncached)*p.Prompt +
		float64(cached)*orRate(p.CacheRead, p.Prompt) +
		float64(cacheWrite)*orRate(p.CacheWrite, p.Prompt) +
		float64(output)*p.Completion +
		float64(reasoning)*p.Reasoning +
		float64(t.webSearches)*p.WebSearch +
		p.Request
	return cost, true
}

// costSourceOf is the cost source an attempt priced from a card of source s
// records. Any other card (the catalog's, or one that names no source) is a
// catalog price: the log entry's cost_source enum admits nothing else.
func costSourceOf(s models.PriceSource) models.CostSource {
	switch s {
	case models.PriceSourceModelDB:
		return models.CostSourceModelDB
	case models.PriceSourcePriceCard:
		return models.CostSourcePriceCard
	case models.PriceSourceOrgCustom:
		return models.CostSourceOrgCustom
	default:
		return models.CostSourceCatalog
	}
}

func orRate(rate, fallback float64) float64 {
	if rate > 0 {
		return rate
	}
	return fallback
}

func clamp(v, hi int) int {
	return max(0, min(v, hi))
}

func firstNonZero(a, b int) int {
	if a != 0 {
		return a
	}
	return b
}
