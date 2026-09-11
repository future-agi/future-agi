package otel

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func bodyPlugin(t *testing.T, include bool) (*Plugin, *captureExporter) {
	t.Helper()
	exp := &captureExporter{}
	p := New(config.OTelConfig{Enabled: true, Exporter: "stdout", IncludeBodies: include})
	p.exporter = exp
	return p, exp
}

func runOnce(p *Plugin, rc *models.RequestContext) map[string]interface{} {
	ctx := context.Background()
	p.ProcessRequest(ctx, rc)
	p.ProcessResponse(ctx, rc)
	return p.exporter.(*captureExporter).Spans()[0].Attributes
}

func rcWithBodies() *models.RequestContext {
	rc := newTestRC()
	content := json.RawMessage(`"my password is hunter2"`)
	rc.Request.Messages = []models.Message{{Role: "user", Content: content}}
	out := json.RawMessage(`"the password is hunter2"`)
	rc.Response.Choices = []models.Choice{{Index: 0, Message: models.Message{Role: "assistant", Content: out}}}
	return rc
}

// input.value / output.value are the keys the platform lifts into its input and
// output columns. A rename here shows up as blank columns, not an error.
func TestBodiesUseTheConventionalKeys(t *testing.T) {
	p, _ := bodyPlugin(t, true)
	attrs := runOnce(p, rcWithBodies())

	in, ok := attrs["input.value"].(string)
	if !ok || !strings.Contains(in, "hunter2") {
		t.Fatalf("input.value = %v", attrs["input.value"])
	}
	if !json.Valid([]byte(in)) {
		t.Error("input.value is not valid JSON")
	}
	out, ok := attrs["output.value"].(string)
	if !ok || !strings.Contains(out, "hunter2") {
		t.Fatalf("output.value = %v", attrs["output.value"])
	}
	if attrs["input.mime_type"] != "application/json" || attrs["output.mime_type"] != "application/json" {
		t.Error("mime types not set")
	}
}

// The knob is the whole privacy contract: off means no content leaves.
func TestBodiesAbsentUnlessEnabled(t *testing.T) {
	p, _ := bodyPlugin(t, false)
	attrs := runOnce(p, rcWithBodies())

	for _, k := range []string{"input.value", "output.value", "input.mime_type", "output.mime_type"} {
		if v, ok := attrs[k]; ok {
			t.Errorf("%s present with include_bodies off: %v", k, v)
		}
	}
}

// Redaction must apply to spans exactly as it applies to the request log,
// otherwise trace export is a way around a configured privacy policy.
func TestBodiesRedactedWithOrgPatterns(t *testing.T) {
	store := tenant.NewStore()
	store.Set("org-1", &tenant.OrgConfig{Privacy: &tenant.PrivacyConfig{
		Enabled:  true,
		Mode:     "patterns",
		Patterns: []*tenant.RedactPatternConfig{{Name: "pw", Pattern: "hunter2"}},
	}})

	p, _ := bodyPlugin(t, true)
	p.SetTenantStore(store)

	rc := rcWithBodies()
	rc.Metadata[tenant.MetadataKeyOrgID] = "org-1"
	attrs := runOnce(p, rc)

	if in := attrs["input.value"].(string); strings.Contains(in, "hunter2") {
		t.Errorf("org pattern not applied to input.value: %s", in)
	}
	if out := attrs["output.value"].(string); strings.Contains(out, "hunter2") {
		t.Errorf("org pattern not applied to output.value: %s", out)
	}
}

// With no org config, the gateway-wide redactor still applies.
func TestBodiesRedactedWithGlobalRedactor(t *testing.T) {
	p, _ := bodyPlugin(t, true)
	p.SetRedactor(privacy.New("patterns", []privacy.PatternConfig{{Name: "pw", Pattern: "hunter2"}}))

	attrs := runOnce(p, rcWithBodies())
	if in := attrs["input.value"].(string); strings.Contains(in, "hunter2") {
		t.Errorf("global redactor not applied: %s", in)
	}
}

// Truncation happens after redaction, and the cap is what makes the order
// observable: a secret straddling the boundary is cut in half by
// truncate-first, stops matching the pattern, and its leading half is
// exported. Redact-first replaces the whole match before any cutting.
func TestBodiesRedactBeforeTruncate(t *testing.T) {
	const secret = "SECRET-1234567890"
	r := privacy.New("patterns", []privacy.PatternConfig{{Name: "s", Pattern: "SECRET-[0-9]{10}"}})

	// Position the secret so the cap falls 12 characters into it.
	filler := strings.Repeat("x", maxBodyAttrBytes-12)
	got := prepareBody(filler+secret, r, "patterns")

	if strings.Contains(got.value, "SECRET-12345") {
		t.Errorf("leading half of the secret survived — truncation ran before redaction")
	}
	if strings.Contains(got.value, secret) {
		t.Error("secret not redacted at all")
	}
	if len(got.value) > maxBodyAttrBytes {
		t.Errorf("value length %d exceeds the cap %d", len(got.value), maxBodyAttrBytes)
	}
}

// Truncation cuts by byte index, so on a non-ASCII body the cap can land in
// the middle of a character. An OTLP attribute is a proto string and
// proto.Marshal fails the whole message on invalid UTF-8, so that one body
// would take its entire batch of unrelated spans with it. The other truncation
// tests use ASCII filler, which is exactly why they cannot see this.
func TestBodiesTruncateOnARuneBoundary(t *testing.T) {
	// Shift the content by one byte at a time: one of these puts a multi-byte
	// character across the cap no matter where the cap happens to sit.
	for pad := 0; pad < 3; pad++ {
		body := strings.Repeat("a", pad) + strings.Repeat("héllo wörld ☃", maxBodyAttrBytes)
		got := prepareBody(body, nil, "")

		if !got.truncated {
			t.Fatalf("pad=%d: body of %d bytes was not truncated", pad, len(body))
		}
		if len(got.value) > maxBodyAttrBytes {
			t.Errorf("pad=%d: value length %d exceeds the cap %d", pad, len(got.value), maxBodyAttrBytes)
		}
		if !utf8.ValidString(got.value) {
			t.Errorf("pad=%d: truncation left invalid UTF-8; the batch this span joins cannot be marshalled", pad)
		}
	}
}

func TestBodiesTruncatedAndFlagged(t *testing.T) {
	p, _ := bodyPlugin(t, true)
	rc := newTestRC()
	huge, _ := json.Marshal(strings.Repeat("x", maxBodyAttrBytes+5000))
	rc.Request.Messages = []models.Message{{Role: "user", Content: json.RawMessage(huge)}}
	rc.Response = nil

	attrs := runOnce(p, rc)
	in := attrs["input.value"].(string)
	if len(in) != maxBodyAttrBytes {
		t.Fatalf("input.value length = %d, want the cap %d", len(in), maxBodyAttrBytes)
	}
	if attrs["agentcc.input_truncated"] != true {
		t.Error("truncation not flagged")
	}
	if attrs["agentcc.input_original_bytes"] == nil {
		t.Error("original size not recorded")
	}
	// Truncated JSON is not parseable; saying so is what stops a consumer trying.
	if _, ok := attrs["input.mime_type"]; ok {
		t.Error("truncated value must not claim to be application/json")
	}
}

// The streaming husk — a response with usage but no choices — must not be
// exported as an empty completion.
func TestBodiesSkipEmptyStreamingResponse(t *testing.T) {
	p, _ := bodyPlugin(t, true)
	rc := rcWithBodies()
	rc.Response = &models.ChatCompletionResponse{Model: "gpt-4o"}

	attrs := runOnce(p, rc)
	if v, ok := attrs["output.value"]; ok {
		t.Errorf("exported an output for a response with no choices: %v", v)
	}
	if _, ok := attrs["input.value"]; !ok {
		t.Error("input should still be captured")
	}
}

// Multimodal endpoints must carry content too. An image span with no prompt is
// exactly as useless as a chat span with no prompt.
func TestBodiesCoverMultimodalEndpoints(t *testing.T) {
	tests := []struct {
		endpoint string
		setup    func(*models.RequestContext)
		wantIn   string
		wantOut  string
	}{
		{
			endpoint: "image",
			setup: func(rc *models.RequestContext) {
				rc.ImageRequest = &models.ImageRequest{Model: "dall-e-3", Prompt: "a red bicycle"}
				rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{URL: "https://img/1.png"}}}
			},
			wantIn:  "a red bicycle",
			wantOut: "https://img/1.png",
		},
		{
			endpoint: "embedding",
			setup: func(rc *models.RequestContext) {
				rc.EmbeddingRequest = &models.EmbeddingRequest{Model: "te-3", Input: json.RawMessage(`"embed me"`)}
				rc.EmbeddingResponse = &models.EmbeddingResponse{Object: "list", Model: "te-3"}
			},
			wantIn:  "embed me",
			wantOut: "list",
		},
		{
			endpoint: "rerank",
			setup: func(rc *models.RequestContext) {
				rc.RerankRequest = &models.RerankRequest{Query: "best bicycle", Documents: []string{"doc"}}
			},
			wantIn: "best bicycle",
		},
		{
			endpoint: "ocr",
			setup: func(rc *models.RequestContext) {
				rc.OCRResponse = &models.OCRResponse{Pages: []models.OCRPage{{Markdown: "# Invoice"}}}
			},
			wantOut: "# Invoice",
		},
		{
			endpoint: "speech",
			setup: func(rc *models.RequestContext) {
				rc.SpeechRequest = &models.SpeechRequest{Model: "tts-1", Input: "read this aloud", Voice: "alloy"}
			},
			wantIn:  "read this aloud",
			wantOut: "binary",
		},
	}

	for _, tt := range tests {
		t.Run(tt.endpoint, func(t *testing.T) {
			p, _ := bodyPlugin(t, true)
			rc := newTestRC()
			rc.EndpointType = tt.endpoint
			rc.Request, rc.Response = nil, nil
			tt.setup(rc)

			attrs := runOnce(p, rc)
			if tt.wantIn != "" {
				in, _ := attrs["input.value"].(string)
				if !strings.Contains(in, tt.wantIn) {
					t.Errorf("input.value = %q, want it to contain %q", in, tt.wantIn)
				}
			}
			if tt.wantOut != "" {
				out, _ := attrs["output.value"].(string)
				if !strings.Contains(out, tt.wantOut) {
					t.Errorf("output.value = %q, want it to contain %q", out, tt.wantOut)
				}
			}
		})
	}
}

// Binary payloads must be described, never carried: one base64 image would
// blow the per-attribute cap on its own and tell a reviewer nothing.
func TestBodiesElideBinaryPayloads(t *testing.T) {
	big := strings.Repeat("A", 500000)
	p, _ := bodyPlugin(t, true)
	rc := newTestRC()
	rc.EndpointType = "image"
	rc.Request, rc.Response = nil, nil
	rc.ImageRequest = &models.ImageRequest{Prompt: "a cat"}
	rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{B64JSON: big}}}

	attrs := runOnce(p, rc)
	out, _ := attrs["output.value"].(string)
	if strings.Contains(out, big) {
		t.Fatal("base64 image was exported on the span")
	}
	if !strings.Contains(out, "b64_json_bytes") {
		t.Error("elided image should still report its size")
	}
	if attrs["agentcc.output_truncated"] == true {
		t.Error("eliding should have kept the payload well under the cap")
	}
}

// The convention has first-class attributes for these endpoints. Emitting them
// is what makes an image or audio span filterable by prompt, size or transcript
// instead of only greppable inside input.value.
func TestEndpointAttributesUseTheConvention(t *testing.T) {
	n := 2
	dims := 1536
	topN := 5

	tests := []struct {
		name     string
		setup    func(*models.RequestContext)
		endpoint string
		want     map[string]interface{}
	}{
		{
			name:     "image",
			endpoint: "image",
			setup: func(rc *models.RequestContext) {
				rc.ImageRequest = &models.ImageRequest{
					Prompt: "a red bicycle", Size: "1024x1024", Quality: "hd", Style: "vivid",
					ResponseFormat: "url", N: &n,
				}
				rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{
					{URL: "https://img/1.png", RevisedPrompt: "a red racing bicycle"},
					{URL: "https://img/2.png"},
				}}
			},
			want: map[string]interface{}{
				"gen_ai.image.prompt":         "a red bicycle",
				"gen_ai.image.size":           "1024x1024",
				"gen_ai.image.quality":        "hd",
				"gen_ai.image.style":          "vivid",
				"gen_ai.image.count":          2,
				"gen_ai.image.output_urls":    "https://img/1.png,https://img/2.png",
				"gen_ai.image.revised_prompt": "a red racing bicycle",
			},
		},
		{
			name:     "embedding",
			endpoint: "embedding",
			setup: func(rc *models.RequestContext) {
				rc.EmbeddingRequest = &models.EmbeddingRequest{Dimensions: &dims}
			},
			want: map[string]interface{}{"gen_ai.embeddings.dimension.count": 1536},
		},
		{
			name:     "rerank",
			endpoint: "rerank",
			setup: func(rc *models.RequestContext) {
				rc.RerankRequest = &models.RerankRequest{Query: "best bicycle", Model: "rerank-1", TopN: &topN}
			},
			want: map[string]interface{}{
				"gen_ai.reranker.query": "best bicycle",
				"gen_ai.reranker.model": "rerank-1",
				"gen_ai.reranker.top_n": 5,
			},
		},
		{
			name:     "speech",
			endpoint: "speech",
			setup: func(rc *models.RequestContext) {
				rc.SpeechRequest = &models.SpeechRequest{Input: "read this aloud", Voice: "alloy"}
			},
			want: map[string]interface{}{"gen_ai.audio.transcript": "read this aloud"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Not gated on include_bodies: these are dimensions, not content.
			p, _ := bodyPlugin(t, false)
			rc := newTestRC()
			rc.EndpointType = tt.endpoint
			rc.Request, rc.Response = nil, nil
			tt.setup(rc)

			attrs := runOnce(p, rc)
			for k, want := range tt.want {
				if got := attrs[k]; got != want {
					t.Errorf("%s = %v (%T), want %v", k, got, got, want)
				}
			}
		})
	}
}

// Inline base64 in chat content is the vision case: there is no separate field
// to leave it in, so carrying it would truncate the prompt away with it.
func TestInlineImageDataElidedFromChat(t *testing.T) {
	b64 := strings.Repeat("QUJD", 100000) // ~400 KB
	p, _ := bodyPlugin(t, true)
	rc := newTestRC()
	content, _ := json.Marshal([]map[string]any{
		{"type": "text", "text": "what is in this picture?"},
		{"type": "image_url", "image_url": map[string]string{"url": "data:image/png;base64," + b64}},
	})
	rc.Request.Messages = []models.Message{{Role: "user", Content: content}}
	rc.Response = nil

	attrs := runOnce(p, rc)
	in, _ := attrs["input.value"].(string)

	if strings.Contains(in, b64) {
		t.Fatal("base64 image carried into the span")
	}
	if !strings.Contains(in, "what is in this picture?") {
		t.Error("the prompt was lost — elision should protect it, not replace it")
	}
	if !strings.Contains(in, "data:image/png;base64,<elided") {
		t.Errorf("elision marker missing: %s", in[:min(len(in), 300)])
	}
	if attrs["agentcc.input_truncated"] == true {
		t.Error("eliding should have kept the body under the cap")
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// The trace viewer renders a conversation from these flattened keys, not from
// input.value. Without them a vision request displays as text with the image
// silently missing.
func TestMessageAttributesRenderable(t *testing.T) {
	p, _ := bodyPlugin(t, true)
	rc := newTestRC()
	content, _ := json.Marshal([]map[string]any{
		{"type": "text", "text": "what colour is this?"},
		{"type": "image_url", "image_url": map[string]string{"url": "https://img/bike.png"}},
	})
	rc.Request.Messages = []models.Message{{Role: "user", Content: content}}
	out, _ := json.Marshal("it is red")
	rc.Response.Choices = []models.Choice{{Message: models.Message{Role: "assistant", Content: out}}}

	attrs := runOnce(p, rc)
	want := map[string]interface{}{
		"llm.input_messages.0.message.role":                                 "user",
		"llm.input_messages.0.message.contents.0.message_content.type":      "text",
		"llm.input_messages.0.message.contents.0.message_content.text":      "what colour is this?",
		"llm.input_messages.0.message.contents.1.message_content.type":      "image_url",
		"llm.input_messages.0.message.contents.1.message_content.image.url": "https://img/bike.png",
		"llm.output_messages.0.message.role":                                "assistant",
		"llm.output_messages.0.message.content":                             "it is red",
	}
	for k, v := range want {
		if got := attrs[k]; got != v {
			t.Errorf("%s = %v, want %v", k, got, v)
		}
	}
}

// A plain text message emits flat content, which is what the viewer expects
// for the common case.
func TestMessageAttributesFlatForPlainText(t *testing.T) {
	p, _ := bodyPlugin(t, true)
	rc := newTestRC()
	c, _ := json.Marshal("just text")
	rc.Request.Messages = []models.Message{{Role: "user", Content: c}}
	rc.Response = nil

	attrs := runOnce(p, rc)
	if attrs["llm.input_messages.0.message.content"] != "just text" {
		t.Errorf("flat content = %v", attrs["llm.input_messages.0.message.content"])
	}
	if _, ok := attrs["llm.input_messages.0.message.contents.0.message_content.text"]; ok {
		t.Error("emitted structured parts for plain text; the viewer would ignore them anyway")
	}
}

// An image too large to carry is described rather than dropped, and only the
// flat summary is emitted — the viewer prefers it over structured parts, so
// sending both would hide the parts and risk the crash that shape once caused.
func TestMessageAttributesMaskOversizedImage(t *testing.T) {
	p, _ := bodyPlugin(t, true)
	rc := newTestRC()
	huge := "data:image/png;base64," + strings.Repeat("A", maxInlineImageBytes+1000)
	content, _ := json.Marshal([]map[string]any{
		{"type": "text", "text": "describe this"},
		{"type": "image_url", "image_url": map[string]string{"url": huge}},
	})
	rc.Request.Messages = []models.Message{{Role: "user", Content: content}}
	rc.Response = nil

	attrs := runOnce(p, rc)
	flat, _ := attrs["llm.input_messages.0.message.content"].(string)
	if !strings.Contains(flat, "describe this") {
		t.Errorf("prompt lost from the masked summary: %q", flat)
	}
	if !strings.Contains(flat, "[image: image/png") {
		t.Errorf("image not described: %q", flat)
	}
	if strings.Contains(flat, strings.Repeat("A", 100)) {
		t.Error("oversized image carried anyway")
	}
	if v, ok := attrs["llm.input_messages.0.message.contents.1.message_content.image.url"]; ok {
		t.Errorf("structured part emitted alongside the flat summary: %v", v)
	}
}

// These attributes carry the same user content input.value does, so they must
// go through the same redaction — otherwise they are a way around it.
func TestMessageAttributesRedacted(t *testing.T) {
	p, _ := bodyPlugin(t, true)
	p.SetRedactor(privacy.New("patterns", []privacy.PatternConfig{{Name: "pw", Pattern: "hunter2"}}))

	rc := newTestRC()
	c, _ := json.Marshal("my password is hunter2")
	rc.Request.Messages = []models.Message{{Role: "user", Content: c}}
	out, _ := json.Marshal("your password is hunter2")
	rc.Response.Choices = []models.Choice{{Message: models.Message{Role: "assistant", Content: out}}}

	attrs := runOnce(p, rc)
	if in, _ := attrs["llm.input_messages.0.message.content"].(string); strings.Contains(in, "hunter2") {
		t.Errorf("input message not redacted: %q", in)
	}
	if o, _ := attrs["llm.output_messages.0.message.content"].(string); strings.Contains(o, "hunter2") {
		t.Errorf("output message not redacted: %q", o)
	}
}

func TestMessageAttributesAbsentUnlessEnabled(t *testing.T) {
	p, _ := bodyPlugin(t, false)
	rc := newTestRC()
	c, _ := json.Marshal("just text")
	rc.Request.Messages = []models.Message{{Role: "user", Content: c}}

	for k := range runOnce(p, rc) {
		if strings.HasPrefix(k, "llm.input_messages") || strings.HasPrefix(k, "llm.output_messages") {
			t.Errorf("message attribute %q emitted with include_bodies off", k)
		}
	}
}
