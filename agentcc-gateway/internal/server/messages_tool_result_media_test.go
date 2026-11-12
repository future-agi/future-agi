package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"
)

const wantMessagesToolResultMediaMessage = "Images or other media inside tool_result blocks can't be sent to this model's provider through /v1/messages yet. Use an Anthropic model with native pass-through (without automatic routing, retries or fallbacks), or put the image in a user content block outside tool_result."

const wantMessagesRoutingTranslationMessage = "This Messages request contains native-only fields that cannot be preserved by the configured routing chain. Use a canonical-compatible Messages request or a route without automatic routing, retries or fallbacks."

func assertMessagesToolResultMediaRefusal(t *testing.T, w *httptest.ResponseRecorder) {
	t.Helper()
	var got map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
		t.Fatalf("invalid error envelope: %v; body=%s", err, w.Body)
	}
	want := map[string]any{
		"type": "error",
		"error": map[string]any{
			"type":    "invalid_request_error",
			"code":    "messages_tool_result_media_unsupported",
			"message": wantMessagesToolResultMediaMessage,
		},
	}
	if w.Code != http.StatusBadRequest || w.Header().Get("Content-Type") != "application/json" || !reflect.DeepEqual(got, want) {
		t.Errorf("want typed, actionable HTTP 400 JSON refusal; got status=%d content-type=%q body=%s", w.Code, w.Header().Get("Content-Type"), w.Body)
	}
}

// Classify the cause, not every failure of the text-only allowlist. Text with
// native annotations and malformed content must retain their existing refusal.
func TestMessagesToolResultRefusalClassification(t *testing.T) {
	for _, tc := range []struct {
		name, content, code string
	}{
		{"string", `"result"`, ""},
		{"text", `[{"type":"text","text":"result"}]`, ""},
		{"image", `[{"type":"image","source":{"type":"url","url":"https://example.invalid/image.png"}}]`, "messages_tool_result_media_unsupported"},
		{"document", `[{"type":"document","source":{"type":"base64","media_type":"application/pdf","data":"cGRm"}}]`, "messages_tool_result_media_unsupported"},
		{"audio", `[{"type":"audio","source":{"type":"base64","media_type":"audio/wav","data":"YXVkaW8="}}]`, "messages_tool_result_media_unsupported"},
		{"video", `[{"type":"video","source":{"type":"url","url":"https://example.invalid/video.mp4"}}]`, "messages_tool_result_media_unsupported"},
		{"annotated_text_and_image", `[{"type":"text","text":"result","cache_control":{"type":"ephemeral"}},{"type":"image","source":{"type":"url","url":"https://example.invalid/image.png"}}]`, "messages_tool_result_media_unsupported"},
		{"tool_use", `[{"type":"tool_use","id":"nested","name":"capture","input":{}}]`, "messages_routing_translation_unsupported"},
		{"tool_result", `[{"type":"tool_result","tool_use_id":"nested","content":"result"}]`, "messages_routing_translation_unsupported"},
		{"invalid_type", `[{"type":42,"text":"result"}]`, "messages_routing_translation_unsupported"},
		{"annotated_text", `[{"type":"text","text":"result","cache_control":{"type":"ephemeral"}}]`, "messages_routing_translation_unsupported"},
		{"object_not_array", `{"type":"text","text":"result"}`, "messages_routing_translation_unsupported"},
		{"untyped_block", `[{"text":"result"}]`, "messages_routing_translation_unsupported"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			err := messagesToolResultTranslationError([]byte(messagesToolResultBody("org-model", false, tc.content)))
			if tc.code == "" {
				if err != nil {
					t.Fatalf("text-only result refused: %v", err)
				}
				return
			}
			wantMessage := wantMessagesToolResultMediaMessage
			if tc.code == "messages_routing_translation_unsupported" {
				wantMessage = wantMessagesRoutingTranslationMessage
			}
			if err == nil || err.Status != http.StatusBadRequest || err.Type != "invalid_request_error" || err.Code != tc.code || err.Message != wantMessage {
				t.Fatalf("wrong refusal: got %+v; want code=%s message=%q", err, tc.code, wantMessage)
			}
		})
	}
}

// The generic refusal historically has an internal stable code but no wire
// error.code. Preserve both that envelope and its exact message for non-media.
func TestMessagesToolResultOtherRefusalsUnchanged(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, reason := range []string{"thinking", "annotated_tool_text"} {
			t.Run(fmt.Sprintf("%s/stream=%v", reason, stream), func(t *testing.T) {
				srv, up := messagesRoutingGateway(t, true, `{"model_fallbacks":{"org-model":["org-model-2"]}}`)
				body := fmt.Sprintf(`{"model":"org-model","max_tokens":32,"stream":%v,"messages":[{"role":"user","content":"hello"}],"thinking":{"type":"enabled","budget_tokens":1024}}`, stream)
				if reason == "annotated_tool_text" {
					body = messagesToolResultBody("org-model", stream, `[{"type":"text","text":"result","cache_control":{"type":"ephemeral"}}]`)
				}
				err := messagesRoutedTranslationError([]byte(body))
				if err == nil || err.Code != "messages_routing_translation_unsupported" || err.Message != wantMessagesRoutingTranslationMessage {
					t.Fatalf("generic refusal changed: %+v", err)
				}
				w := postMessages(t, srv, "/v1/messages", r7CustA, body)
				var got map[string]any
				if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
					t.Fatalf("invalid error envelope: %v", err)
				}
				want := map[string]any{
					"type": "error",
					"error": map[string]any{
						"type":    "invalid_request_error",
						"message": wantMessagesRoutingTranslationMessage,
					},
				}
				if w.Code != http.StatusBadRequest || w.Header().Get("Content-Type") != "application/json" || !reflect.DeepEqual(got, want) || up.count() != 0 {
					t.Fatalf("generic refusal changed: status=%d body=%s calls=%v", w.Code, w.Body, up.snapshot())
				}
			})
		}
	}
}
