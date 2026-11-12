package otel

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestBImageNativeContainerBoundaries(t *testing.T) {
	for _, tc := range []struct {
		name     string
		part     map[string]any
		metadata string
	}{
		{"openai_link", map[string]any{"type": "image_url", "image_url": map[string]any{"url": imageTestURL}}, imageTestLinkMetadata},
		{"openai_data", map[string]any{"type": "image_url", "image_url": map[string]any{"url": imageTestURI}}, imageTestMetadata},
		{"anthropic_base64", map[string]any{"type": "image", "source": map[string]any{"type": "base64", "media_type": "image/png", "data": imageTestData}}, imageTestMetadata},
		{"gemini_inline_data", map[string]any{"inline_data": map[string]any{"mime_type": "image/png", "data": imageTestData}}, imageTestMetadata},
	} {
		for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata, models.ContentModeFull} {
			t.Run(tc.name+"/"+string(mode), func(t *testing.T) {
				p, _ := bodyPlugin(t, true)
				rc := newTestRC()
				rc.Privacy.ContentMode = mode
				envelope := func(part any) string {
					return imageTestJSON(map[string]any{"type": "tool_result", "content": map[string]any{"text": part, "caption": "keep literal text"}})
				}
				raw := envelope(tc.part)
				rc.Request.Messages = []models.Message{{Role: "tool", Content: json.RawMessage(imageTestJSON(raw))}}
				rc.Response = nil
				attrs := runOnce(p, rc)
				want := raw
				if mode != models.ContentModeFull {
					marker := "[image]"
					if mode == models.ContentModeMetadata {
						marker = tc.metadata
					}
					want = envelope(imageTestPart(marker))
					assertNoImageContent(t, attrs)
				}
				if attrs["llm.input_messages.0.message.content"] != want {
					t.Errorf("structured tool text member was not masked: got %v, want %s", attrs["llm.input_messages.0.message.content"], want)
				}
			})
		}
	}
}
