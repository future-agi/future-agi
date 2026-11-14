package capability

import (
	"time"

	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
)

// OpenRouterRevision pins the public OpenRouter video catalog snapshot
// (GET /api/v1/videos/models) read on 2026-11-06. Widening a model's ranges or
// adding a model requires a new evidence-backed revision.
const OpenRouterRevision = "openrouter-2026-11-06"

type openRouterRow struct {
	id                string
	min, max          int
	enum              []int
	defaultDuration   int
	resolutions       []string
	defaultResolution string
	ratios            []string
	lastFrame         bool
	audio             bool
	seed              bool
}

var openRouterRows = []openRouterRow{
	{id: "bytedance/seedance-1-5-pro", min: 4, max: 12, resolutions: []string{"480p", "720p", "1080p"}, ratios: []string{"1:1", "3:4", "9:16", "9:21", "4:3", "16:9", "21:9"}, lastFrame: true, audio: true, seed: true},
	{id: "google/veo-3.1-lite", min: 4, max: 8, enum: []int{4, 6, 8}, defaultDuration: 4, resolutions: []string{"720p", "1080p"}, ratios: []string{"16:9", "9:16"}, lastFrame: true, audio: true, seed: true},
	{id: "kwaivgi/kling-v3.0-std", min: 3, max: 15, resolutions: []string{"720p"}, ratios: []string{"16:9", "9:16", "1:1"}, lastFrame: true, audio: true},
	{id: "alibaba/wan-3.0", min: 2, max: 30, resolutions: []string{"480p", "720p", "1080p"}, ratios: []string{"16:9", "4:3", "1:1", "3:4", "9:16"}, lastFrame: true, audio: true, seed: true},
	{id: "minimax/hailuo-3-max", min: 5, max: 15, resolutions: []string{"480p", "768p"}, defaultResolution: "768p", ratios: []string{"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}, lastFrame: true},
	{id: "runway/gen-4.5", min: 2, max: 10, resolutions: []string{"720p"}, ratios: []string{"16:9", "9:16"}, seed: true},
	// The catalog reports generate_audio:null for Grok: the gateway never sends
	// the flag and never claims the output is silent.
	{id: "x-ai/grok-imagine-video-1.5-lite", min: 1, max: 15, resolutions: []string{"480p", "720p", "1080p"}, ratios: []string{"16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3"}},
}

// OpenRouterSendsAudioFlag reports whether the catalog documents a switchable
// generate_audio flag for the model. Models without it never receive the field.
func OpenRouterSendsAudioFlag(model string) bool {
	for _, row := range openRouterRows {
		if row.id == model {
			return row.audio
		}
	}
	return false
}

// OpenRouter is an aggregator route: proof gathered through it is never
// evidence about the model vendor's native API, auth, billing or retention.
// OpenRouter documents no submit idempotency, job lookup, list or cancel
// operation, so an uncertain submit can only be reconciled by polling a known
// job id and is never resubmitted. Outputs are copied only through the
// authenticated content endpoint; signed bearer URLs are never retained.
func OpenRouter() video.Capabilities {
	c := video.Capabilities{Service: "openrouter", Revision: OpenRouterRevision, Route: video.RouteAggregator, Regions: []string{"global"}, SubmitIdempotency: video.IdempotencyNone, CorrelationLookup: video.LookupNone, CancelSupport: video.CancelNone, OutputACL: video.ACLPrivateToken, BillsOnFailure: video.BillingUnknown, Webhook: video.WebhookNone, PollMinInterval: 5 * time.Second}
	for _, row := range openRouterRows {
		image := video.InputSpec{MinCount: 1, MaxCount: 1, MaxBytes: 10 * 1024 * 1024, MaxPixels: 36_000_000, Formats: []string{"jpeg", "png", "webp"}}
		m := video.ModelCapability{
			ModelID:           row.id,
			Operations:        map[video.Operation]video.OperationSpec{video.TextToVideo: {}, video.ImageFirstFrame: {Roles: []video.Role{video.FirstFrame}, RequiredRoles: []video.Role{video.FirstFrame}}},
			Inputs:            map[video.Role]video.InputSpec{video.FirstFrame: image},
			Durations:         video.IntRange{Min: row.min, Max: row.max, Enum: row.enum},
			DefaultDuration:   row.defaultDuration,
			Resolutions:       row.resolutions,
			DefaultResolution: row.defaultResolution,
			AspectRatios:      row.ratios,
			MaxOutputs:        1,
			PromptLimit:       video.PromptLimit{Unit: "bytes", Max: 16 * 1024},
			Options:           map[string]video.OptionSpec{},
			Tariff:            &video.TariffRef{Unit: video.USD, Revision: OpenRouterRevision},
			Access:            video.AccessDocumented,
			Audio:             video.AudioSpec{Generated: "never"},
		}
		if row.audio {
			m.Audio.Generated = "optional_default_true"
		}
		if row.seed {
			m.Options["seed"] = video.OptionSpec{Type: "int", Min: 0, Max: 2147483647}
		}
		if row.lastFrame {
			m.Inputs[video.LastFrame] = image
			m.Operations[video.ImageFirstLast] = video.OperationSpec{Roles: []video.Role{video.FirstFrame, video.LastFrame}, RequiredRoles: []video.Role{video.FirstFrame, video.LastFrame}}
		}
		c.Models = append(c.Models, m)
	}
	return c
}
