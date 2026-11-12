package otel

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/payloads"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The independent span exporter must not pattern-match only the
// serialized JSON bytes, where quotes/newlines hide the configured match.
// Capture Export directly; no collector, HTTP, or live provider is used.
func TestW2B1PatternEncodedNonChatExporter(t *testing.T) {
	const secret = "private\"\nvalue"
	for _, source := range []string{"org", "global"} {
		for _, mode := range []string{privacy.ModePatterns, privacy.ModeFull, privacy.ModeNone} {
			for _, endpoint := range []string{"embedding", "image", "translation", "rerank"} {
				t.Run(source+"/"+mode+"/"+endpoint, func(t *testing.T) {
					p, exp := bodyPlugin(t, true)
					t.Cleanup(p.Close)
					patterns := []privacy.PatternConfig{{Name: "encoded", Pattern: secret}}
					if source == "org" {
						store := tenant.NewStore()
						store.Set("w2-b1-org", &tenant.OrgConfig{Privacy: &tenant.PrivacyConfig{
							Enabled: true, Mode: mode, ContentMode: "full",
							Patterns: []*tenant.RedactPatternConfig{{Name: "encoded", Pattern: secret}},
						}})
						p.SetTenantStore(store)
						p.SetRedactor(privacy.New(privacy.ModeNone, nil))
					} else {
						p.SetRedactor(privacy.New(mode, patterns))
					}
					rc := newTestRC()
					rc.Request, rc.Response = nil, nil
					rc.EndpointType = endpoint
					rc.Privacy.ContentMode = models.ContentModeFull
					rc.Metadata[tenant.MetadataKeyOrgID] = "w2-b1-org"
					if mode != privacy.ModeNone {
						rc.Metadata["privacy_mode"] = privacy.ModeNone // a caller cannot weaken masking
					}
					switch endpoint {
					case "embedding":
						rc.EmbeddingRequest = &models.EmbeddingRequest{Model: rc.Model, Input: json.RawMessage(`"private\"\nvalue"`)}
						rc.EmbeddingResponse = &models.EmbeddingResponse{Model: rc.Model}
					case "image":
						rc.ImageRequest = &models.ImageRequest{Model: rc.Model, Prompt: secret}
						rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{RevisedPrompt: secret}}}
					case "translation":
						rc.TranslationReq = &models.TranslationRequest{Model: rc.Model, Prompt: secret}
						rc.TranslationResp = &models.TranslationResponse{Text: secret}
					case "rerank":
						rc.RerankRequest = &models.RerankRequest{Model: rc.Model, Query: secret, Documents: []string{secret}}
						rc.RerankResponse = &models.RerankResponse{Results: []models.RerankResult{{Document: &models.RerankDocument{Text: secret}}}}
					}
					beforeReq, beforeResp := payloads.ForEndpoint(rc)
					runOnce(p, rc)
					spans := exp.Spans()
					if len(spans) != 1 {
						t.Fatalf("exported %d spans, want one", len(spans))
					}
					wire, err := json.Marshal(spans[0])
					if err != nil {
						t.Fatal(err)
					}
					var span struct {
						Attributes map[string]any `json:"attributes"`
					}
					if err := json.Unmarshal(wire, &span); err != nil {
						t.Fatal(err)
					}
					for _, key := range []string{"input.value", "output.value"} {
						value, ok := span.Attributes[key].(string)
						if !ok || value == "" {
							t.Fatalf("exported span lacks %s", key)
						}
						if mode == privacy.ModeNone {
							want := beforeReq
							if key == "output.value" {
								want = beforeResp
							}
							if value != string(want) {
								t.Errorf("disabled masking changed %s: %s", key, value)
							}
						} else if strings.Contains(value, "private") || !strings.Contains(value, "[REDACTED") {
							t.Errorf("exported %s retains encoded content or lacks masking: %s", key, value)
						}
					}
					if span.Attributes["gen_ai.request.model"] != rc.Model {
						t.Error("body masking lost request model metadata")
					}
					afterReq, afterResp := payloads.ForEndpoint(rc)
					if !bytes.Equal(beforeReq, afterReq) || !bytes.Equal(beforeResp, afterResp) {
						t.Error("exporter mutated live non-chat content")
					}
				})
			}
		}
	}
}
