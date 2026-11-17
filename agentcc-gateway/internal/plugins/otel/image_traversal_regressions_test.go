package otel

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Pin the actual computer-call output shape in both conversation attributes
// and evaluator bodies. The encoded_tool row encodes the request tool result;
// the response is a structured assistant result in both rows.
func TestImageComputerScreenshotContentModes(t *testing.T) {
	for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata, models.ContentModeFull} {
		for _, tc := range []struct {
			name, reference, metadata string
		}{
			{"remote", imageTestURL, imageTestLinkMetadata},
			{"data", imageTestURI, imageTestMetadata},
		} {
			t.Run(string(mode)+"/"+tc.name, func(t *testing.T) {
				part := map[string]any{"type": "computer_screenshot", "image_url": tc.reference}
				envelope := func(image any) string {
					return imageTestJSON(map[string]any{
						"type": "computer_call_output", "call_id": "call-screenshot",
						"output": image, "status": "completed", "note": "keep screenshot text",
					})
				}
				assertImageTraversalEnvelope(t, mode, part, tc.metadata, envelope)
			})
		}
	}
}

func TestImageStructuredTextMemberContentModes(t *testing.T) {
	literal := `{"type":"image_url","image_url":{"url":"quoted-reference-only"}}`
	for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata, models.ContentModeFull} {
		for _, tc := range []struct {
			name     string
			part     map[string]any
			metadata string
		}{
			{"openai_url", map[string]any{"type": "image_url", "image_url": map[string]any{"url": imageTestURL}}, imageTestLinkMetadata},
			{"openai_data", map[string]any{"type": "image_url", "image_url": map[string]any{"url": imageTestURI}}, imageTestMetadata},
			{"anthropic", map[string]any{"type": "image", "source": map[string]any{"type": "base64", "media_type": "image/png", "data": imageTestData}}, imageTestMetadata},
			{"gemini", map[string]any{"inline_data": map[string]any{"mime_type": "image/png", "data": imageTestData}}, imageTestMetadata},
		} {
			for _, shape := range []string{"object", "array", "typed_text_object", "typed_text_array"} {
				t.Run(string(mode)+"/"+tc.name+"/"+shape, func(t *testing.T) {
					envelope := func(image any) string {
						var text any = image
						if strings.HasSuffix(shape, "array") {
							text = []any{imageTestPart(literal), image, "keep array text"}
						}
						content := map[string]any{
							"text": text, "note": literal, "status": "ok",
							"number": json.Number("9007199254740993"),
						}
						if strings.HasPrefix(shape, "typed_text_") {
							// A text label with an object/array is not a valid text block.
							content["type"] = "text"
						}
						return imageTestJSON(map[string]any{"type": "tool_result", "content": content})
					}
					assertImageTraversalEnvelope(t, mode, tc.part, tc.metadata, envelope)
				})
			}
		}
	}
}

func assertImageTraversalEnvelope(t *testing.T, mode models.ContentMode, part any, metadata string, envelope func(any) string) {
	t.Helper()
	raw := envelope(part)
	want := raw
	if mode == models.ContentModeNone {
		want = envelope(imageTestPart("[image]"))
	} else if mode == models.ContentModeMetadata {
		want = envelope(imageTestPart(metadata))
	}
	// Whitespace is intentional: full must bypass decoding/re-encoding entirely.
	padded := " \n" + raw + "\t"
	if mode == models.ContentModeFull && maskImageJSONInContext(padded, mode, imageToolEnvelope) != padded {
		t.Error("full changed original envelope bytes")
	}
	for _, wire := range []string{"structured", "encoded_tool"} {
		t.Run(wire, func(t *testing.T) {
			p, _ := bodyPlugin(t, true)
			rc := newTestRC()
			rc.Privacy.ContentMode = mode
			content, wantContent := raw, want
			if wire == "encoded_tool" {
				content, wantContent = imageTestJSON(raw), imageTestJSON(want)
			}
			rc.Request.Messages = []models.Message{{Role: "tool", Content: json.RawMessage(content)}}
			rc.Response.Choices = []models.Choice{{Message: models.Message{Role: "assistant", Content: json.RawMessage(raw)}}}
			beforeIn, beforeOut := bodyJSON(rc)
			attrs := runOnce(p, rc)
			for _, side := range []string{"input", "output"} {
				key := "llm." + side + "_messages.0.message.content"
				if attrs[key] != want {
					t.Errorf("%s differs from the expected filtered envelope", key)
				}
			}
			assertImageTraversalJSON(t, "input.value", attrs["input.value"], strings.Replace(beforeIn, content, wantContent, 1))
			assertImageTraversalJSON(t, "output.value", attrs["output.value"], strings.Replace(beforeOut, raw, want, 1))
			if mode == models.ContentModeFull {
				if attrs["input.value"] != beforeIn || attrs["output.value"] != beforeOut {
					t.Error("full changed evaluator body bytes")
				}
			} else {
				// Report only the attribute and needle ID, never fixture values.
				for key, value := range attrs {
					for id, needle := range []string{imageTestURL, imageTestURI, imageTestData} {
						if strings.Contains(imageTestJSON(value), needle) {
							t.Errorf("%s retained image needle %d", key, id)
						}
					}
				}
			}
			afterIn, afterOut := bodyJSON(rc)
			if beforeIn != afterIn || beforeOut != afterOut || string(rc.Request.Messages[0].Content) != content || string(rc.Response.Choices[0].Message.Content) != raw {
				t.Error("image traversal mutated request or response")
			}
		})
	}
}

// UseNumber keeps this assertion sensitive to precision loss in sibling fields.
// Failures deliberately identify coordinates rather than printing fixture data.
func assertImageTraversalJSON(t *testing.T, key string, got any, want string) {
	t.Helper()
	text, ok := got.(string)
	if !ok {
		t.Errorf("%s is not a JSON string", key)
		return
	}
	decode := func(text string) any {
		decoder := json.NewDecoder(strings.NewReader(text))
		decoder.UseNumber()
		var value any
		if decoder.Decode(&value) != nil {
			t.Fatalf("%s is not valid JSON", key)
		}
		return value
	}
	if !reflect.DeepEqual(decode(text), decode(want)) {
		t.Errorf("%s differs from the expected filtered body", key)
	}
}

func TestImageTraversalTextAndShapeControls(t *testing.T) {
	literal := imageTestJSON(map[string]any{"type": "computer_screenshot", "image_url": imageTestURL})
	for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata, models.ContentModeFull} {
		for _, tc := range []struct {
			name string
			part any
		}{
			{"text_string", map[string]any{"text": literal}},
			{"text_block", imageTestPart(literal)},
			{"input_text_block", map[string]any{"type": "input_text", "text": literal}},
			{"output_text_block", map[string]any{"type": "output_text", "text": literal}},
			{"missing_reference", map[string]any{"type": "computer_screenshot", "caption": "keep caption"}},
			{"empty_reference", map[string]any{"type": "computer_screenshot", "image_url": ""}},
			{"non_string_reference", map[string]any{"type": "computer_screenshot", "image_url": 42}},
			{"reference_schema", map[string]any{"type": "computer_screenshot", "image_url": map[string]any{"type": "string", "description": "keep schema"}}},
			{"audio_under_text", map[string]any{"text": map[string]any{"inline_data": map[string]any{"mime_type": "audio/wav", "data": imageTestData}}}},
		} {
			t.Run(string(mode)+"/"+tc.name, func(t *testing.T) {
				raw := " \n" + imageTestJSON(map[string]any{"type": "tool_result", "content": []any{tc.part}}) + "\t"
				if maskImageJSONInContext(raw, mode, imageToolEnvelope) != raw {
					t.Error("non-image control changed original bytes")
				}
			})
		}
	}
}
