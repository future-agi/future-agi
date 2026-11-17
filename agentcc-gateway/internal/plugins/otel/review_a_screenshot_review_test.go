package otel

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestAComputerScreenshotToolResult(t *testing.T) {
	for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata, models.ContentModeFull} {
		for _, ref := range []string{imageTestURI, imageTestURL} {
			t.Run(string(mode)+"/"+ref[:4], func(t *testing.T) {
				p, _ := bodyPlugin(t, true)
				rc := newTestRC()
				rc.Privacy.ContentMode = mode
				raw := imageTestJSON(map[string]any{"type": "computer_call_output", "call_id": "call-1", "output": map[string]any{"type": "computer_screenshot", "image_url": ref}})
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
