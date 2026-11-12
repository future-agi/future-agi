package otel

import (
	"bytes"
	"encoding/json"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Synthetic markers are assembled here, never obtained from provider traffic.
func c26Marker() string       { return "c26-" + "private" + "@" + "example.invalid" }
func c26QuotedMarker() string { return "c26-" + "private\"\n" + "value" }
func c26NumberMarker() string { return "739" + "215" }
func c26EscapedMarker() string {
	return strings.ReplaceAll(c26Marker(), "@", string(rune(92))+"u0040")
}

func c26JSON(t *testing.T, value any) []byte {
	t.Helper()
	b, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// Snapshot the shared content and policy inputs without unrelated runtime state.
func c26Snapshot(t *testing.T, rc *models.RequestContext) []byte {
	t.Helper()
	return c26JSON(t, []any{
		rc.Request, rc.Response, rc.ImageRequest, rc.ImageResponse,
		rc.TranscriptionReq, rc.TranscriptionResp, rc.TranslationReq, rc.TranslationResp,
		rc.SpeechRequest, rc.RerankRequest, rc.RerankResponse, rc.SearchRequest,
		rc.Metadata, rc.CallerExtras, rc.RequestHeaders, rc.Privacy,
	})
}

func c26Policy(p *Plugin, rc *models.RequestContext, source, mode string) {
	patterns := []privacy.PatternConfig{
		{Name: "marker", Pattern: regexp.QuoteMeta(c26Marker())},
		{Name: "quoted", Pattern: regexp.QuoteMeta(c26QuotedMarker())},
		{Name: "number", Pattern: c26NumberMarker()},
	}
	rc.Metadata[tenant.MetadataKeyOrgID] = "c26-org"
	// A caller cannot downgrade the selected org/global redactor.
	rc.Metadata["privacy_mode"] = privacy.ModeNone
	if mode == "absent" {
		p.SetRedactor(nil)
		return
	}
	if source == "org" {
		orgPatterns := make([]*tenant.RedactPatternConfig, len(patterns))
		for i, pattern := range patterns {
			orgPatterns[i] = &tenant.RedactPatternConfig{Name: pattern.Name, Pattern: pattern.Pattern}
		}
		store := tenant.NewStore()
		store.Set("c26-org", &tenant.OrgConfig{Privacy: &tenant.PrivacyConfig{
			Enabled: true, Mode: mode, ContentMode: string(rc.Privacy.ContentMode), Patterns: orgPatterns,
		}})
		p.SetTenantStore(store)
		p.SetRedactor(privacy.New(privacy.ModeNone, nil))
	} else {
		p.SetRedactor(privacy.New(mode, patterns))
	}
}

// Observe the actual exporter and its wire representation, not an internal copy.
func c26Export(t *testing.T, p *Plugin, rc *models.RequestContext) map[string]any {
	t.Helper()
	runOnce(p, rc)
	spans := p.exporter.(*captureExporter).Spans()
	if len(spans) != 1 {
		t.Fatalf("exported %d spans, want one", len(spans))
	}
	var wire struct {
		Attributes map[string]any `json:"attributes"`
	}
	if err := json.Unmarshal(c26JSON(t, spans[0]), &wire); err != nil {
		t.Fatal(err)
	}
	return wire.Attributes
}

func c26Metadata(t *testing.T, attrs map[string]any, rc *models.RequestContext) {
	t.Helper()
	for key, want := range map[string]any{
		"gen_ai.request.model": rc.Model,
		"agentcc.request_id":   rc.RequestID,
		"user.id":              rc.UserID,
		"session.id":           rc.SessionID,
		"gen_ai.provider.name": rc.Provider,
	} {
		if attrs[key] != want {
			t.Errorf("metadata %s = %v, want %v", key, attrs[key], want)
		}
	}
	if _, ok := attrs["gen_ai.client.operation.duration"].(float64); !ok {
		t.Error("duration metadata missing")
	}
	if rc.Response != nil && rc.Response.Usage != nil {
		if attrs["gen_ai.usage.input_tokens"] != float64(rc.Response.Usage.PromptTokens) ||
			attrs["gen_ai.usage.output_tokens"] != float64(rc.Response.Usage.CompletionTokens) {
			t.Error("token metadata changed")
		}
	}
}

func c26Masked(t *testing.T, value string) {
	t.Helper()
	if !strings.Contains(value, "[REDACTED") {
		t.Errorf("missing masking indication: %q", value)
	}
	// Decode a JSON document when present so unicode/quote escapes cannot hide a leak.
	var decoded any
	if json.Unmarshal([]byte(value), &decoded) == nil {
		value = string(c26JSON(t, decoded))
	}
	for _, marker := range []string{c26Marker(), c26QuotedMarker(), c26NumberMarker()} {
		encoded := c26JSON(t, marker)
		if strings.Contains(value, marker) || bytes.Contains([]byte(value), encoded[1:len(encoded)-1]) {
			t.Errorf("sensitive marker survived export: %q", value)
		}
	}
}

type c26EndpointFixture struct {
	name, key     string
	setup         func(*models.RequestContext) string
	metadataKey   string
	metadataValue any
}

func c26EndpointFixtures() []c26EndpointFixture {
	return []c26EndpointFixture{
		{"image_prompt", "gen_ai.image.prompt", func(rc *models.RequestContext) string {
			rc.EndpointType = "image"
			rc.ImageRequest = &models.ImageRequest{Model: rc.Model, Prompt: c26QuotedMarker(), Size: "1024x1024"}
			return c26QuotedMarker()
		}, "gen_ai.image.size", "1024x1024"},
		{"image_revised_prompt", "gen_ai.image.revised_prompt", func(rc *models.RequestContext) string {
			rc.EndpointType = "image"
			rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{RevisedPrompt: c26QuotedMarker()}}}
			return c26QuotedMarker()
		}, "", nil},
		{"transcription", "gen_ai.audio.transcript", func(rc *models.RequestContext) string {
			rc.EndpointType = "transcription"
			rc.TranscriptionReq = &models.TranscriptionRequest{Language: "en"}
			rc.TranscriptionResp = &models.TranscriptionResponse{Text: c26QuotedMarker()}
			rc.Metadata["audio_seconds"] = "1.5"
			return c26QuotedMarker()
		}, "gen_ai.audio.duration_secs", float64(1.5)},
		{"translation", "gen_ai.audio.transcript", func(rc *models.RequestContext) string {
			rc.EndpointType = "translation"
			rc.TranslationResp = &models.TranslationResponse{Text: c26QuotedMarker()}
			return c26QuotedMarker()
		}, "", nil},
		{"speech", "gen_ai.audio.transcript", func(rc *models.RequestContext) string {
			rc.EndpointType = "speech"
			rc.SpeechRequest = &models.SpeechRequest{Input: c26QuotedMarker()}
			rc.Metadata["response_content_type"] = "audio/mpeg"
			return c26QuotedMarker()
		}, "gen_ai.audio.mime_type", "audio/mpeg"},
		{"speech_stream", "gen_ai.audio.transcript", func(rc *models.RequestContext) string {
			rc.EndpointType = "speech_stream"
			rc.SpeechRequest = &models.SpeechRequest{Input: c26QuotedMarker()}
			return c26QuotedMarker()
		}, "", nil},
		{"rerank", "gen_ai.reranker.query", func(rc *models.RequestContext) string {
			rc.EndpointType = "rerank"
			n := 3
			rc.RerankRequest = &models.RerankRequest{Query: c26QuotedMarker(), Model: rc.Model, TopN: &n}
			return c26QuotedMarker()
		}, "gen_ai.reranker.top_n", float64(3)},
		{"search_string", "gen_ai.retrieval.query", func(rc *models.RequestContext) string {
			rc.EndpointType = "search"
			raw := `"` + c26EscapedMarker() + `"`
			rc.SearchRequest = &models.SearchRequest{QueryRaw: json.RawMessage(raw), MaxResults: 4}
			return raw
		}, "gen_ai.retrieval.top_k", float64(4)},
		{"search_array", "gen_ai.retrieval.query", func(rc *models.RequestContext) string {
			rc.EndpointType = "search"
			raw := `["ordinary","` + c26EscapedMarker() + `"]`
			rc.SearchRequest = &models.SearchRequest{QueryRaw: json.RawMessage(raw), MaxResults: 4}
			return raw
		}, "gen_ai.retrieval.top_k", float64(4)},
	}
}

func TestC26PrivacyEndpointAttributes(t *testing.T) {
	for _, source := range []string{"org", "global"} {
		for _, mode := range []string{privacy.ModeFull, privacy.ModePatterns} {
			for _, contentMode := range []models.ContentMode{models.ContentModeFull, models.ContentModeMetadata, models.ContentModeNone} {
				for _, fixture := range c26EndpointFixtures() {
					t.Run(source+"/"+mode+"/"+string(contentMode)+"/"+fixture.name, func(t *testing.T) {
						// Endpoint attributes export even when body capture is disabled.
						p, _ := bodyPlugin(t, false)
						t.Cleanup(p.Close)
						rc := newTestRC()
						rc.Request = nil
						rc.Privacy.ContentMode = contentMode
						fixture.setup(rc)
						c26Policy(p, rc, source, mode)
						before := c26Snapshot(t, rc)
						attrs := c26Export(t, p, rc)
						value, ok := attrs[fixture.key].(string)
						if !ok {
							t.Fatalf("missing %s", fixture.key)
						}
						c26Masked(t, value)
						if fixture.metadataKey != "" && attrs[fixture.metadataKey] != fixture.metadataValue {
							t.Errorf("endpoint metadata changed: %s = %v", fixture.metadataKey, attrs[fixture.metadataKey])
						}
						c26Metadata(t, attrs, rc)
						if _, ok := attrs["input.value"]; ok {
							t.Error("include_bodies=false exported a body")
						}
						if !bytes.Equal(before, c26Snapshot(t, rc)) {
							t.Error("live endpoint context mutated")
						}
					})
				}
			}
		}
	}
}

func TestC26PrivacyEndpointDisabledControls(t *testing.T) {
	for _, mode := range []string{privacy.ModeNone, "absent"} {
		for _, contentMode := range []models.ContentMode{models.ContentModeFull, models.ContentModeMetadata, models.ContentModeNone} {
			for _, fixture := range c26EndpointFixtures() {
				t.Run(mode+"/"+string(contentMode)+"/"+fixture.name, func(t *testing.T) {
					p, _ := bodyPlugin(t, false)
					t.Cleanup(p.Close)
					rc := newTestRC()
					rc.Request = nil
					rc.Privacy.ContentMode = contentMode
					want := fixture.setup(rc)
					c26Policy(p, rc, "global", mode)
					attrs := c26Export(t, p, rc)
					if attrs[fixture.key] != want {
						t.Errorf("disabled export changed %s: %v", fixture.key, attrs[fixture.key])
					}
					c26Metadata(t, attrs, rc)
				})
			}
		}
	}
}

func TestC26PrivacyToolArguments(t *testing.T) {
	const benign = `{"count":9007199254740993,"ok":true}`
	fixtures := []struct {
		name, args string
		invalid    bool
	}{
		{"escaped_value", `{"value":"` + c26EscapedMarker() + `"}`, false},
		{"escaped_key_array", `{"` + c26EscapedMarker() + `":[{"value":"` + c26EscapedMarker() + `"}]}`, false},
		{"quoted_newline", `{"value":` + string(c26JSON(t, c26QuotedMarker())) + `}`, false},
		{"number", `{"value":` + c26NumberMarker() + `}`, false},
		{"partial", `{"value":"` + c26EscapedMarker(), true},
		{"trailing", `{"ok":true} {"value":"` + c26EscapedMarker() + `"}`, true},
	}
	for _, source := range []string{"org", "global"} {
		for _, shape := range []string{"request_response", "response_only_stream"} {
			for _, fixture := range fixtures {
				t.Run(source+"/"+shape+"/"+fixture.name, func(t *testing.T) {
					p, _ := bodyPlugin(t, true)
					t.Cleanup(p.Close)
					rc := newTestRC()
					rc.Privacy.ContentMode = models.ContentModeFull
					msg := models.Message{Role: "assistant", ToolCalls: []models.ToolCall{
						{ID: "sensitive-call", Type: "function", Function: models.FunctionCall{Name: "lookup", Arguments: fixture.args}},
						{ID: "benign-call", Type: "function", Function: models.FunctionCall{Name: "count", Arguments: benign}},
					}}
					rc.Request.Messages = []models.Message{msg, msg}
					rc.Response.Choices = []models.Choice{{Message: msg}, {Message: msg}}
					if shape == "response_only_stream" {
						rc.Request = nil
						rc.IsStream = true
					}
					c26Policy(p, rc, source, privacy.ModePatterns)
					before := c26Snapshot(t, rc)
					attrs := c26Export(t, p, rc)
					check := func(label string, messages []models.Message) {
						t.Helper()
						if len(messages) != 2 {
							t.Fatalf("%s lost messages", label)
						}
						for _, message := range messages {
							if len(message.ToolCalls) != 2 {
								t.Fatalf("%s lost tool calls", label)
							}
							args := message.ToolCalls[0].Function.Arguments
							c26Masked(t, args)
							if fixture.invalid && args != "[REDACTED]" {
								t.Errorf("%s retained invalid arguments", label)
							}
							if !fixture.invalid && !json.Valid([]byte(args)) {
								t.Errorf("%s corrupted valid argument JSON: %s", label, args)
							}
							if message.ToolCalls[0].ID != "sensitive-call" || message.ToolCalls[0].Function.Name != "lookup" {
								t.Error("tool identity changed")
							}
							if message.ToolCalls[1].Function.Arguments != benign {
								t.Error("benign arguments or exact integer changed")
							}
						}
					}
					if rc.Request != nil {
						var request models.ChatCompletionRequest
						value, ok := attrs["input.value"].(string)
						if !ok {
							t.Fatal("missing input body")
						}
						if err := json.Unmarshal([]byte(value), &request); err != nil {
							t.Fatal(err)
						}
						check("input", request.Messages)
					} else if _, ok := attrs["input.value"]; ok {
						t.Error("invented request body")
					}
					var response models.ChatCompletionResponse
					value, ok := attrs["output.value"].(string)
					if !ok {
						t.Fatal("missing output body")
					}
					if err := json.Unmarshal([]byte(value), &response); err != nil {
						t.Fatal(err)
					}
					messages := make([]models.Message, len(response.Choices))
					for i, choice := range response.Choices {
						messages[i] = choice.Message
					}
					check("output", messages)
					c26Metadata(t, attrs, rc)
					if !bytes.Equal(before, c26Snapshot(t, rc)) {
						t.Error("live chat context mutated")
					}
				})
			}
		}
	}
}

func TestC26PrivacyChatModeControls(t *testing.T) {
	for _, mode := range []string{privacy.ModeFull, privacy.ModePatterns, privacy.ModeNone, "absent"} {
		for _, contentMode := range []models.ContentMode{models.ContentModeFull, models.ContentModeMetadata, models.ContentModeNone} {
			t.Run(mode+"/"+string(contentMode), func(t *testing.T) {
				p, _ := bodyPlugin(t, true)
				t.Cleanup(p.Close)
				rc := newTestRC()
				rc.Privacy.ContentMode = contentMode
				msg := models.Message{Role: "assistant", ToolCalls: []models.ToolCall{{ID: "call", Type: "function", Function: models.FunctionCall{Name: "empty", Arguments: ""}}}}
				rc.Request.Messages = []models.Message{msg}
				rc.Response.Choices = []models.Choice{{Message: msg}}
				c26Policy(p, rc, "global", mode)
				wantIn, wantOut := bodyJSON(rc)
				attrs := c26Export(t, p, rc)
				for key, want := range map[string]string{"input.value": wantIn, "output.value": wantOut} {
					if mode == privacy.ModeFull {
						want = "[REDACTED]"
					}
					if mode == privacy.ModePatterns {
						// Pattern masking may canonicalize JSON but must preserve empty args.
						var original, exported any
						if err := json.Unmarshal([]byte(want), &original); err != nil {
							t.Fatal(err)
						}
						value, ok := attrs[key].(string)
						if !ok {
							t.Fatalf("missing %s", key)
						}
						if err := json.Unmarshal([]byte(value), &exported); err != nil {
							t.Fatal(err)
						}
						if !bytes.Equal(c26JSON(t, original), c26JSON(t, exported)) {
							t.Errorf("empty arguments changed in %s", key)
						}
					} else if attrs[key] != want {
						t.Errorf("%s = %v, want %s", key, attrs[key], want)
					}
				}
				c26Metadata(t, attrs, rc)
			})
		}
	}
}

func TestC26PrivacyAdditionalAttributes(t *testing.T) {
	for _, source := range []string{"org", "global"} {
		for _, mode := range []string{privacy.ModeFull, privacy.ModePatterns} {
			for _, sink := range []string{"generated_image_url", "input_image_url", "output_image_url", "input_structured_content", "output_structured_content", "extra_number"} {
				t.Run(source+"/"+mode+"/"+sink, func(t *testing.T) {
					p, _ := bodyPlugin(t, true)
					t.Cleanup(p.Close)
					rc := newTestRC()
					rc.Privacy.ContentMode = models.ContentModeFull
					key := "gen_ai.image.output_urls"
					url := "https://images.example.invalid/" + c26Marker()
					switch sink {
					case "generated_image_url":
						rc.EndpointType = "image"
						rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{URL: url}}}
					case "input_image_url", "output_image_url":
						msg := models.Message{Role: "user", Content: c26JSON(t, []any{map[string]any{"type": "image_url", "image_url": map[string]any{"url": url}}})}
						direction := "input"
						if sink == "input_image_url" {
							rc.Request.Messages = []models.Message{msg}
						} else {
							direction = "output"
							rc.Response.Choices = []models.Choice{{Message: msg}}
						}
						key = "llm." + direction + "_messages.0.message.contents.0.message_content.image.url"
					case "input_structured_content", "output_structured_content":
						msg := models.Message{Role: "tool", Content: json.RawMessage(`{"value":"` + c26EscapedMarker() + `"}`)}
						direction := "input"
						if sink == "input_structured_content" {
							rc.Request.Messages = []models.Message{msg}
						} else {
							direction = "output"
							rc.Response.Choices = []models.Choice{{Message: msg}}
						}
						key = "llm." + direction + "_messages.0.message.content"
					case "extra_number":
						n, err := strconv.Atoi(c26NumberMarker())
						if err != nil {
							t.Fatal(err)
						}
						rc.CallerExtras = map[string]any{"customer_number": n}
						key = "agentcc.body.field_0"
					}
					c26Policy(p, rc, source, mode)
					before := c26Snapshot(t, rc)
					attrs := c26Export(t, p, rc)
					value, exists := attrs[key]
					if strings.Contains(sink, "image_url") && !exists {
						// Omitting opaque references is an allowed privacy boundary.
					} else if text, ok := value.(string); ok {
						c26Masked(t, text)
					} else {
						t.Errorf("unmasked or missing %s: %v", key, value)
					}
					wire := c26JSON(t, attrs)
					if bytes.Contains(wire, []byte(c26Marker())) {
						t.Error("another span attribute retained sensitive content")
					}
					c26Metadata(t, attrs, rc)
					if !bytes.Equal(before, c26Snapshot(t, rc)) {
						t.Error("live content mutated")
					}
				})
			}
		}
	}
}

func TestC26PrivacyAdditionalControls(t *testing.T) {
	for _, mode := range []string{privacy.ModeFull, privacy.ModePatterns, privacy.ModeNone, "absent"} {
		for _, contentMode := range []models.ContentMode{models.ContentModeFull, models.ContentModeMetadata, models.ContentModeNone} {
			t.Run(mode+"/"+string(contentMode), func(t *testing.T) {
				p, _ := bodyPlugin(t, false)
				t.Cleanup(p.Close)
				rc := newTestRC()
				rc.Privacy.ContentMode = contentMode
				rc.EndpointType = "image"
				url := "https://images.example.invalid/ordinary.png"
				rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{URL: url}}}
				rc.CallerExtras = map[string]any{"count": 7, "enabled": true, "label": "ordinary"}
				rc.Metadata["group"] = "ordinary"
				rc.CustomMetadataKeys = []string{"group"}
				rc.RequestHeaders = http.Header{"X-Note": []string{c26Marker()}}
				p.captureHeaders = newHeaderMatcher([]string{"x-note"})
				c26Policy(p, rc, "global", mode)
				attrs := c26Export(t, p, rc)
				wantURL := url
				if contentMode != models.ContentModeFull {
					wantURL = imageURLInfo(url).placeholder(contentMode)
				} else if mode == privacy.ModeFull || mode == privacy.ModePatterns {
					wantURL = "[REDACTED]"
				}
				if attrs["gen_ai.image.output_urls"] != wantURL {
					t.Errorf("image policy changed: %v, want %s", attrs["gen_ai.image.output_urls"], wantURL)
				}
				for i, field := range []struct {
					key  string
					want any
				}{{"count", float64(7)}, {"enabled", true}, {"label", "ordinary"}} {
					key, want := field.key, field.want
					if mode == privacy.ModeFull || mode == privacy.ModePatterns {
						key = "field_" + strconv.Itoa(i)
					}
					if mode == privacy.ModeFull {
						want = "[REDACTED]"
					}
					if attrs["agentcc.body."+key] != want {
						t.Errorf("extra %s changed: %v", key, attrs["agentcc.body."+key])
					}
				}
				wantGroup := "ordinary"
				if mode == privacy.ModeFull {
					wantGroup = "[REDACTED]"
				}
				if attrs["agentcc.metadata.group"] != wantGroup {
					t.Error("grouping metadata did not follow privacy mode")
				}
				if mode == privacy.ModeFull || mode == privacy.ModePatterns {
					c26Masked(t, attrs["http.request.header.x-note"].(string))
				} else if attrs["http.request.header.x-note"] != c26Marker() {
					t.Error("disabled header changed")
				}
				c26Metadata(t, attrs, rc)
			})
		}
	}
}
