package otel

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Independent boundary probes: protocol image references must not escape a
// restrictive policy because their tool-result discriminator or container key
// differs from an ordinary chat image part.
func TestBImageToolResultBoundaries(t *testing.T) {
	image := map[string]any{"type": "image_url", "image_url": map[string]any{"url": imageTestURL}}
	for _, tc := range []struct {
		name string
		body any
	}{
		{"computer_screenshot", map[string]any{"type": "computer_call_output", "output": map[string]any{"type": "computer_screenshot", "image_url": imageTestURL}}},
		{"tool_result_text_object", map[string]any{"type": "tool_result", "content": map[string]any{"text": image}}},
		{"tool_result_text_array", map[string]any{"type": "tool_result", "content": map[string]any{"text": []any{image}}}},
		{"ordinary_nested_image_control", map[string]any{"type": "tool_result", "content": map[string]any{"result": image}}},
	} {
		for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata, models.ContentModeFull} {
			t.Run(tc.name+"/"+string(mode), func(t *testing.T) {
				p, _ := bodyPlugin(t, true)
				rc := newTestRC()
				rc.Privacy.ContentMode = mode
				raw := imageTestJSON(tc.body)
				content := json.RawMessage(imageTestJSON(raw))
				rc.Request.Messages = []models.Message{{Role: "tool", Content: content}}
				rc.Response.Choices = []models.Choice{{Message: models.Message{Role: "tool", Content: content}}}
				beforeIn, beforeOut := bodyJSON(rc)
				attrs := runOnce(p, rc)
				if mode == models.ContentModeFull {
					if attrs["input.value"] != beforeIn || attrs["output.value"] != beforeOut {
						t.Error("full evaluator body changed")
					}
					if attrs["llm.input_messages.0.message.content"] != raw || attrs["llm.output_messages.0.message.content"] != raw {
						t.Error("full conversation bytes changed")
					}
					return
				}
				for key, value := range attrs {
					if text, ok := value.(string); ok && strings.Contains(text, imageTestURL) {
						t.Errorf("%s leaked image URL in %s", key, text)
					}
				}
			})
		}
	}
}
