package tariff

import (
	"errors"
	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
	"math"
	"testing"
)

func request(t *testing.T, model, res string, duration float64, ratio string) *capability.Resolved {
	t.Helper()
	r, err := capability.NewRegistry().Normalize(capability.Request{Model: "byteplus/" + model, Prompt: "fox", Resolution: res, DurationSeconds: duration, AspectRatio: ratio}, "")
	if err != nil {
		t.Fatal(err)
	}
	return &r
}
func TestBytePlusRates(t *testing.T) {
	for _, tc := range []struct {
		model, res         string
		noVideo, withVideo float64
	}{{"dreamina-seedance-2-5-260628", "480p", 10.7, 6.4}, {"dreamina-seedance-2-5-260628", "720p", 10.7, 6.4}, {"dreamina-seedance-2-5-260628", "1080p", 11.7, 7}, {"dreamina-seedance-2-0-260128", "720p", 7, 4.3}, {"dreamina-seedance-2-0-260128", "1080p", 7.7, 4.7}, {"dreamina-seedance-2-0-260128", "4k", 4, 2.4}, {"dreamina-seedance-2-0-fast-260128", "480p", 5.6, 3.3}, {"dreamina-seedance-2-0-mini-260615", "720p", 3.5, 2.1}} {
		for _, input := range []bool{false, true} {
			rate, err := BytePlusRate(tc.model, tc.res, input, true)
			want := tc.noVideo
			if input {
				want = tc.withVideo
			}
			if err != nil || rate.USDPerMillion != want || rate.Revision != "byteplus-2026-10-02" || rate.Unit != VideoTokens {
				t.Fatal(tc, input, rate, err)
			}
		}
	}
	for _, tc := range []struct {
		model string
		audio bool
		want  float64
	}{{"seedance-1-5-pro-251215", true, 2.4}, {"seedance-1-5-pro-251215", false, 1.2}, {"seedance-1-0-pro-250528", false, 2.5}, {"seedance-1-0-pro-fast-251015", false, 1}} {
		rate, err := BytePlusRate(tc.model, "720p", false, tc.audio)
		if err != nil || rate.USDPerMillion != tc.want {
			t.Fatal(rate, err)
		}
	}
}
func TestBytePlusTokens(t *testing.T) {
	r := request(t, "dreamina-seedance-2-5-260628", "1080p", 8, "16:9")
	e, err := Estimate(*r, Options{})
	if err != nil {
		t.Fatal(err)
	}
	if e.Quantity != 388800 || e.Micros != 4548960 || e.TariffRevision != Revision || e.Basis != "upper_bound" {
		t.Fatalf("%+v", e)
	}
	r.Request.DurationSeconds = -1
	e, err = Estimate(*r, Options{})
	if err != nil || e.Quantity != 1458000 {
		t.Fatal(e, err)
	}
	r.Request.ProviderOptions["frames"] = int64(29)
	e, err = Estimate(*r, Options{})
	if err != nil || e.Quantity != 58725 {
		t.Fatal(e, err)
	}
	r.Request.ProviderOptions = map[string]any{}
	r.Request.DurationSeconds = 8
	r.Request.Inputs = []video.Input{{Role: video.ReferenceVideo, DurationSeconds: 2}}
	if e, err := Estimate(*r, Options{}); !errors.Is(err, ErrUnpriced) || e != nil {
		t.Fatal(e, err)
	}
	e, err = Estimate(*r, Options{MinTokensWithVideoInput: 1_000_000})
	if err != nil || e.Quantity != 1_000_000 || e.Micros != 7_000_000 {
		t.Fatal(e, err)
	}
	r.Request.Inputs[0].DurationSeconds = 0
	if _, err := Estimate(*r, Options{MinTokensWithVideoInput: 1}); !errors.Is(err, ErrUnpriced) {
		t.Fatal("unknown input duration", err)
	}
}
func TestAdaptiveUpperBound(t *testing.T) {
	r := request(t, "dreamina-seedance-2-5-260628", "1080p", 30, "adaptive")
	e, err := Estimate(*r, Options{})
	if err != nil {
		t.Fatal(err)
	}
	if e.Quantity < 2700*1080*24*30/1024.0 {
		t.Fatal("adaptive under-reserved", e)
	}
}
func TestUnknownTariffNotZero(t *testing.T) {
	for _, tc := range []struct {
		model, res string
		input      bool
	}{{"unknown", "720p", false}, {"dreamina-seedance-2-0-mini-260615", "1080p", false}, {"seedance-1-0-pro-250528", "720p", true}} {
		if _, err := BytePlusRate(tc.model, tc.res, tc.input, false); !errors.Is(err, ErrUnpriced) {
			t.Fatal(tc, err)
		}
	}
	r := request(t, "dreamina-seedance-2-5-260628", "720p", 8, "16:9")
	r.Service = "unknown"
	if e, err := Estimate(*r, Options{}); !errors.Is(err, ErrUnpriced) || e != nil {
		t.Fatal(e, err)
	}
}
func TestPriceRejectsInvalidUsage(t *testing.T) {
	rate, _ := BytePlusRate("dreamina-seedance-2-5-260628", "720p", false, true)
	for _, q := range []float64{-1, math.Inf(1), math.NaN(), math.MaxFloat64} {
		if _, err := rate.Price(q); err == nil {
			t.Fatal(q)
		}
	}
	micros, err := rate.Price(1)
	if err != nil || micros != 11 {
		t.Fatal("reservation must round up", micros, err)
	}
}
