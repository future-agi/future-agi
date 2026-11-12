package capability

import (
	"bytes"
	"encoding/base64"
	"errors"
	"mime/multipart"
	"net/textproto"
	"testing"

	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
)

var pngBytes = []byte("\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")

// sdkMultipart reproduces what openai-python's videos.create sends: every
// scalar as a form field and input_reference as a file part whose content type
// httpx guesses from the filename (or octet-stream for raw bytes).
func sdkMultipart(t *testing.T, fields map[string]string, file []byte, fileType string) ([]byte, string) {
	t.Helper()
	var b bytes.Buffer
	w := multipart.NewWriter(&b)
	for _, k := range []string{"model", "prompt", "seconds", "size", "audio", "resolution", "aspect_ratio", "extra"} {
		if v, ok := fields[k]; ok {
			_ = w.WriteField(k, v)
		}
	}
	if file != nil {
		h := textproto.MIMEHeader{}
		h.Set("Content-Disposition", `form-data; name="input_reference"; filename="frame.bin"`)
		h.Set("Content-Type", fileType)
		p, _ := w.CreatePart(h)
		_, _ = p.Write(file)
	}
	_ = w.Close()
	return b.Bytes(), w.FormDataContentType()
}
func TestOpenAIMultipartMapsToGatewayRequest(t *testing.T) {
	body, ct := sdkMultipart(t, map[string]string{"model": "openrouter/x-ai/grok-imagine-video-1.5-lite", "prompt": "a paper boat", "seconds": "4", "size": "1280x720", "audio": "false"}, pngBytes, "application/octet-stream")
	c, err := DecodeOpenAIMultipart(body, ct)
	if err != nil {
		t.Fatal(err)
	}
	req, err := c.Request()
	if err != nil {
		t.Fatal(err)
	}
	if req.Model != "openrouter/x-ai/grok-imagine-video-1.5-lite" || req.Prompt != "a paper boat" || req.DurationSeconds != 4 || req.Resolution != "720p" || req.AspectRatio != "16:9" || req.Audio == nil || *req.Audio {
		t.Fatalf("%+v", req)
	}
	if len(req.Inputs) != 1 || req.Inputs[0].Role != video.FirstFrame || req.Inputs[0].MediaType != "image/png" || req.Inputs[0].Source.Data != base64.StdEncoding.EncodeToString(pngBytes) {
		t.Fatalf("%+v", req.Inputs)
	}
}
func TestOpenAIMultipartRejectsUnknownRepeatedAndNonImage(t *testing.T) {
	body, ct := sdkMultipart(t, map[string]string{"model": "m", "prompt": "p", "extra": "x"}, nil, "")
	var ve *ValidationError
	if _, err := DecodeOpenAIMultipart(body, ct); !errors.As(err, &ve) || ve.Code != "unknown_field" || ve.Param != "extra" {
		t.Fatal(err)
	}
	body, ct = sdkMultipart(t, map[string]string{"model": "m", "prompt": "p"}, []byte("not an image at all"), "image/png")
	if _, err := DecodeOpenAIMultipart(body, ct); !errors.As(err, &ve) || ve.Param != "input_reference" {
		t.Fatal(err)
	}
	var b bytes.Buffer
	w := multipart.NewWriter(&b)
	_ = w.WriteField("model", "a")
	_ = w.WriteField("model", "b")
	_ = w.Close()
	if _, err := DecodeOpenAIMultipart(b.Bytes(), w.FormDataContentType()); !errors.As(err, &ve) || ve.Param != "model" {
		t.Fatal(err)
	}
	if _, err := DecodeOpenAIMultipart(b.Bytes(), "multipart/form-data"); err == nil {
		t.Fatal("missing boundary accepted")
	}
}
func TestOpenAIJSONShape(t *testing.T) {
	if !IsOpenAIJSON([]byte(`{"model":"m","seconds":"4"}`)) || IsOpenAIJSON([]byte(`{"model":"m","duration_seconds":4}`)) {
		t.Fatal("detection")
	}
	c, err := DecodeOpenAIJSON([]byte(`{"model":"openrouter/runway/gen-4.5","prompt":"p","seconds":2,"size":"720x1280","input_reference":{"image_url":"data:image/png;base64,` + base64.StdEncoding.EncodeToString(pngBytes) + `"}}`))
	if err != nil {
		t.Fatal(err)
	}
	req, err := c.Request()
	if err != nil || req.DurationSeconds != 2 || req.Resolution != "720p" || req.AspectRatio != "9:16" || len(req.Inputs) != 1 || req.Inputs[0].MediaType != "image/png" {
		t.Fatalf("%+v %v", req, err)
	}
	for _, body := range []string{
		`{"model":"m","seconds":"4","duration_seconds":4}`,
		`{"model":"m","seconds":"4","input_reference":{"file_id":"file-1"}}`,
		`{"model":"m","seconds":"4","input_reference":{"image_url":"http://insecure.example/a.png"}}`,
		`{"model":"m","seconds":"4","audio":"yes"}`,
		`{"model":"m","seconds":"4","seconds":"12"}`,
		`{"model":"m","seconds":"4","input_reference":{"image_url":"https://a.example/x.png","image_url":"https://b.example/x.png"}}`,
		`[]`,
	} {
		if _, err := DecodeOpenAIJSON([]byte(body)); err == nil {
			t.Fatalf("accepted %s", body)
		}
	}
}
func TestOpenAISizeMapping(t *testing.T) {
	for size, want := range map[string][2]string{"1280x720": {"720p", "16:9"}, "720x1280": {"720p", "9:16"}, "854x480": {"480p", "16:9"}, "480x480": {"480p", "1:1"}, "1920x1080": {"1080p", "16:9"}, "1366x768": {"768p", "16:9"}, "720x720": {"720p", "1:1"}} {
		r, a, err := ParseSize(size)
		if err != nil || r != want[0] || a != want[1] {
			t.Fatalf("%s: %s %s %v", size, r, a, err)
		}
	}
	// Sora-only sizes and arbitrary sizes are refused, not rounded to another paid tier.
	// Near-canonical sizes are refused too: 1283x720 must not become 1280x720.
	for _, size := range []string{"1792x1024", "1024x1792", "1000x700", "1283x720", "1281x720", "848x480", "0x720", "big", ""} {
		if _, _, err := ParseSize(size); err == nil {
			t.Fatalf("accepted %q", size)
		}
	}
	for _, tc := range [][3]string{{"480p", "16:9", "854x480"}, {"720p", "9:16", "720x1280"}, {"768p", "16:9", "1366x768"}, {"1080p", "1:1", "1080x1080"}, {"720p", "adaptive", ""}} {
		if got := SizeFor(tc[0], tc[1]); got != tc[2] {
			t.Fatalf("%v: %s", tc, got)
		}
	}
	if _, err := (OpenAICreate{Model: "m", Size: "1280x720", Resolution: "480p"}).Request(); err == nil {
		t.Fatal("conflicting size and resolution accepted")
	}
	if _, err := (OpenAICreate{Model: "m", Seconds: "4.5"}).Request(); err == nil {
		t.Fatal("fractional seconds accepted")
	}
}

// Every pinned OpenRouter model normalizes with its catalog defaults and
// rejects values outside the 2026-11-06 catalog snapshot.
func TestOpenRouterCapabilitiesNormalize(t *testing.T) {
	r := NewRegistry()
	off := false
	for _, tc := range []struct {
		model, resolution string
		seconds           float64
	}{{"bytedance/seedance-1-5-pro", "480p", 4}, {"google/veo-3.1-lite", "720p", 4}, {"kwaivgi/kling-v3.0-std", "720p", 3}, {"alibaba/wan-3.0", "480p", 2}, {"minimax/hailuo-3-max", "480p", 5}, {"runway/gen-4.5", "720p", 2}, {"x-ai/grok-imagine-video-1.5-lite", "480p", 1}} {
		res, err := r.Normalize(Request{Model: "openrouter/" + tc.model, Prompt: "p", DurationSeconds: tc.seconds, Resolution: tc.resolution, Audio: &off}, "")
		if err != nil || res.Service != "openrouter" || res.ModelID != tc.model || res.Region != "global" || res.Request.FPS != 0 {
			t.Fatalf("%s: %+v %v", tc.model, res, err)
		}
		if below := res.Capability.Durations.Min - 1; below > 0 {
			if _, err = r.Normalize(Request{Model: "openrouter/" + tc.model, Prompt: "p", DurationSeconds: float64(below)}, ""); err == nil {
				t.Fatalf("%s: below minimum duration accepted", tc.model)
			}
		}
		if _, err = r.Normalize(Request{Model: "openrouter/" + tc.model, Prompt: "p", DurationSeconds: float64(res.Capability.Durations.Max + 1)}, ""); err == nil {
			t.Fatalf("%s: above maximum duration accepted", tc.model)
		}
		if _, err = r.Normalize(Request{Model: "openrouter/" + tc.model, Prompt: "p"}, ""); err != nil {
			t.Fatalf("%s: catalog defaults invalid: %v", tc.model, err)
		}
	}
	if _, err := r.Normalize(Request{Model: "openrouter/google/veo-3.1-lite", Prompt: "p", DurationSeconds: 5}, ""); err == nil {
		t.Fatal("veo duration outside enum accepted")
	}
	if _, err := r.Normalize(Request{Model: "openrouter/runway/gen-4.5", Prompt: "p", Audio: func() *bool { b := true; return &b }()}, ""); err == nil {
		t.Fatal("audio accepted for a model without generated audio")
	}
	if _, err := r.Normalize(Request{Model: "openrouter/openai/sora-2", Prompt: "p"}, ""); err == nil {
		t.Fatal("retired Sora accepted through OpenRouter")
	}
	if got := r.Services(); len(got) != 2 || got[0] != "byteplus" || got[1] != "openrouter" {
		t.Fatal(got)
	}
	if r.services["openrouter"].Route != video.RouteAggregator || r.services["byteplus"].Route != video.RouteNative {
		t.Fatal("route kinds")
	}
}
