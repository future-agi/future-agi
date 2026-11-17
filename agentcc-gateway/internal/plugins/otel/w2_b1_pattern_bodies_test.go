package otel

import (
	"bytes"
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/payloads"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Check the whole-body suppression contract with ordinary synthetic content.
// Export is captured in memory; no collector, HTTP or provider is contacted.
func TestW2B1PatternNonChatBodyControls(t *testing.T) {
	const sample = "sample-content"
	for _, source := range []string{"org", "global"} {
		for _, mode := range []string{privacy.ModePatterns, privacy.ModeFull, privacy.ModeNone} {
			for _, contentMode := range []models.ContentMode{models.ContentModeFull, models.ContentModeMetadata, models.ContentModeNone} {
				for _, endpoint := range []string{"embedding", "image", "translation", "rerank"} {
					t.Run(source+"/"+mode+"/"+string(contentMode)+"/"+endpoint, func(t *testing.T) {
						p, exp := bodyPlugin(t, true)
						t.Cleanup(p.Close)
						patterns := []privacy.PatternConfig{{Name: "sample", Pattern: sample}}
						if source == "org" {
							store := tenant.NewStore()
							store.Set("w2-b1-org", &tenant.OrgConfig{Privacy: &tenant.PrivacyConfig{
								Enabled: true, Mode: mode, ContentMode: string(contentMode),
								Patterns: []*tenant.RedactPatternConfig{{Name: "sample", Pattern: sample}},
							}})
							p.SetTenantStore(store)
							p.SetRedactor(privacy.New(privacy.ModeNone, nil))
						} else {
							p.SetRedactor(privacy.New(mode, patterns))
						}
						rc := newTestRC()
						rc.Request, rc.Response = nil, nil
						rc.EndpointType = endpoint
						rc.Privacy.ContentMode = contentMode
						rc.Metadata[tenant.MetadataKeyOrgID] = "w2-b1-org"
						rc.Metadata["privacy_mode"] = privacy.ModeNone
						switch endpoint {
						case "embedding":
							rc.EmbeddingRequest = &models.EmbeddingRequest{Model: rc.Model, Input: json.RawMessage(`"sample-content"`)}
							rc.EmbeddingResponse = &models.EmbeddingResponse{Model: rc.Model}
						case "image":
							rc.ImageRequest = &models.ImageRequest{Model: rc.Model, Prompt: sample}
							rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{RevisedPrompt: sample}}}
						case "translation":
							rc.TranslationReq = &models.TranslationRequest{Model: rc.Model, Prompt: sample}
							rc.TranslationResp = &models.TranslationResponse{Text: sample}
						case "rerank":
							rc.RerankRequest = &models.RerankRequest{Model: rc.Model, Query: sample, Documents: []string{sample}}
							rc.RerankResponse = &models.RerankResponse{Results: []models.RerankResult{{Document: &models.RerankDocument{Text: sample}}}}
						}
						beforeReq, beforeResp := payloads.ForEndpoint(rc)
						runOnce(p, rc)
						spans := exp.Spans()
						if len(spans) != 1 {
							t.Fatalf("exported %d spans, want one", len(spans))
						}
						for _, key := range []string{"input.value", "output.value"} {
							value, ok := spans[0].Attributes[key].(string)
							if !ok || value == "" {
								t.Fatalf("exported span lacks %s", key)
							}
							want := `"[REDACTED]"`
							if mode == privacy.ModeFull {
								want = "[REDACTED]"
							} else if mode == privacy.ModeNone {
								want = string(beforeReq)
								if key == "output.value" {
									want = string(beforeResp)
								}
							}
							if value != want {
								t.Errorf("%s = %q, want %q", key, value, want)
							}
						}
						if spans[0].Attributes["gen_ai.request.model"] != rc.Model {
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
}

func TestW2B1PatternNonChatAbsentBodyControls(t *testing.T) {
	for _, present := range []string{"request", "response", "neither"} {
		t.Run(present, func(t *testing.T) {
			p, _ := bodyPlugin(t, true)
			t.Cleanup(p.Close)
			p.SetRedactor(privacy.New(privacy.ModePatterns, nil))
			rc := newTestRC()
			rc.Request, rc.Response = nil, nil
			rc.EndpointType = "translation"
			rc.Privacy.ContentMode = models.ContentModeFull
			if present == "request" {
				rc.TranslationReq = &models.TranslationRequest{Model: rc.Model, Prompt: "sample-content"}
			}
			if present == "response" {
				rc.TranslationResp = &models.TranslationResponse{Text: "sample-content"}
			}
			attrs := runOnce(p, rc)
			for key, wantPresent := range map[string]bool{"input.value": present == "request", "output.value": present == "response"} {
				value, exists := attrs[key]
				if exists != wantPresent {
					t.Errorf("%s presence = %v, want %v", key, exists, wantPresent)
				} else if exists && value != `"[REDACTED]"` {
					t.Errorf("%s = %v, want suppressed body", key, value)
				}
			}
		})
	}
}
