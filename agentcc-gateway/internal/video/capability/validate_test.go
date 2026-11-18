package capability

import (
	"encoding/json"
	"errors"
	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"math"
	"strings"
	"testing"
)

func base() Request {
	return Request{Model: "byteplus/dreamina-seedance-2-5-260628", Prompt: "a fox", DurationSeconds: 8, Resolution: "1080p", AspectRatio: "16:9", FPS: 24, N: 1}
}
func TestBytePlusRecords(t *testing.T) {
	r := NewRegistry()
	if len(r.Models("byteplus")) != 7 {
		t.Fatal(r.Models("byteplus"))
	}
	for _, tc := range []struct {
		id               string
		min, max         int
		res              int
		last, refs, auto bool
	}{{"dreamina-seedance-2-5-260628", 4, 30, 3, true, true, true}, {"dreamina-seedance-2-0-260128", 4, 15, 4, true, true, true}, {"dreamina-seedance-2-0-fast-260128", 4, 15, 2, true, true, true}, {"dreamina-seedance-2-0-mini-260615", 4, 15, 2, true, true, true}, {"seedance-1-5-pro-251215", 4, 12, 3, true, false, false}, {"seedance-1-0-pro-250528", 2, 12, 3, true, false, false}, {"seedance-1-0-pro-fast-251015", 2, 12, 3, false, false, false}} {
		t.Run(tc.id, func(t *testing.T) {
			m, err := r.Model("byteplus", tc.id)
			if err != nil {
				t.Fatal(err)
			}
			_, last := m.Operations[video.ImageFirstLast]
			_, refs := m.Operations[video.Reference]
			if m.Durations.Min != tc.min || m.Durations.Max != tc.max || len(m.Resolutions) != tc.res || last != tc.last || refs != tc.refs || (m.AutoDuration != nil) != tc.auto {
				t.Fatalf("%+v", m)
			}
		})
	}
}
func TestDurationEdges(t *testing.T) {
	for _, tc := range []struct {
		v  float64
		ok bool
	}{{4, true}, {30, true}, {3, false}, {31, false}, {2.5, false}, {math.NaN(), false}, {math.Inf(1), false}, {-1, true}} {
		r := base()
		r.DurationSeconds = tc.v
		_, err := NewRegistry().Validate(r, "ap-southeast-1")
		if (err == nil) != tc.ok {
			t.Errorf("%v: %v", tc.v, err)
		}
	}
	if _, err := Decode([]byte(`{"model":"x","duration_seconds":"8"}`)); err == nil {
		t.Fatal("string duration")
	}
}
func TestValidate_BoundaryOptions(t *testing.T) {
	for _, tc := range []struct {
		name  string
		value any
		ok    bool
		old   bool
	}{{"frames", 29, true, false}, {"frames", 289, true, false}, {"frames", 30, false, false}, {"frames", 25, false, false}, {"frames", 293, false, false}, {"frames", 29.5, false, false}, {"seed", 42, false, false}, {"seed", -1, true, true}, {"seed", 2147483647, true, true}, {"seed", 2147483648, false, true}, {"seed", -2, false, true}, {"watermark", true, true, false}, {"watermark", "true", false, false}, {"service_tier", "default", true, false}, {"service_tier", "flex", false, false}, {"callback_url", "secret", false, false}, {"draft", true, false, false}} {
		t.Run(tc.name, func(t *testing.T) {
			r := base()
			if tc.old {
				r.Model = "byteplus/seedance-1-5-pro-251215"
			}
			r.ProviderOptions = map[string]any{tc.name: tc.value}
			_, err := NewRegistry().Validate(r, "")
			if (err == nil) != tc.ok {
				t.Fatal(err)
			}
			if err != nil {
				var e *ValidationError
				if !errors.As(err, &e) || e.Param != "provider_options."+tc.name {
					t.Fatal(err)
				}
			}
		})
	}
}
func TestUnknownFields(t *testing.T) {
	for _, tc := range []struct{ body, param string }{{`{"evil":1}`, "evil"}, {`{"inputs":[{"role":"first_frame","secret":1}]}`, "secret"}, {`{"inputs":[{"source":{"token":"x"}}]}`, "token"}, {`{"model":"a"} {}`, "body"}, {`null`, "body"}} {
		_, err := Decode([]byte(tc.body))
		var e *ValidationError
		if !errors.As(err, &e) || e.Param != tc.param {
			t.Fatalf("%s: %v", tc.body, err)
		}
	}
}
func TestRatioResolutionCombos(t *testing.T) {
	for _, tc := range []struct {
		name   string
		change func(*Request)
		ok     bool
	}{{"first adaptive", func(r *Request) {
		r.Inputs = []Input{{Role: video.FirstFrame, MediaType: "image/png", Source: video.Source{Data: "YQ=="}}}
		r.AspectRatio = "adaptive"
	}, true}, {"first fixed", func(r *Request) {
		r.Inputs = []Input{{Role: video.FirstFrame, MediaType: "image/png", Source: video.Source{Data: "YQ=="}}}
	}, false}, {"last only", func(r *Request) {
		r.Inputs = []Input{{Role: video.LastFrame, MediaType: "image/png", Source: video.Source{Data: "YQ=="}}}
	}, false}, {"4k unsupported", func(r *Request) { r.Resolution = "4k" }, false}, {"n2", func(r *Request) { r.N = 2 }, false}, {"wrong fps", func(r *Request) { r.FPS = 30 }, false}, {"audio only25", func(r *Request) {
		r.Inputs = []Input{{Role: video.ReferenceAudio, MediaType: "audio/mpeg", Source: video.Source{Data: "YQ=="}}}
	}, true}, {"audio only20", func(r *Request) {
		r.Model = "byteplus/dreamina-seedance-2-0-260128"
		r.Inputs = []Input{{Role: video.ReferenceAudio, MediaType: "audio/mpeg", Source: video.Source{Data: "YQ=="}}}
	}, false}} {
		t.Run(tc.name, func(t *testing.T) {
			r := base()
			tc.change(&r)
			_, err := NewRegistry().Validate(r, "")
			if (err == nil) != tc.ok {
				t.Fatal(err)
			}
		})
	}
}
func TestPromptLimitUnit(t *testing.T) {
	for _, size := range []int{16384, 16385} {
		r := base()
		r.Prompt = strings.Repeat("x", size)
		_, err := NewRegistry().Validate(r, "")
		if (err == nil) != (size == 16384) {
			t.Fatal(size, err)
		}
	}
	r := base()
	r.Prompt = strings.Repeat("界", 6000)
	if _, err := NewRegistry().Validate(r, ""); err == nil {
		t.Fatal("limit counted chars")
	}
}
func TestDefaultsDisclosed(t *testing.T) {
	r, err := NewRegistry().Normalize(Request{Model: base().Model, Prompt: "fox"}, "")
	if err != nil {
		t.Fatal(err)
	}
	if r.Request.DurationSeconds != 5 || r.Request.Resolution != "720p" || r.Request.N != 1 || r.Request.FPS != 24 || r.Request.Audio == nil || !*r.Request.Audio || r.Request.ProviderOptions["watermark"] != false {
		t.Fatalf("%+v", r.Request)
	}
}
func TestRetiredModelRejected(t *testing.T) {
	for _, id := range []string{"sora", "sora-2", "sora-2-pro", "openai/sora-2", "openai/sora-2-pro"} {
		r := base()
		r.Model = id
		_, err := NewRegistry().Validate(r, "")
		var e *ValidationError
		if !errors.As(err, &e) || e.Code != "unsupported_model" || e.Reason != "retired" {
			t.Fatal(id, err)
		}
	}
}
func TestFingerprintGolden(t *testing.T) {
	a := base()
	a.ProviderOptions = map[string]any{"frames": json.Number("29.0"), "watermark": false}
	a.Inputs = []Input{{Role: video.ReferenceImage, MediaType: "image/png", Source: video.Source{URL: "https://cdn.example/a?token=one"}, Digest: strings.Repeat("a", 64)}}
	b := a
	b.ProviderOptions = map[string]any{"watermark": false, "frames": 29}
	b.Inputs = append([]Input(nil), a.Inputs...)
	b.Inputs[0].Source.URL = "https://cdn.example/b?token=two"
	na, err := NewRegistry().Normalize(a, "")
	if err != nil {
		t.Fatal(err)
	}
	nb, err := NewRegistry().Normalize(b, "")
	if err != nil {
		t.Fatal(err)
	}
	ca, ha, err := Fingerprint(na)
	if err != nil {
		t.Fatal(err)
	}
	_, hb, err := Fingerprint(nb)
	if err != nil || ha != hb {
		t.Fatal("unstable", err, ha, hb)
	}
	const golden = "f4e8b98f1d5d1122b8c0121eeab2443fa59c4a4938afa2e6e49cdd97dcfe01c4"
	if ha != golden {
		t.Fatalf("fingerprint=%s canonical=%s", ha, ca)
	}
	b.Prompt = "changed"
	nb, _ = NewRegistry().Normalize(b, "")
	_, hb, _ = Fingerprint(nb)
	if ha == hb {
		t.Fatal("changed prompt same fingerprint")
	}
}
func TestMediaAndScopeFailClosed(t *testing.T) {
	for _, tc := range []struct {
		name   string
		change func(*Request)
	}{{"both", func(r *Request) {
		r.Inputs = []Input{{Role: video.FirstFrame, MediaType: "image/png", Source: video.Source{URL: "https://example.com", Data: "YQ=="}}}
	}}, {"http", func(r *Request) {
		r.Inputs = []Input{{Role: video.ReferenceImage, MediaType: "image/png", Source: video.Source{URL: "http://example.com"}}}
	}}, {"roles mixed", func(r *Request) {
		r.AspectRatio = "adaptive"
		r.Inputs = []Input{{Role: video.FirstFrame, MediaType: "image/png", Source: video.Source{Data: "YQ=="}}, {Role: video.ReferenceImage, MediaType: "image/png", Source: video.Source{Data: "YQ=="}}}
	}}} {
		t.Run(tc.name, func(t *testing.T) {
			r := base()
			tc.change(&r)
			if _, err := NewRegistry().Validate(r, ""); err == nil {
				t.Fatal("accepted")
			}
		})
	}
	if _, err := NewRegistry().Validate(base(), "other"); err == nil {
		t.Fatal("region")
	}
}

func TestExplicitZeroAndEmptyPrompt(t *testing.T) {
	for _, field := range []string{"duration_seconds", "fps", "n"} {
		if _, err := Decode([]byte(`{"` + field + `":0}`)); err == nil {
			t.Fatal("explicit zero accepted", field)
		}
	}
	r := base()
	r.Prompt = " "
	if _, err := NewRegistry().Validate(r, ""); err == nil {
		t.Fatal("empty text-only prompt")
	}
}
func TestFingerprintRequiresVerifiedMedia(t *testing.T) {
	r := base()
	r.Inputs = []Input{{Role: video.ReferenceImage, MediaType: "image/png", Source: video.Source{URL: "https://example.com/image"}}}
	resolved, err := NewRegistry().Normalize(r, "")
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := Fingerprint(resolved); err == nil {
		t.Fatal("URL used without content digest")
	}
	resolved.Request.Inputs[0].Source = video.Source{Data: "YQ=="}
	resolved.Request.Inputs[0].Digest = strings.Repeat("b", 64)
	if _, _, err := Fingerprint(resolved); err == nil {
		t.Fatal("digest mismatch")
	}
}
