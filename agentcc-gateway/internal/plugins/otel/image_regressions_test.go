package otel

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestImagePrivacyRegressions(t *testing.T) {
	t.Run("export_policy_before_caps", testImageExportPolicyBeforeCaps)
	t.Run("schema_is_not_content", testImageSchemaIsNotContent)
	t.Run("literal_text_is_opaque", testImageLiteralTextIsOpaque)
}

func testImageExportPolicyBeforeCaps(t *testing.T) {
	restore, err := privacy.SetDefaultContentMode("full")
	if err != nil {
		t.Fatal(err)
	}
	defer restore()
	for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata, models.ContentModeFull} {
		for _, flatten := range []bool{false, true} {
			name := string(mode) + "/blob_only"
			if flatten {
				name = string(mode) + "/flattened"
			}
			t.Run(name, func(t *testing.T) {
				p, exp := bodyPlugin(t, true)
				p.metadataAttributes = flatten
				p.captureHeaders = newHeaderMatcher([]string{"x-image-result"})
				store := tenant.NewStore()
				store.Set("image-org", &tenant.OrgConfig{Privacy: &tenant.PrivacyConfig{ContentMode: "full"}})
				p.SetTenantStore(store)
				rc := newTestRC()
				rc.EndpointType = "responses" // no resolved step-5 policy
				rc.Metadata["org_id"] = "image-org"
				// The URL sorts before padding and survives the old 512-byte cap.
				raw := imageTestJSON(map[string]any{"type": "image_url", "image_url": map[string]any{"url": imageTestURL}, "padding": strings.Repeat("x", 600)})
				rc.Metadata["tool_result"] = raw
				rc.Metadata["label"] = "original label"
				rc.CustomMetadataKeys = []string{"label", "tool_result"}
				rc.CallerExtras = map[string]any{"tool_result": raw, "weight": 3}
				rc.RequestHeaders = http.Header{"X-Image-Result": []string{raw, raw}}
				rc.Request.Messages = []models.Message{{Role: "tool", Content: json.RawMessage(imageTestJSON(raw))}}
				beforeIn, _ := bodyJSON(rc)
				p.ProcessRequest(context.Background(), rc)
				store.Set("image-org", &tenant.OrgConfig{Privacy: &tenant.PrivacyConfig{ContentMode: string(mode)}})
				// Export must rebuild owned request-time copies, not recapture these.
				rc.Metadata["tool_result"] = "later metadata"
				rc.Metadata["label"] = "later label"
				rc.CustomMetadataKeys[0] = "later_key"
				rc.CallerExtras["tool_result"] = "later extra"
				rc.RequestHeaders["X-Image-Result"][0] = "later header"
				p.ProcessResponse(context.Background(), rc)
				attrs := exp.Spans()[0].Attributes
				want := raw
				if mode != models.ContentModeFull {
					marker := "[image]"
					if mode == models.ContentModeMetadata {
						marker = imageTestLinkMetadata
					}
					want = imageTestJSON(imageTestPart(marker))
					assertNoImageContent(t, attrs)
				}
				capped := truncateAtRune(want, maxMetadataValueLen)
				assertImageJSONEqual(t, attrs["metadata"], imageTestJSON(map[string]string{"label": "original label", "tool_result": capped}))
				if flatten {
					if attrs[metadataAttrPrefix+"tool_result"] != capped || attrs[metadataAttrPrefix+"label"] != "original label" {
						t.Error("flattened metadata was not rebuilt from the original uncapped value")
					}
				} else if _, ok := attrs[metadataAttrPrefix+"tool_result"]; ok {
					t.Error("disabled metadata flattening was re-enabled")
				}
				if attrs[bodyAttrPrefix+"tool_result"] != capped || attrs[bodyAttrPrefix+"weight"] != 3 {
					t.Error("body extras were not rebuilt from original uncapped scalars")
				}
				if attrs[headerAttrPrefix+"x-image-result"] != truncateAtRune(want+","+want, maxHeaderValueLen) {
					t.Error("header values were not individually sanitized before joining and capping")
				}
				if attrs["llm.input_messages.0.message.content"] != want {
					t.Error("conversation and captured dimensions used different export policies")
				}
				assertImageJSONEqual(t, attrs["input.value"], strings.Replace(beforeIn, imageTestJSON(raw), imageTestJSON(want), 1))
				if rc.Privacy.ContentMode != "" {
					t.Error("observer wrote the shared request policy")
				}
			})
		}
	}
}

func testImageSchemaIsNotContent(t *testing.T) {
	for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata, models.ContentModeFull} {
		t.Run(string(mode), func(t *testing.T) {
			p, _ := bodyPlugin(t, true)
			rc := newTestRC()
			rc.Privacy.ContentMode = mode
			// image_url is a property declaration, not an actual image reference.
			schema := `{"type":"object","properties":{"image_url":{"type":"string","description":"Optional URL"},"caption":{"type":"string","description":"Keep sibling"}},"required":["caption"]}`
			rc.Request.Tools = []models.Tool{{Type: "function", Function: models.ToolFunction{Name: "describe", Parameters: json.RawMessage(schema)}}}
			beforeIn, _ := bodyJSON(rc)
			attrs := runOnce(p, rc)
			if attrs["input.value"] != beforeIn {
				t.Error("image-free tool schema changed")
			}
		})
	}
}

func testImageLiteralTextIsOpaque(t *testing.T) {
	literals := []string{
		`{"type":"image","label":"example only; no image payload"}`,
		`{"type":"image_url","image_url":{"url":"https://example.invalid/documentation.png"}}`,
		`{"inline_data":{"mime_type":"image/png","data":"RVhBTVBMRQ=="}}`,
	}
	for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata, models.ContentModeFull} {
		for _, literal := range literals {
			for _, shape := range []string{"plain", "text_block", "nested_tool_text"} {
				t.Run(string(mode)+"/"+shape+"/"+literal[:12], func(t *testing.T) {
					p, _ := bodyPlugin(t, true)
					rc := newTestRC()
					rc.Privacy.ContentMode = mode
					content := imageTestJSON(literal)
					attrSuffix := ".message.content"
					if shape == "text_block" {
						content = imageTestJSON([]any{imageTestPart(literal)})
						attrSuffix = ".message.contents.0.message_content.text"
					} else if shape == "nested_tool_text" {
						// An object takes the flattener's JSON fallback, exposing its nested text.
						content = imageTestJSON(map[string]any{"type": "tool_result", "content": []any{imageTestPart(literal)}})
					}
					rc.Request.Messages = []models.Message{{Role: "user", Content: json.RawMessage(content)}}
					rc.Response.Choices = []models.Choice{{Message: models.Message{Role: "assistant", Content: json.RawMessage(content)}}}
					beforeIn, beforeOut := bodyJSON(rc)
					attrs := runOnce(p, rc)
					want := literal
					if shape == "nested_tool_text" {
						want = content
					}
					for _, side := range []string{"input", "output"} {
						if attrs["llm."+side+"_messages.0"+attrSuffix] != want {
							t.Errorf("%s literal conversation text was reinterpreted", side)
						}
					}
					if attrs["input.value"] != beforeIn || attrs["output.value"] != beforeOut {
						t.Error("literal text in evaluator bodies was reinterpreted")
					}
				})
			}
		}
	}
}
