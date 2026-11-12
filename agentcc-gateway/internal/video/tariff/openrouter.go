package tariff

import (
	"fmt"
	"math"

	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
)

// OpenRouterRevision pins the list SKUs published in the OpenRouter video
// catalog (pricing_skus) on 2026-11-06. These are the operator's own BYOK
// supplier prices, used only to reserve budget before submit. Settlement uses
// the USD cost OpenRouter reports for the finished job; a missing or null cost
// leaves the job unsettled and is never treated as zero. Advertised discounts
// are deliberately ignored so the estimate stays an upper bound.
//
// The 2026-11-07 live smoke showed where list SKUs alone under-reserve: Kling
// 3.0 Std and Wan 3.0 billed 5 output seconds for 3s and 2s requests, and
// Seedance billed tokens on its real output (864x496, 97 frames for a 4s 480p
// 16:9 request). Hence minBilledSeconds below, the grid pixel bound in
// dimensions, and tokenHeadroom for frame counts.
const OpenRouterRevision = capability.OpenRouterRevision

// USDRate converts a provider-reported USD charge into microdollars: one
// million USD of usage costs one million USD.
func USDRate(revision string) Rate {
	return Rate{Unit: video.USD, Revision: revision, Currency: "USD", USDPerMillion: 1e6}
}

// USDMicros rounds a USD amount up to whole microdollars after removing binary
// floating-point noise below a nanodollar, so 0.12 is 120000 and not 120001.
func USDMicros(usd float64) (int64, error) {
	if usd < 0 || math.IsNaN(usd) || math.IsInf(usd, 0) || usd*1e6 >= math.MaxInt64 {
		return 0, fmt.Errorf("invalid video usage quantity")
	}
	return int64(math.Ceil(math.Round(usd*1e9) / 1e3)), nil
}

type openRouterPrice struct {
	// perSecond maps resolution to [without audio, with audio] USD per output second.
	perSecond map[string][2]float64
	// perMillionTokens prices Seedance video tokens [without audio, with audio].
	perMillionTokens [2]float64
	perInputImage    float64
	// minBilledSeconds is the shortest duration the route bills.
	minBilledSeconds float64
}

// tokenHeadroom covers output dimensions and frame counts above the nominal
// size for token-priced models.
const tokenHeadroom = 1.15

var openRouterPrices = map[string]openRouterPrice{
	"bytedance/seedance-1-5-pro":       {perMillionTokens: [2]float64{1.2, 2.4}},
	"google/veo-3.1-lite":              {perSecond: map[string][2]float64{"720p": {0.03, 0.05}, "1080p": {0.05, 0.08}}},
	"kwaivgi/kling-v3.0-std":           {perSecond: map[string][2]float64{"720p": {0.084, 0.126}}, minBilledSeconds: 5},
	"alibaba/wan-3.0":                  {perSecond: map[string][2]float64{"480p": {0.05, 0.05}, "720p": {0.1, 0.1}, "1080p": {0.2, 0.2}}, minBilledSeconds: 5},
	"minimax/hailuo-3-max":             {perSecond: map[string][2]float64{"480p": {0.05, 0.05}, "768p": {0.08, 0.08}}},
	"runway/gen-4.5":                   {perSecond: map[string][2]float64{"720p": {0.12, 0.12}}},
	"x-ai/grok-imagine-video-1.5-lite": {perSecond: map[string][2]float64{"480p": {0.02, 0.02}, "720p": {0.03, 0.03}, "1080p": {0.14, 0.14}}, perInputImage: 0.01},
}

func estimateOpenRouter(res capability.Resolved) (*EstimateResult, error) {
	if res.CapabilityRevision != OpenRouterRevision {
		return nil, ErrUnpriced
	}
	price, ok := openRouterPrices[res.ModelID]
	if !ok {
		return nil, fmt.Errorf("%w: openrouter model", ErrUnpriced)
	}
	req := res.Request
	duration := req.DurationSeconds
	if duration <= 0 || math.IsNaN(duration) || math.IsInf(duration, 0) {
		return nil, fmt.Errorf("invalid duration")
	}
	duration = max(duration, price.minBilledSeconds)
	audio := 0
	if req.Audio != nil && *req.Audio {
		audio = 1
	}
	var usd float64
	if price.perSecond != nil {
		rate, ok := price.perSecond[req.Resolution]
		if !ok || rate[audio] <= 0 {
			return nil, fmt.Errorf("%w: openrouter model/resolution combination", ErrUnpriced)
		}
		usd = duration * rate[audio]
	} else {
		width, height, err := dimensions(req.Resolution, req.AspectRatio)
		if err != nil {
			return nil, err
		}
		tokens := math.Ceil(width * height * duration * 24 / 1024 * tokenHeadroom)
		usd = tokens * price.perMillionTokens[audio] / 1e6
	}
	for _, in := range req.Inputs {
		if in.Role == video.FirstFrame || in.Role == video.LastFrame || in.Role == video.ReferenceImage {
			usd += price.perInputImage
		}
	}
	micros, err := USDMicros(usd)
	if err != nil {
		return nil, err
	}
	return &EstimateResult{UsageLine: video.UsageLine{Unit: video.USD, Quantity: float64(micros) / 1e6, Source: video.UsageEstimate, TariffRevision: OpenRouterRevision, Currency: "USD", USD: float64(micros) / 1e6}, Micros: micros, Basis: "upper_bound", Rate: USDRate(OpenRouterRevision)}, nil
}
