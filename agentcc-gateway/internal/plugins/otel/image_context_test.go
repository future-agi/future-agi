package otel

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The same literal JSON can be a schema example, quoted text or an actual
// image part. Only the protocol content/tool envelope makes it an image.
func TestImageStructuredContextBoundaries(t *testing.T) {
	part := map[string]any{"type": "image_url", "image_url": map[string]any{"url": imageTestURL}}
	literal := imageTestJSON(part)
	for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata, models.ContentModeFull} {
		t.Run(string(mode), func(t *testing.T) {
			p, _ := bodyPlugin(t, true)
			rc := newTestRC()
			rc.Privacy.ContentMode = mode
			schema := imageTestJSON(map[string]any{
				"type": "object", "properties": map[string]any{"image_url": map[string]any{"type": "string"}},
				"description": literal, "examples": []any{part},
			})
			rc.Request.Tools = []models.Tool{{Type: "function", Function: models.ToolFunction{Name: "view", Parameters: json.RawMessage(schema)}}}
			envelope := func(image any) string {
				return imageTestJSON(map[string]any{"parameters": map[string]any{"content": []any{imageTestPart(literal), image}}, "note": literal})
			}
			args := envelope(part)
			content := imageTestJSON(map[string]any{"type": "tool_result", "content": []any{imageTestPart(literal), part}})
			rc.Request.Messages = []models.Message{{Role: "user", Content: json.RawMessage(content)}}
			rc.Response.Choices = []models.Choice{{Message: models.Message{
				Role: "assistant", Content: json.RawMessage(imageTestJSON(literal)),
				ToolCalls: []models.ToolCall{{ID: "call-1", Type: "function", Function: models.FunctionCall{Name: "view", Arguments: args}}},
			}}}
			beforeIn, beforeOut := bodyJSON(rc)
			wantPart := part
			if mode == models.ContentModeNone {
				wantPart = imageTestPart("[image]")
			} else if mode == models.ContentModeMetadata {
				wantPart = imageTestPart(imageTestLinkMetadata)
			}
			wantContent := imageTestJSON(map[string]any{"type": "tool_result", "content": []any{imageTestPart(literal), wantPart}})
			attrs := runOnce(p, rc)
			assertImageJSONEqual(t, attrs["input.value"], strings.Replace(beforeIn, content, wantContent, 1))
			assertImageJSONEqual(t, attrs["output.value"], strings.Replace(beforeOut, imageTestJSON(args), imageTestJSON(envelope(wantPart)), 1))
			if attrs["llm.input_messages.0.message.content"] != wantContent {
				t.Error("nested tool result did not preserve text while masking its image")
			}
			if attrs["llm.output_messages.0.message.content"] != literal {
				t.Error("assistant text quoting an image was changed")
			}
			afterIn, afterOut := bodyJSON(rc)
			if afterIn != beforeIn || afterOut != beforeOut {
				t.Error("structured traversal mutated provider content")
			}
		})
	}
}

func TestImageEndpointLiteralPromptsStayText(t *testing.T) {
	literal := `{"type":"image","label":"example only; no image payload"}`
	for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata, models.ContentModeFull} {
		p, _ := bodyPlugin(t, true)
		rc := newTestRC()
		rc.EndpointType = "image"
		rc.Privacy.ContentMode = mode
		rc.Request, rc.Response = nil, nil
		rc.ImageRequest = &models.ImageRequest{Prompt: literal}
		rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{URL: imageTestURL, RevisedPrompt: literal}}}
		attrs := runOnce(p, rc)
		for _, key := range []string{"gen_ai.image.prompt", "gen_ai.image.revised_prompt"} {
			if attrs[key] != literal {
				t.Errorf("%s: %s did not preserve literal text", mode, key)
			}
		}
	}
}
