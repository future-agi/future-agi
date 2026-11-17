package otel

import (
	"encoding/json"
	"net/http"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	imageTestData         = "SU1BR0U=" // five decoded bytes; below both existing elision limits
	imageTestURI          = "data:image/png;base64," + imageTestData
	imageTestURL          = "https://images.invalid/private.png?signature=private-image"
	imageTestMetadata     = `[image: {"kind":"image","media_type":"image/png","bytes":5}]`
	imageTestLinkMetadata = `[image: {"kind":"image","media_type":"unknown","bytes":0}]`
)

func imageTestJSON(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return string(b)
}

func imageTestPart(text string) map[string]any {
	return map[string]any{"type": "text", "text": text}
}

// Pins exact conversation attributes AND both evaluator bodies. The full rows
// are positive controls for the existing representation (including native
// parts the conversation flattener did not previously render).
func TestImageContentModes(t *testing.T) {
	openAI := map[string]any{"type": "image_url", "image_url": map[string]any{"url": imageTestURI}}
	anthropic := map[string]any{"type": "image", "source": map[string]any{"type": "base64", "media_type": "image/png", "data": imageTestData}}
	gemini := map[string]any{"inline_data": map[string]any{"mime_type": "image/png", "data": imageTestData}}
	for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata, models.ContentModeFull} {
		for _, tc := range []struct {
			name     string
			part     map[string]any
			role     string
			fullType string
			fullURL  string
			link     bool
			nested   bool
		}{
			{"openai_data", openAI, "user", "image_url", imageTestURI, false, false},
			{"openai_link", map[string]any{"type": "image_url", "image_url": map[string]any{"url": imageTestURL}}, "user", "image_url", imageTestURL, true, false},
			{"anthropic", anthropic, "user", "image", "", false, false},
			{"gemini", gemini, "user", "", "", false, false},
			{"tool_result", map[string]any{"type": "tool_result", "tool_use_id": "call-1", "content": []any{imageTestPart("tool text"), anthropic}}, "user", "tool_result", "", false, true},
			{"tool_message", openAI, "tool", "image_url", imageTestURI, false, false},
		} {
			t.Run(string(mode)+"/"+tc.name, func(t *testing.T) {
				p, _ := bodyPlugin(t, true)
				rc := newTestRC()
				rc.Privacy.ContentMode = mode
				content := imageTestJSON([]any{imageTestPart("keep this text"), tc.part})
				rc.Request.Messages = []models.Message{{Role: tc.role, Content: json.RawMessage(content)}}
				rc.Response.Choices = []models.Choice{{Message: models.Message{Role: "assistant", Content: json.RawMessage(content)}}}
				beforeIn, beforeOut := bodyJSON(rc)
				wantPart := tc.part
				marker := "[image]"
				if mode == models.ContentModeMetadata {
					marker = imageTestMetadata
					if tc.link {
						marker = imageTestLinkMetadata
					}
				}
				if mode != models.ContentModeFull {
					wantPart = imageTestPart(marker)
					if tc.nested {
						wantPart = map[string]any{"type": "tool_result", "tool_use_id": "call-1", "content": []any{imageTestPart("tool text"), wantPart}}
					}
				}
				wantContent := imageTestJSON([]any{imageTestPart("keep this text"), wantPart})
				wantIn := strings.Replace(beforeIn, content, wantContent, 1)
				wantOut := strings.Replace(beforeOut, content, wantContent, 1)
				attrs := runOnce(p, rc)
				assertImageJSONEqual(t, attrs["input.value"], wantIn)
				assertImageJSONEqual(t, attrs["output.value"], wantOut)
				want := map[string]any{}
				for _, side := range []string{"input", "output"} {
					prefix := "llm." + side + "_messages.0.message."
					role := tc.role
					if side == "output" {
						role = "assistant"
					}
					want[prefix+"role"] = role
					want[prefix+"contents.0.message_content.type"] = "text"
					want[prefix+"contents.0.message_content.text"] = "keep this text"
					base := prefix + "contents.1.message_content."
					if mode != models.ContentModeFull && !tc.nested {
						want[base+"type"] = "text"
						want[base+"text"] = marker
					} else {
						if tc.fullType != "" {
							want[base+"type"] = tc.fullType
						}
						if tc.fullURL != "" {
							want[base+"image.url"] = tc.fullURL
						}
					}
				}
				got := map[string]any{}
				for k, v := range attrs {
					if strings.HasPrefix(k, "llm.input_messages.") || strings.HasPrefix(k, "llm.output_messages.") {
						got[k] = v
					}
				}
				if !reflect.DeepEqual(got, want) {
					t.Errorf("message attributes = %#v, want %#v", got, want)
				}
				if mode != models.ContentModeFull {
					assertNoImageContent(t, attrs)
				}
				afterIn, afterOut := bodyJSON(rc)
				if beforeIn != afterIn || beforeOut != afterOut {
					t.Error("observer mutated request or response")
				}
			})
		}
	}
}

func assertImageJSONEqual(t *testing.T, got any, want string) {
	t.Helper()
	gs, ok := got.(string)
	if !ok {
		t.Errorf("JSON attribute = %v, want %s", got, want)
		return
	}
	var g, w any
	if json.Unmarshal([]byte(gs), &g) != nil || json.Unmarshal([]byte(want), &w) != nil {
		t.Fatalf("invalid JSON: got %q want %q", gs, want)
	}
	if !reflect.DeepEqual(g, w) {
		t.Errorf("JSON attribute = %s, want %s", gs, want)
	}
}

func assertNoImageContent(t *testing.T, attrs map[string]any) {
	t.Helper()
	for k, v := range attrs {
		s := imageTestJSON(v)
		for _, forbidden := range []string{imageTestData, "data:image/", "images.invalid", "private-image"} {
			if strings.Contains(s, forbidden) {
				t.Errorf("%s retained image content %q", k, forbidden)
			}
		}
	}
}

func TestImageContentModeSources(t *testing.T) {
	no := false
	for _, tc := range []struct {
		name, org, key, header, global, orgKey string
		resolved                               models.ContentMode
		storeBodies                            *bool
		want                                   string
	}{
		{name: "org_none_beats_global_full", org: "none", global: "full", orgKey: "org_id", want: "[image]"},
		{name: "auth_org_key", org: "none", global: "full", orgKey: tenant.MetadataKeyOrgID, want: "[image]"},
		{name: "key_tightens", org: "full", key: "none", global: "full", want: "[image]"},
		{name: "request_tightens", org: "full", header: "metadata", global: "full", want: imageTestMetadata},
		{name: "global_cannot_be_relaxed", org: "full", key: "full", header: "full", global: "none", want: "[image]"},
		{name: "store_bodies_tightens", org: "full", global: "full", storeBodies: &no, want: imageTestMetadata},
		{name: "resolved_none", resolved: models.ContentModeNone, global: "full", want: "[image]"},
		{name: "invalid_header_fails_closed", header: "typo", global: "full", want: "[image]"},
		{name: "all_full", org: "full", key: "full", header: "full", global: "full", want: imageTestURI},
	} {
		t.Run(tc.name, func(t *testing.T) {
			restore, err := privacy.SetDefaultContentMode(tc.global)
			if err != nil {
				t.Fatal(err)
			}
			defer restore()
			p, _ := bodyPlugin(t, true)
			store := tenant.NewStore()
			store.Set("image-org", &tenant.OrgConfig{Privacy: &tenant.PrivacyConfig{ContentMode: tc.org, Retention: &tenant.PrivacyRetentionConfig{StoreBodies: tc.storeBodies}}})
			p.SetTenantStore(store)
			rc := newTestRC()
			orgKey := tc.orgKey
			if orgKey == "" {
				orgKey = "org_id"
			}
			rc.Metadata[orgKey] = "image-org"
			rc.Metadata[privacy.KeyLogContentRequestMetadata] = tc.key
			rc.RequestHeaders = http.Header{}
			rc.RequestHeaders.Set(privacy.LogContentHeader, tc.header)
			rc.Privacy.ContentMode = tc.resolved
			rc.Request.Messages = []models.Message{{Role: "user", Content: json.RawMessage(imageTestJSON([]any{map[string]any{"type": "image_url", "image_url": map[string]any{"url": imageTestURI}}}))}}
			attrs := runOnce(p, rc)
			key := "llm.input_messages.0.message.contents.0.message_content.text"
			if tc.want == imageTestURI {
				key = "llm.input_messages.0.message.contents.0.message_content.image.url"
			}
			if attrs[key] != tc.want {
				t.Errorf("%s = %v, want %q", key, attrs[key], tc.want)
			}
			if tc.want != imageTestURI {
				assertNoImageContent(t, attrs)
			}
		})
	}
}

// Tool messages can contain JSON encoded as a string. Copies in metadata,
// scalar extras and captured headers must be filtered BEFORE their size cap.
func TestImageContentInAttributeCopies(t *testing.T) {
	for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata, models.ContentModeFull} {
		t.Run(string(mode), func(t *testing.T) {
			p, _ := bodyPlugin(t, true)
			p.captureHeaders = newHeaderMatcher([]string{"x-image-result"})
			rc := newTestRC()
			rc.Privacy.ContentMode = mode
			raw := imageTestJSON(map[string]any{"type": "image_url", "image_url": map[string]any{"url": imageTestURL}, "padding": strings.Repeat("x", 600)})
			rc.Request.Messages = []models.Message{{Role: "tool", Content: json.RawMessage(imageTestJSON(raw))}}
			rc.Metadata["tool_result"] = raw
			rc.CustomMetadataKeys = []string{"tool_result"}
			rc.CallerExtras = map[string]any{"tool_result": raw}
			rc.RequestHeaders = http.Header{"X-Image-Result": []string{raw}}
			attrs := runOnce(p, rc)
			if mode != models.ContentModeFull {
				assertNoImageContent(t, attrs)
			}
			want := raw
			if mode != models.ContentModeFull {
				marker := "[image]"
				if mode == models.ContentModeMetadata {
					marker = imageTestLinkMetadata
				}
				want = imageTestJSON(imageTestPart(marker))
			}
			if attrs["llm.input_messages.0.message.content"] != want {
				t.Errorf("tool message = %v, want %s", attrs["llm.input_messages.0.message.content"], want)
			}
			for _, k := range []string{metadataAttrPrefix + "tool_result", bodyAttrPrefix + "tool_result", headerAttrPrefix + "x-image-result"} {
				if attrs[k] != truncateAtRune(want, maxMetadataValueLen) {
					t.Errorf("%s = %v, want sanitized copy", k, attrs[k])
				}
			}
		})
	}
}

func TestImageEndpointContentModes(t *testing.T) {
	for _, mode := range []models.ContentMode{models.ContentModeNone, models.ContentModeMetadata, models.ContentModeFull} {
		for _, bodies := range []bool{false, true} {
			p, _ := bodyPlugin(t, bodies)
			rc := newTestRC()
			rc.Privacy.ContentMode = mode
			rc.EndpointType = "image"
			rc.Request, rc.Response = nil, nil
			rc.ImageRequest = &models.ImageRequest{Prompt: "keep this prompt"}
			rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{URL: imageTestURL, RevisedPrompt: "keep revised prompt"}}}
			attrs := runOnce(p, rc)
			want := imageTestURL
			if mode == models.ContentModeNone {
				want = "[image]"
			}
			if mode == models.ContentModeMetadata {
				want = imageTestLinkMetadata
			}
			if attrs["gen_ai.image.output_urls"] != want {
				t.Errorf("mode %s: URL attribute = %v, want %s", mode, attrs["gen_ai.image.output_urls"], want)
			}
			if attrs["gen_ai.image.prompt"] != "keep this prompt" || attrs["gen_ai.image.revised_prompt"] != "keep revised prompt" {
				t.Error("image text changed")
			}
			if mode != models.ContentModeFull {
				assertNoImageContent(t, attrs)
			}
			if bodies {
				assertImageJSONEqual(t, attrs["output.value"], imageTestJSON(map[string]any{"created": 0, "data": []any{map[string]any{"url": want, "revised_prompt": "keep revised prompt"}}}))
			}
		}
	}
}
