package otel

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestImagePartVariantsAndUnchangedText(t *testing.T) {
	for _, tc := range []struct {
		name, raw, marker string
	}{
		{"responses", `{"type":"input_image","image_url":"` + imageTestURI + `"}`, imageTestMetadata},
		{"gemini_sdk", `{"inlineData":{"mimeType":"image/png","data":"` + imageTestData + `"}}`, imageTestMetadata},
		{"gemini_file", `{"fileData":{"mimeType":"image/png","fileUri":"` + imageTestURL + `"}}`, imageTestLinkMetadata},
		{"anthropic_url", `{"type":"image","source":{"type":"url","url":"` + imageTestURL + `"}}`, imageTestLinkMetadata},
		{"tool_image", `{"type":"image","mimeType":"image/png","data":"` + imageTestData + `"}`, imageTestMetadata},
		{"percent_data", `{"type":"image_url","image_url":{"url":"data:image/png,%49MAGE"}}`, imageTestMetadata},
	} {
		t.Run(tc.name, func(t *testing.T) {
			for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata, models.ContentModeFull} {
				want := tc.raw
				if mode == models.ContentModeNone {
					want = imageTestJSON(imageTestPart("[image]"))
				}
				if mode == models.ContentModeMetadata {
					want = imageTestJSON(imageTestPart(tc.marker))
				}
				if got := maskImageJSON(tc.raw, mode); got != want {
					t.Errorf("mode %s: %s, want %s", mode, got, want)
				}
			}
		})
	}
	// These are text and audio, not image parts, even though the text contains
	// an image URL and a data URI. Do not widen this lane into text scrubbing.
	raw := `{"text":"` + imageTestURL + ` ` + imageTestURI + `","number":9007199254740993,"parts":[{"inline_data":{"mime_type":"audio/wav","data":"` + imageTestData + `"}}]}`
	for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata, models.ContentModeFull} {
		if got := maskImageJSON(raw, mode); got != raw {
			t.Errorf("non-image content changed for %s: %s", mode, got)
		}
	}
	// Re-encoding a body containing an image must not round a sibling integer.
	raw = `{"number":9007199254740993,"content":[{"type":"image_url","image_url":{"url":"` + imageTestURL + `"}}]}`
	if got := maskImageJSON(raw, models.ContentModeNone); !strings.Contains(got, `"number":9007199254740993`) {
		t.Errorf("integer rounded: %s", got)
	}
}

func TestImageFullRetainsExistingSmallAndMediumRepresentations(t *testing.T) {
	for _, data := range []string{imageTestData, strings.Repeat("QUJD", 100)} {
		p, _ := bodyPlugin(t, true)
		rc := newTestRC()
		rc.Privacy.ContentMode = models.ContentModeFull
		uri := "data:image/png;base64," + data
		rc.Request.Messages = []models.Message{{Role: "user", Content: json.RawMessage(imageTestJSON([]any{map[string]any{"type": "image_url", "image_url": map[string]any{"url": uri}}}))}}
		original, _ := bodyJSON(rc)
		attrs := runOnce(p, rc)
		if attrs["llm.input_messages.0.message.contents.0.message_content.image.url"] != uri {
			t.Error("full lost a renderable image")
		}
		if attrs["input.value"] != prepareBody(original, nil, "").value {
			t.Error("full changed the existing body elision policy")
		}
	}
}

func TestImageResponseFieldsAndBinaryGeneration(t *testing.T) {
	for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata, models.ContentModeFull} {
		p, _ := bodyPlugin(t, true)
		rc := newTestRC()
		rc.Privacy.ContentMode = mode
		images := `[{"type":"image_url","image_url":{"url":"` + imageTestURI + `"}}]`
		rc.Response.Choices = []models.Choice{{Message: models.Message{Role: "assistant", Content: json.RawMessage(`"keep text"`), Images: json.RawMessage(images)}}}
		attrs := runOnce(p, rc)
		if mode != models.ContentModeFull {
			assertNoImageContent(t, attrs)
		}
		if attrs["llm.output_messages.0.message.content"] != "keep text" {
			t.Error("response text changed")
		}
		if string(rc.Response.Choices[0].Message.Images) != images {
			t.Error("response image field mutated")
		}

		p, _ = bodyPlugin(t, true)
		rc = newTestRC()
		rc.Privacy.ContentMode = mode
		rc.EndpointType = "image"
		rc.Request, rc.Response = nil, nil
		rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{B64JSON: imageTestData}}}
		attrs = runOnce(p, rc)
		entry := map[string]any{"b64_json_bytes": 8}
		if mode == models.ContentModeNone {
			entry = map[string]any{"url": "[image]"}
		}
		if mode == models.ContentModeMetadata {
			entry = map[string]any{"url": `[image: {"kind":"image","media_type":"unknown","bytes":5}]`}
		}
		assertImageJSONEqual(t, attrs["output.value"], imageTestJSON(map[string]any{"created": 0, "data": []any{entry}}))
		if rc.ImageResponse.Data[0].B64JSON != imageTestData {
			t.Error("generated image mutated")
		}
	}
}
