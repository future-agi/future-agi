package eligibility

import (
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The caller's provider filters (rules 6 to 8) are judged on a managed
// candidate's endpoints, which the gateway pins; a BYOK aggregator is
// forwarded the caller's provider object, which it enforces itself; any
// other candidate is judged as one endpoint, its gateway provider. A managed
// candidate with no endpoint data cannot be pinned, so a filter it cannot
// check refuses it: nothing the gateway interprets is accepted and then
// ignored.

// forwarded reports whether the caller's provider object reaches the
// upstream unchanged, so the upstream enforces its filters.
func (c *checker) forwarded() bool { return !c.in.Managed && c.in.Aggregator }

// unpinnable reports whether c is a managed candidate with no endpoint data.
func (c *checker) unpinnable() bool { return c.in.Managed && !c.hasEndpoints }

// checkProviderFilter is rule 6: the caller's provider.only and
// provider.ignore (upstream provider slugs), applied after the org's
// allowances (rule 2), so a caller can only narrow them.
func checkProviderFilter(c *checker) (string, string) {
	p := c.in.Prefs
	if p == nil || (len(p.Only) == 0 && len(p.Ignore) == 0) || c.forwarded() {
		return "", ""
	}
	const reason = "no provider of this model matches the request's provider.only and provider.ignore"
	keep := func(names ...string) bool {
		for _, n := range names {
			if n != "" && equalFoldAny(p.Ignore, n) {
				return false
			}
		}
		if len(p.Only) == 0 {
			return true
		}
		for _, n := range names {
			if n != "" && equalFoldAny(p.Only, n) {
				return true
			}
		}
		return false
	}
	switch {
	case c.hasEndpoints:
		if !c.narrow(func(e *catalog.Endpoint) bool { return keep(e.UpstreamProvider, e.Tag) }) {
			return CodeNoProviderMatches, reason
		}
	case c.unpinnable():
		return CodeNoProviderMatches, reason
	case !keep(c.in.ProviderID):
		return CodeNoProviderMatches, reason
	}
	return "", ""
}

// checkMaxPrice is rule 7: provider.max_price is a hard cap, prompt and
// completion in USD per million tokens, request and image in USD per unit.
// A price that is unknown or dynamic cannot be shown under the cap. The cap
// is compared with the endpoint's or model's list price, as OpenRouter
// compares its own max_price: a managed attempt is billed that price plus
// the managed markup, which the cap does not include.
func checkMaxPrice(c *checker) (string, string) {
	p := c.in.Prefs
	if p == nil || p.MaxPrice == nil || c.forwarded() {
		return "", ""
	}
	capPrice := p.MaxPrice
	switch {
	case c.hasEndpoints:
		if !c.narrow(func(e *catalog.Endpoint) bool { return !e.DynamicPrice && underCap(e.Prices, capPrice) }) {
			return CodePriceCap, "no eligible endpoint is priced under the request's provider.max_price"
		}
	case c.in.Entry != nil && c.in.Entry.Priced():
		if !underCap(c.in.Entry.Prices, capPrice) {
			return CodePriceCap, "model " + c.in.Model + " is priced above the request's provider.max_price"
		}
	default:
		return CodePriceCap, "model " + c.in.Model + "'s price is unknown, so provider.max_price cannot be enforced"
	}
	return "", ""
}

// underCap reports whether prices are within the cap; USD per token is
// compared per million tokens.
func underCap(prices catalog.Prices, cap *models.ProviderMaxPrice) bool {
	within := func(price float64, limit *float64, perMillion bool) bool {
		if limit == nil {
			return true
		}
		if perMillion {
			price *= 1e6
		}
		return price <= *limit+1e-12
	}
	return within(prices.Prompt, cap.Prompt, true) && within(prices.Completion, cap.Completion, true) &&
		within(prices.Request, cap.Request, false) && within(prices.Image, cap.Image, false)
}

// checkQuantizationAndParameters is rule 8: provider.quantizations keeps the
// endpoints whose quantization is listed (unknown is not), and
// provider.require_parameters keeps the endpoints that support every
// parameter the request uses.
func checkQuantizationAndParameters(c *checker) (string, string) {
	p := c.in.Prefs
	if p == nil || c.forwarded() {
		return "", ""
	}
	if len(p.Quantizations) > 0 {
		const reason = "no eligible endpoint serves one of the request's provider.quantizations"
		switch {
		case c.hasEndpoints:
			if !c.narrow(func(e *catalog.Endpoint) bool {
				return e.Quantization != "" && equalFoldAny(p.Quantizations, e.Quantization)
			}) {
				return CodeQuantization, reason
			}
		default:
			return CodeQuantization, reason // the quantization is unknown
		}
	}
	if p.RequireParameters == nil || !*p.RequireParameters {
		return "", ""
	}
	var used [maxRequestParams]string
	params := requestParameters(c.in.Request, used[:0])
	if len(params) == 0 {
		return "", ""
	}
	switch {
	case c.hasEndpoints:
		if !c.narrow(func(e *catalog.Endpoint) bool { return supportsAll(e.SupportedParams, params) }) {
			return CodeMissingParameter, "no eligible endpoint supports every parameter the request uses (" + strings.Join(params, ", ") + ")"
		}
	case c.in.Entry != nil:
		if missing := firstMissing(c.in.Entry.SupportedParameters, params); missing != "" {
			return CodeMissingParameter, "model " + c.in.Model + " does not support " + missing
		}
	default:
		return CodeMissingParameter, "model " + c.in.Model + "'s supported parameters are unknown"
	}
	return "", ""
}

// maxRequestParams bounds requestParameters' result.
const maxRequestParams = 16

// requestParameters appends to out the parameters req uses, in the names
// OpenRouter's supported_parameters use.
func requestParameters(req *models.ChatCompletionRequest, out []string) []string {
	if req == nil {
		return out
	}
	add := func(set bool, name string) {
		if set {
			out = append(out, name)
		}
	}
	add(len(req.Tools) > 0, "tools")
	add(len(req.ToolChoice) > 0, "tool_choice")
	add(req.ResponseFormat != nil, "response_format")
	add(req.ResponseFormat != nil && req.ResponseFormat.Type == "json_schema", "structured_outputs")
	add(req.MaxTokens != nil || req.MaxCompletionTokens != nil, "max_tokens")
	add(req.Temperature != nil, "temperature")
	add(req.TopP != nil, "top_p")
	add(len(req.Stop) > 0, "stop")
	add(req.Seed != nil, "seed")
	add(req.FrequencyPenalty != nil, "frequency_penalty")
	add(req.PresencePenalty != nil, "presence_penalty")
	add(len(req.LogitBias) > 0, "logit_bias")
	add(req.Logprobs != nil, "logprobs")
	add(req.TopLogprobs != nil, "top_logprobs")
	_, reasoning := req.Extra["reasoning"]
	add(reasoning, "reasoning")
	return out
}

func supportsAll(supported, params []string) bool { return firstMissing(supported, params) == "" }

// firstMissing is the first of params not in supported, or "".
func firstMissing(supported, params []string) string {
	for _, p := range params {
		found := false
		for _, s := range supported {
			if s == p {
				found = true
				break
			}
		}
		if !found {
			return p
		}
	}
	return ""
}
