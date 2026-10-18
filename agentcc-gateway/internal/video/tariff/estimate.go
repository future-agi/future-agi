package tariff

import (
	"encoding/json"
	"fmt"
	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
	"math"
	"strconv"
	"strings"
)

// Estimate consumes a validated, normalized request plus ingress-verified media
// metadata. Auto duration and adaptive dimensions use the largest bounded case.
func Estimate(res capability.Resolved, opts Options) (*EstimateResult, error) {
	if res.Service != "byteplus" || res.CapabilityRevision != Revision {
		return nil, ErrUnpriced
	}
	req := res.Request
	duration := req.DurationSeconds
	if duration == -1 {
		if res.Capability.AutoDuration == nil || !res.Capability.AutoDuration.Bounded {
			return nil, ErrUnpriced
		}
		duration = float64(res.Capability.Durations.Max)
	}
	if frames, ok := req.ProviderOptions["frames"]; ok {
		var n float64
		switch v := frames.(type) {
		case int64:
			n = float64(v)
		case int:
			n = float64(v)
		case float64:
			n = v
		case json.Number:
			n, _ = v.Float64()
		default:
			return nil, fmt.Errorf("invalid frames")
		}
		if n < 29 || n > 289 || math.Mod(n-25, 4) != 0 {
			return nil, fmt.Errorf("invalid frames")
		}
		duration = n / float64(req.FPS)
	}
	if duration <= 0 || math.IsNaN(duration) || math.IsInf(duration, 0) || req.FPS <= 0 {
		return nil, fmt.Errorf("invalid duration or fps")
	}
	inputSeconds := 0.0
	hasVideo := false
	for _, in := range req.Inputs {
		if in.Role == video.ReferenceVideo {
			hasVideo = true
			if in.DurationSeconds <= 0 || math.IsNaN(in.DurationSeconds) || math.IsInf(in.DurationSeconds, 0) {
				return nil, fmt.Errorf("%w: verified input video duration required", ErrUnpriced)
			}
			inputSeconds += in.DurationSeconds
		}
	}
	if hasVideo && opts.MinTokensWithVideoInput <= 0 {
		return nil, fmt.Errorf("%w: min_tokens_with_video_input required", ErrUnpriced)
	}
	width, height, err := dimensions(req.Resolution, req.AspectRatio)
	if err != nil {
		return nil, err
	}
	tokens := math.Ceil((inputSeconds + duration) * width * height * float64(req.FPS) / 1024)
	if hasVideo {
		tokens = math.Max(tokens, float64(opts.MinTokensWithVideoInput))
	}
	audio := req.Audio != nil && *req.Audio
	rate, err := BytePlusRate(res.ModelID, req.Resolution, hasVideo, audio)
	if err != nil {
		return nil, err
	}
	micros, err := rate.Price(tokens)
	if err != nil {
		return nil, err
	}
	return &EstimateResult{UsageLine: video.UsageLine{Unit: VideoTokens, Quantity: tokens, Source: video.UsageEstimate, TariffRevision: Revision, Currency: "USD", USD: float64(micros) / 1e6}, Micros: micros, Basis: "upper_bound"}, nil
}
func dimensions(resolution, ratio string) (float64, float64, error) {
	short := map[string]float64{"480p": 480, "720p": 720, "1080p": 1080, "4k": 2160}[resolution]
	if short == 0 {
		return 0, 0, ErrUnpriced
	}
	// 2.5 is the largest accepted image aspect ratio. It also exceeds all fixed
	// output ratios (max 21:9), bounding adaptive output when dimensions are unknown.
	if ratio == "adaptive" {
		return math.Ceil(short * 2.5), short, nil
	}
	a, b, ok := strings.Cut(ratio, ":")
	if !ok {
		return 0, 0, ErrUnpriced
	}
	w, e1 := strconv.ParseFloat(a, 64)
	h, e2 := strconv.ParseFloat(b, 64)
	if e1 != nil || e2 != nil || w <= 0 || h <= 0 || math.IsNaN(w) || math.IsNaN(h) || math.IsInf(w, 0) || math.IsInf(h, 0) {
		return 0, 0, ErrUnpriced
	}
	if w >= h {
		return math.Ceil(short * w / h), short, nil
	}
	return short, math.Ceil(short * h / w), nil
}
