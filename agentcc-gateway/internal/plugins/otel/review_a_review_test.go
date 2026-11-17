package otel

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Tool results are arbitrary JSON, so a property named text need not contain
// a text string. Protect actual strings/text blocks, not image objects nested
// beneath such a property.
func TestAImageUnderStructuredTextField(t *testing.T) {
	parts := map[string]any{
		"openai_data": map[string]any{"type": "image_url", "image_url": map[string]any{"url": imageTestURI}},
		"openai_link": map[string]any{"type": "image_url", "image_url": map[string]any{"url": imageTestURL}},
		"anthropic":   map[string]any{"type": "image", "source": map[string]any{"type": "base64", "media_type": "image/png", "data": imageTestData}},
		"gemini":      map[string]any{"inline_data": map[string]any{"mime_type": "image/png", "data": imageTestData}},
	}
	for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata, models.ContentModeFull} {
		for name, part := range parts {
			t.Run(string(mode)+"/"+name, func(t *testing.T) {
				p, _ := bodyPlugin(t, true)
				rc := newTestRC()
				rc.Privacy.ContentMode = mode
				// A Gemini functionResponse and a tool-role content string both
				// admit arbitrary structured tool-result object properties.
				raw := imageTestJSON(map[string]any{"functionResponse": map[string]any{"name": "view", "response": map[string]any{"text": map[string]any{"content": []any{part}}, "status": "ok"}}})
				rc.Request.Messages = []models.Message{{Role: "tool", Content: json.RawMessage(imageTestJSON(raw))}}
				rc.Response.Choices = []models.Choice{{Message: models.Message{Role: "assistant", Content: json.RawMessage(raw)}}}
				beforeIn, beforeOut := bodyJSON(rc)
				attrs := runOnce(p, rc)
				if mode == models.ContentModeFull {
					if attrs["input.value"] != beforeIn || attrs["output.value"] != beforeOut {
						t.Error("full body bytes changed")
					}
					return
				}
				assertNoImageContent(t, attrs)
				for _, key := range []string{"input.value", "output.value", "llm.input_messages.0.message.content", "llm.output_messages.0.message.content"} {
					v, _ := attrs[key].(string)
					if !strings.Contains(v, "[image") {
						t.Errorf("%s is missing an image placeholder", key)
					}
				}
			})
		}
	}
}
