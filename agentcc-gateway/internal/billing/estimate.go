package billing

import (
	"encoding/json"
	"math"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tokenizer"
)

const (
	microsPerUSD = 1_000_000
	bpsDenom     = 10_000
)

// EstimateWorstCaseMicros is the most a chat request can cost on price, in
// micros, with the markup: ceil((input_tokens × prompt + out_cap × completion
// + request_fee) × (10000 + markupBps) / 10000). out_cap is the request's
// max_tokens or max_completion_tokens (the larger when both are set), else
// defaultMaxOut, which the caller sets to min(the model's max output, the
// org's managed_models.default_max_tokens_estimate or
// managed.reserve_default_output_tokens). The credits plugin holds a managed
// request at this amount.
func EstimateWorstCaseMicros(req *models.ChatCompletionRequest, price models.PriceCard, markupBps int, defaultMaxOut int) int64 {
	in := float64(EstimateInputTokens(req))
	out := float64(OutputCap(req, defaultMaxOut))
	usd := in*price.Prompt + out*price.Completion + price.Request
	if !(usd > 0) {
		return 0
	}
	if markupBps < 0 {
		markupBps = 0
	}
	micros := usd * microsPerUSD * float64(bpsDenom+markupBps) / bpsDenom
	// Snap to a millionth of a micro first, so float error cannot push an
	// exact amount over the next whole micro (15130 must not become 15131).
	micros = math.Round(micros*1e6) / 1e6
	if micros >= math.MaxInt64/2 {
		return math.MaxInt64 / 2
	}
	return int64(math.Ceil(micros))
}

// EstimateInputTokens is the request's prompt size in tokens: its messages
// (tokenizer.EstimateTokens) and its tool definitions, about four bytes of
// JSON to a token.
func EstimateInputTokens(req *models.ChatCompletionRequest) int {
	if req == nil {
		return 0
	}
	n := tokenizer.EstimateTokens(req.Messages)
	if len(req.Tools) > 0 {
		if b, err := json.Marshal(req.Tools); err == nil {
			n += (len(b) + 3) / 4
		}
	}
	return n
}

// OutputCap is the most output tokens the request can be billed for: its
// max_tokens or max_completion_tokens, the larger when both are set, else
// defaultMaxOut.
func OutputCap(req *models.ChatCompletionRequest, defaultMaxOut int) int {
	out := 0
	if req != nil {
		if req.MaxTokens != nil && *req.MaxTokens > out {
			out = *req.MaxTokens
		}
		if req.MaxCompletionTokens != nil && *req.MaxCompletionTokens > out {
			out = *req.MaxCompletionTokens
		}
	}
	if out > 0 {
		return out
	}
	if defaultMaxOut < 0 {
		return 0
	}
	return defaultMaxOut
}

// DefaultMaxOut is the output a request with no max_tokens is held for:
// min(modelMaxOut, configured), where a model max output of 0 is unknown and
// leaves configured.
func DefaultMaxOut(modelMaxOut, configured int) int {
	if modelMaxOut > 0 && (configured <= 0 || modelMaxOut < configured) {
		return modelMaxOut
	}
	return configured
}

// MinOrgDefaultMaxOut is the least output an org's
// managed_models.default_max_tokens_estimate holds a managed request with no
// max_tokens for: a quarter of the gateway's 4096 default
// (managed.reserve_default_output_tokens), so an org can still lower its
// holds fourfold for a small balance. A request that wants a smaller hold
// sends max_tokens, which is never floored. The Django contract's minimum
// (api_contracts/gateway/agentcc-admin.schema.json) is the same number.
const MinOrgDefaultMaxOut = 1024

// OrgDefaultMaxOut is an org's stored default_max_tokens_estimate as a hold
// uses it: 0 (unset) when stored is 0 or less, otherwise at least
// MinOrgDefaultMaxOut. A stored value below the floor holds the floor.
func OrgDefaultMaxOut(stored int) int {
	if stored <= 0 {
		return 0
	}
	return max(stored, MinOrgDefaultMaxOut)
}

// HighestCard returns the card in set with the highest worst-case estimate
// for req, and that estimate: a managed request whose upstream may serve it
// from any endpoint in the set is held at the dearest. ok is false
// for an empty set.
func HighestCard(set []models.EndpointRef, req *models.ChatCompletionRequest, markupBps, defaultMaxOut int) (card models.PriceCard, micros int64, ok bool) {
	for i, ref := range set {
		m := EstimateWorstCaseMicros(req, ref.Price, markupBps, defaultMaxOut)
		if i == 0 || m > micros {
			card, micros, ok = ref.Price, m, true
		}
	}
	return card, micros, ok
}

// USDToMicros is usd in whole micros, rounded half up; 0 for a non-positive
// or non-finite amount.
func USDToMicros(usd float64) int64 {
	if !(usd > 0) || math.IsInf(usd, 1) {
		return 0
	}
	m := math.Round(usd*microsPerUSD*1e6) / 1e6
	if m >= math.MaxInt64/2 {
		return math.MaxInt64 / 2
	}
	return int64(math.Floor(m + 0.5))
}

// MicrosToUSD is micros in USD.
func MicrosToUSD(micros int64) float64 { return float64(micros) / microsPerUSD }
