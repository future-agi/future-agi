package logging

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/payloads"
	otelplugin "github.com/futureagi/agentcc-gateway/internal/plugins/otel"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// J15.W05-W16-W18-01: full content retention does not turn off data masking.
const w2B1Marker = "W2-B1-private-input-73921"

func w2B1Request(endpoint string) *models.RequestContext {
	rc := newRC()
	rc.EndpointType = endpoint
	rc.Privacy.ContentMode = models.ContentModeFull
	rc.Metadata[tenant.MetadataKeyOrgID] = "w2-b1-org"
	switch endpoint {
	case "embedding", "genai_embed":
		rc.EmbeddingRequest = &models.EmbeddingRequest{Model: rc.Model, Input: json.RawMessage(`["` + w2B1Marker + `",[73921,73922]]`)}
		rc.EmbeddingResponse = &models.EmbeddingResponse{Model: rc.Model}
	case "speech", "speech_stream":
		rc.SpeechRequest = &models.SpeechRequest{Model: rc.Model, Input: w2B1Marker, Voice: "alloy"}
		rc.Metadata["response_content_type"] = "audio/mpeg"
	case "image":
		rc.ImageRequest = &models.ImageRequest{Model: rc.Model, Prompt: w2B1Marker}
		rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{URL: "https://example.invalid/" + w2B1Marker, RevisedPrompt: w2B1Marker}}}
	case "transcription":
		rc.TranscriptionReq = &models.TranscriptionRequest{Model: rc.Model, FileName: w2B1Marker + ".wav"}
		rc.TranscriptionResp = &models.TranscriptionResponse{Text: w2B1Marker}
	case "translation":
		rc.TranslationReq = &models.TranslationRequest{Model: rc.Model, FileName: w2B1Marker + ".wav", Prompt: w2B1Marker}
		rc.TranslationResp = &models.TranslationResponse{Text: w2B1Marker}
	case "rerank":
		rc.RerankRequest = &models.RerankRequest{Model: rc.Model, Query: w2B1Marker, Documents: []string{w2B1Marker}}
		rc.RerankResponse = &models.RerankResponse{Results: []models.RerankResult{{Document: &models.RerankDocument{Text: w2B1Marker}}}}
	}
	return rc
}

func w2B1Tenants(mode string) *tenant.Store {
	s := tenant.NewStore()
	s.Set("w2-b1-org", &tenant.OrgConfig{Privacy: &tenant.PrivacyConfig{
		Enabled: true, Mode: mode, ContentMode: "full",
		Patterns: []*tenant.RedactPatternConfig{{Name: "private", Pattern: w2B1Marker}},
	}})
	return s
}

// Exercise the actual queue and webhook encoder without sending any HTTP.
// The emitter has no worker so its pre-enqueue snapshot is also inspectable.
func w2B1Log(t *testing.T, rc *models.RequestContext, tenants *tenant.Store, global *privacy.Redactor) (TraceRecord, map[string]any) {
	t.Helper()
	cfg := config.RequestLoggingConfig{Enabled: true, IncludeBodies: true}
	e := &TraceEmitter{ch: make(chan TraceRecord, 1), cfg: cfg}
	f := NewLogFlusher("http://127.0.0.1:1/never", "", time.Hour, 100)
	p := &Plugin{cfg: cfg, tenantStore: tenants, redactor: global, emitter: e, flusher: f}
	p.ProcessResponse(context.Background(), rc)
	var traced TraceRecord
	select {
	case traced = <-e.ch:
	default:
		t.Fatal("no trace record enqueued")
	}
	records := f.take()
	if len(records) != 1 {
		t.Fatalf("queued records = %d, want one", len(records))
	}
	body, err := f.encodeLogs(records, nil)
	if err != nil {
		t.Fatal(err)
	}
	var batch struct {
		Logs []map[string]any `json:"logs"`
	}
	if err := json.Unmarshal(body, &batch); err != nil || len(batch.Logs) != 1 {
		t.Fatalf("webhook payload = %s, error = %v", body, err)
	}
	return traced, batch.Logs[0]
}

func TestW2B1FullPrivacyMasksNonChatBodies(t *testing.T) {
	for _, source := range []string{"org", "global"} {
		for _, endpoint := range []string{"embedding", "genai_embed", "speech", "speech_stream", "image", "transcription", "translation", "rerank"} {
			t.Run(source+"/"+endpoint, func(t *testing.T) {
				rc := w2B1Request(endpoint)
				// Non-chat handlers need not resolve content mode or copy privacy
				// metadata: the stored organization policy must suffice.
				rc.Privacy.ContentMode = ""
				beforeReq, beforeResp := payloads.ForEndpoint(rc)
				var tenants *tenant.Store
				var global *privacy.Redactor
				if source == "org" {
					tenants = w2B1Tenants(privacy.ModeFull)
					// Org policy must win over a weaker gateway policy.
					global = privacy.New(privacy.ModePatterns, nil)
				} else {
					global = privacy.New(privacy.ModeFull, nil)
				}
				trace, entry := w2B1Log(t, rc, tenants, global)
				for _, field := range []string{"request_body", "response_body"} {
					body, ok := entry[field]
					if !ok {
						t.Errorf("full retention lost the %s redaction indication", field)
						continue
					}
					encoded, err := json.Marshal(body)
					if err != nil || !json.Valid(encoded) || strings.Contains(string(encoded), w2B1Marker) || !strings.Contains(string(encoded), "[REDACTED]") {
						t.Errorf("%s exposes unmasked content: %s (error %v)", field, encoded, err)
					}
				}
				queued, err := json.Marshal(trace)
				if err != nil || bytes.Contains(queued, []byte(w2B1Marker)) {
					t.Errorf("trace queue contains unmasked body: %s (error %v)", queued, err)
				}
				if entry["content_mode"] != "full" || entry["model"] != rc.Model || trace.Metadata["privacy_redacted"] != "true" {
					t.Errorf("redaction lost request metadata: %v", entry)
				}
				afterReq, afterResp := payloads.ForEndpoint(rc)
				if !bytes.Equal(beforeReq, afterReq) || !bytes.Equal(beforeResp, afterResp) || rc.Metadata["privacy_redacted"] != "" {
					t.Error("logging mutated the live request/response or parallel-plugin metadata")
				}
			})
		}
	}
}

func TestW2B1PrivacyControls(t *testing.T) {
	t.Run("disabled masking retains full content", func(t *testing.T) {
		_, entry := w2B1Log(t, w2B1Request("embedding"), nil, privacy.New(privacy.ModeNone, nil))
		body, _ := json.Marshal(entry["request_body"])
		if !bytes.Contains(body, []byte(w2B1Marker)) {
			t.Fatalf("disabled masking dropped requested full content: %s", body)
		}
	})
	t.Run("patterns cannot leak encoded non-chat content", func(t *testing.T) {
		rc := w2B1Request("embedding")
		// A pattern over serialized JSON would miss the decoded quote/newline.
		rc.EmbeddingRequest.Input = json.RawMessage(`"private\"\nvalue"`)
		r := privacy.New(privacy.ModePatterns, []privacy.PatternConfig{{Name: "encoded", Pattern: "private\"\nvalue"}})
		_, entry := w2B1Log(t, rc, nil, r)
		body, _ := json.Marshal(entry["request_body"])
		if bytes.Contains(body, []byte("private")) || !bytes.Contains(body, []byte("[REDACTED")) {
			t.Fatalf("encoded content escaped redaction: %s", body)
		}
	})
	for _, mode := range []models.ContentMode{models.ContentModeMetadata, models.ContentModeNone} {
		t.Run(string(mode)+" retention stays body-free", func(t *testing.T) {
			rc := w2B1Request("embedding")
			rc.Privacy.ContentMode = mode
			_, entry := w2B1Log(t, rc, w2B1Tenants(privacy.ModeFull), nil)
			for _, key := range []string{"request_body", "response_body"} {
				if _, ok := entry[key]; ok {
					t.Errorf("%s retention kept %s", mode, key)
				}
			}
		})
	}
}

func TestW2B1ChatToolArgumentsStayMasked(t *testing.T) {
	rc := newRC()
	rc.Privacy.ContentMode = models.ContentModeFull
	var msg models.Message
	if err := json.Unmarshal([]byte(`{"role":"assistant","tool_calls":[{"id":"call-1","type":"function","function":{"name":"lookup","arguments":"{\"query\":\"`+w2B1Marker+`\"}"}}]}`), &msg); err != nil {
		t.Fatal(err)
	}
	rc.Request = &models.ChatCompletionRequest{Model: rc.Model, Messages: []models.Message{msg}}
	rc.Response = &models.ChatCompletionResponse{Model: rc.Model, Choices: []models.Choice{{Message: msg}}}
	rc.IsStream = true // includes the stream's already-assembled tool calls
	_, entry := w2B1Log(t, rc, nil, privacy.New(privacy.ModeFull, nil))
	encoded, _ := json.Marshal(entry)
	if bytes.Contains(encoded, []byte(w2B1Marker)) || !bytes.Contains(encoded, []byte("[REDACTED]")) {
		t.Fatalf("structured tool arguments were not masked: %s", encoded)
	}
	if !strings.Contains(rc.Response.Choices[0].Message.ToolCalls[0].Function.Arguments, w2B1Marker) {
		t.Fatal("logging changed live streamed tool arguments")
	}
}

// The separately exported spans already apply privacy. Exercise their actual
// stdout exporter with the same policies so fixing the webhook cannot hide a
// second plaintext destination. No collector or live gateway is contacted.
func TestW2B1TraceExporterMasksNonChatBodies(t *testing.T) {
	for _, source := range []string{"org", "global", "disabled"} {
		t.Run(source, func(t *testing.T) {
			file, err := os.CreateTemp(t.TempDir(), "span-*.json")
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { file.Close() })
			stdout := os.Stdout
			var p *otelplugin.Plugin
			func() {
				os.Stdout = file
				defer func() { os.Stdout = stdout }()
				p = otelplugin.New(config.OTelConfig{Enabled: true, Exporter: "stdout", IncludeBodies: true, SampleRate: 1})
			}()
			t.Cleanup(p.Close)
			switch source {
			case "org":
				p.SetTenantStore(w2B1Tenants(privacy.ModeFull))
			case "global":
				p.SetRedactor(privacy.New(privacy.ModeFull, nil))
			}
			rc := w2B1Request("image") // content in request and response
			p.ProcessRequest(context.Background(), rc)
			p.ProcessResponse(context.Background(), rc)
			body, err := os.ReadFile(file.Name())
			if err != nil {
				t.Fatal(err)
			}
			var span struct {
				Attributes map[string]any `json:"attributes"`
			}
			if err := json.Unmarshal(body, &span); err != nil {
				t.Fatal(err)
			}
			for _, key := range []string{"input.value", "output.value"} {
				value, ok := span.Attributes[key].(string)
				if !ok || value == "" {
					t.Fatalf("exported span missing %s: %s", key, body)
				}
				if source == "disabled" {
					if !strings.Contains(value, w2B1Marker) {
						t.Errorf("unmasked control lost %s: %s", key, value)
					}
				} else if strings.Contains(value, w2B1Marker) || !strings.Contains(value, "[REDACTED]") {
					t.Errorf("trace exporter leaked %s: %s", key, value)
				}
			}
		})
	}
}
