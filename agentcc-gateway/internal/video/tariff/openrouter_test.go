package tariff

import (
	"errors"
	"testing"

	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
)

func orResolved(t *testing.T, model string, seconds float64, resolution, ratio string, audio bool, inputs ...video.Input) capability.Resolved {
	t.Helper()
	res, err := capability.NewRegistry().Normalize(capability.Request{Model: "openrouter/" + model, Prompt: "p", DurationSeconds: seconds, Resolution: resolution, AspectRatio: ratio, Audio: &audio, Inputs: inputs}, "")
	if err != nil {
		t.Fatal(err)
	}
	return res
}

// Upper-bound reservations for the seven smoke clips, from the pinned
// 2026-11-06 catalog SKUs (Wan's advertised discount is ignored on purpose),
// the 5s minimum billed duration of Kling and Wan, and Seedance token headroom.
func TestOpenRouterEstimatesForSmokeClips(t *testing.T) {
	var total int64
	for _, tc := range []struct {
		model, resolution string
		seconds           float64
		audio             bool
		micros            int64
	}{
		{"bytedance/seedance-1-5-pro", "480p", 4, false, 55444},
		{"google/veo-3.1-lite", "720p", 4, false, 120000},
		{"kwaivgi/kling-v3.0-std", "720p", 3, false, 420000},
		{"alibaba/wan-3.0", "480p", 2, false, 250000},
		{"minimax/hailuo-3-max", "480p", 5, false, 250000},
		{"runway/gen-4.5", "720p", 2, false, 240000},
		{"x-ai/grok-imagine-video-1.5-lite", "480p", 1, false, 20000},
	} {
		e, err := Estimate(orResolved(t, tc.model, tc.seconds, tc.resolution, "16:9", tc.audio), Options{})
		if err != nil || e.Micros != tc.micros || e.Unit != video.USD || e.TariffRevision != OpenRouterRevision || e.Rate.Unit != video.USD || e.Basis != "upper_bound" {
			t.Fatalf("%s: %+v %v", tc.model, e, err)
		}
		total += e.Micros
	}
	if total != 1355444 || total >= 5_000_000 {
		t.Fatalf("smoke reservation total %d", total)
	}
}
func TestOpenRouterEstimateAudioAndInputs(t *testing.T) {
	off, on := orResolved(t, "google/veo-3.1-lite", 4, "720p", "16:9", false), orResolved(t, "google/veo-3.1-lite", 4, "720p", "16:9", true)
	a, _ := Estimate(off, Options{})
	b, _ := Estimate(on, Options{})
	if a.Micros != 120000 || b.Micros != 200000 {
		t.Fatal(a.Micros, b.Micros)
	}
	seedance, _ := Estimate(orResolved(t, "bytedance/seedance-1-5-pro", 4, "480p", "16:9", true), Options{})
	if seedance.Micros != 110888 {
		t.Fatal(seedance.Micros)
	}
	frame := video.Input{Role: video.FirstFrame, MediaType: "image/png", Source: video.Source{Data: "aW1n"}}
	grok, _ := Estimate(orResolved(t, "x-ai/grok-imagine-video-1.5-lite", 1, "480p", "16:9", false, frame), Options{})
	if grok.Micros != 30000 {
		t.Fatal(grok.Micros)
	}
}
func TestOpenRouterUnpricedFailsClosed(t *testing.T) {
	res := orResolved(t, "runway/gen-4.5", 2, "720p", "16:9", false)
	res.CapabilityRevision = "openrouter-older"
	if e, err := Estimate(res, Options{}); !errors.Is(err, ErrUnpriced) || e != nil {
		t.Fatal(e, err)
	}
	res = orResolved(t, "runway/gen-4.5", 2, "720p", "16:9", false)
	res.ModelID = "unknown/model"
	if _, err := Estimate(res, Options{}); !errors.Is(err, ErrUnpriced) {
		t.Fatal(err)
	}
	res = orResolved(t, "kwaivgi/kling-v3.0-std", 3, "720p", "16:9", false)
	res.Request.Resolution = "1080p"
	if _, err := Estimate(res, Options{}); !errors.Is(err, ErrUnpriced) {
		t.Fatal(err)
	}
	if RevisionFor("openrouter") != OpenRouterRevision || RevisionFor("byteplus") != Revision || RevisionFor("other") != "" {
		t.Fatal("revision table")
	}
}
func TestUSDMicrosRoundsUpWithoutFloatNoise(t *testing.T) {
	for usd, want := range map[float64]int64{0.12: 120000, 0.0461: 46100, 0.046116: 46116, 0.0000001: 1, 0: 0, 1.0000004: 1000001} {
		got, err := USDMicros(usd)
		if err != nil || got != want {
			t.Fatalf("%v: %d %v", usd, got, err)
		}
	}
	for _, bad := range []float64{-0.01} {
		if _, err := USDMicros(bad); err == nil {
			t.Fatal(bad)
		}
	}
	if n, err := USDRate(OpenRouterRevision).Price(0.12); err != nil || n != 120000 {
		t.Fatal(n, err)
	}
}

// The usage.cost OpenRouter reported for each clip in the 2026-11-07 live smoke
// (gateway micros, rounded up). Every reservation must cover what was billed.
func TestOpenRouterEstimatesCoverLiveBilledCosts(t *testing.T) {
	for _, tc := range []struct {
		model, resolution string
		seconds           float64
		billed            int64
	}{
		{"x-ai/grok-imagine-video-1.5-lite", "480p", 1, 19800},
		{"bytedance/seedance-1-5-pro", "480p", 4, 48226},
		{"alibaba/wan-3.0", "480p", 2, 210375},
		{"google/veo-3.1-lite", "720p", 4, 118800},
		{"runway/gen-4.5", "720p", 2, 237600},
		{"minimax/hailuo-3-max", "480p", 5, 247500},
		{"kwaivgi/kling-v3.0-std", "720p", 3, 415800},
	} {
		e, err := Estimate(orResolved(t, tc.model, tc.seconds, tc.resolution, "16:9", false), Options{})
		if err != nil || e.Micros < tc.billed {
			t.Fatalf("%s: reserved %v below billed %d (%v)", tc.model, e, tc.billed, err)
		}
	}
}
