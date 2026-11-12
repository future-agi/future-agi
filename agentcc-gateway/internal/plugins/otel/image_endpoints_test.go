package otel

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Native model endpoints and nested tool-response envelopes must use the same
// filter as chat, including JSON encoded inside tool message strings.
func TestImageContentAcrossModelEndpoints(t *testing.T) {
	part := map[string]any{"inline_data": map[string]any{"mime_type": "image/png", "data": imageTestData}}
	for _, endpoint := range []string{"chat", "anthropic_messages", "genai_generate", "genai_stream", "responses"} {
		for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata, models.ContentModeFull} {
			t.Run(endpoint+"/"+string(mode), func(t *testing.T) {
				p, _ := bodyPlugin(t, true)
				rc := newTestRC()
				rc.EndpointType = endpoint
				rc.Privacy.ContentMode = mode
				envelope := func(image any) string {
					return imageTestJSON(map[string]any{"functionResponse": map[string]any{"name": "view", "response": map[string]any{"content": []any{imageTestPart("keep tool text"), image}}}})
				}
				raw := envelope(part)
				content := imageTestJSON(raw)
				rc.Request.Messages = []models.Message{{Role: "tool", Content: json.RawMessage(content)}}
				// Assistant text is opaque; use a structured response envelope,
				// not a plain assistant string that merely quotes tool JSON.
				rc.Response.Choices = []models.Choice{{Message: models.Message{Role: "assistant", Content: json.RawMessage(raw)}}}
				beforeIn, beforeOut := bodyJSON(rc)
				want := raw
				if mode == models.ContentModeNone {
					want = envelope(imageTestPart("[image]"))
				} else if mode == models.ContentModeMetadata {
					want = envelope(imageTestPart(imageTestMetadata))
				}
				attrs := runOnce(p, rc)
				for _, side := range []string{"input", "output"} {
					key := "llm." + side + "_messages.0.message.content"
					if attrs[key] != want {
						t.Errorf("%s = %v, want %s", key, attrs[key], want)
					}
				}
				assertImageJSONEqual(t, attrs["input.value"], strings.Replace(beforeIn, content, imageTestJSON(want), 1))
				assertImageJSONEqual(t, attrs["output.value"], strings.Replace(beforeOut, raw, want, 1))
				if mode != models.ContentModeFull {
					assertNoImageContent(t, attrs)
				} else if attrs["input.value"] != beforeIn || attrs["output.value"] != beforeOut {
					t.Error("full body bytes changed")
				}
			})
		}
	}
}

// The image filter runs before the old inline-image elision and body cap. A
// large image must not become an elided data URI or a truncated image prefix.
func TestImageContentBeforeSizeCaps(t *testing.T) {
	data := strings.Repeat("QUJD", maxBodyAttrBytes/4+1)
	uri := "data:image/png;base64," + data
	for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata} {
		t.Run(string(mode), func(t *testing.T) {
			p, _ := bodyPlugin(t, true)
			rc := newTestRC()
			rc.Privacy.ContentMode = mode
			content := imageTestJSON([]any{imageTestPart("keep text"), map[string]any{"type": "image_url", "image_url": map[string]any{"url": uri}}})
			rc.Request.Messages = []models.Message{{Role: "user", Content: json.RawMessage(content)}}
			before, _ := bodyJSON(rc)
			marker := "[image]"
			if mode == models.ContentModeMetadata {
				marker = `[image: {"kind":"image","media_type":"image/png","bytes":786435}]`
			}
			attrs := runOnce(p, rc)
			wantContent := imageTestJSON([]any{imageTestPart("keep text"), imageTestPart(marker)})
			assertImageJSONEqual(t, attrs["input.value"], strings.Replace(before, content, wantContent, 1))
			if attrs["llm.input_messages.0.message.contents.1.message_content.text"] != marker {
				t.Error("image placeholder was lost to the message size cap")
			}
			if attrs["agentcc.input_truncated"] != nil || attrs["input.mime_type"] != "application/json" {
				t.Error("image was not replaced before body truncation")
			}
			for k, v := range attrs {
				if s, ok := v.(string); ok && (strings.Contains(s, "data:image/") || strings.Contains(s, "QUJD")) {
					t.Errorf("%s retained image data", k)
				}
			}
		})
	}
}
