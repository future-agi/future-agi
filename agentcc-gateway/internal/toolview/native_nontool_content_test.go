package toolview

import (
	"context"
	"testing"
)

// Only declarations differ in the oracle; content and extension bytes must survive.
func assertNonToolStrip(t *testing.T, messages bool, fields string, refuse bool) {
	t.Helper()
	tools := `"tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}]`
	kept := `"tools":[{"type":"function","name":"keep"}]`
	makeView := Responses
	if messages {
		tools = `"tools":[{"name":"keep","input_schema":{}},{"name":"drop","input_schema":{}}]`
		kept = `"tools":[{"name":"keep","input_schema":{}}]`
		makeView = Anthropic
	}
	body := " \n{" + fields + "," + tools + `,"opaque":{"name":"drop","tool_choice":"drop","n":1e+03}}` + "\n "
	r := makeView([]byte(body))
	if !refuse {
		if chat, why := r.ChatRequest(context.Background()); why != "" || chat == nil {
			t.Fatal("non-tool fixture is not accepted by the inherited reader")
		}
	}
	generation := r.Generation()
	compatReference(t, r, refuse)
	if !refuse {
		want := " \n{" + fields + "," + kept + `,"opaque":{"name":"drop","tool_choice":"drop","n":1e+03}}` + "\n "
		if string(r.Body()) != want || r.Generation() != generation+1 {
			t.Fatal("non-tool strip changed content bytes or committed more than once")
		}
	}
}

func TestNativeStripSupportedNonToolContent(t *testing.T) {
	for _, tc := range []struct{ name, fields string }{
		{"input-string", `"input":"drop"`},
		{"message-string", `"input":[{"type":"message","role":"user","content":"drop"}]`},
		{"reasoning-summary", `"input":[{"type":"reasoning","id":"r1","summary":[{"type":"summary_text","text":"drop"}]},{"role":"user","content":"hello"}]`},
		{"reasoning-encrypted", `"input":[{"type":"reasoning","summary":[],"encrypted_content":"drop","status":"completed"},{"role":"user","content":"hello"}]`},
		{"text-parts", `"input":[{"role":"assistant","content":[{"type":"input_text","text":"drop"},{"type":"output_text","text":"drop"},{"type":"text","text":"drop"},{"type":"summary_text","text":"drop"},{"type":"refusal","refusal":"drop"}]}]`},
		{"image-url", `"input":[{"role":"user","content":[{"type":"input_image","image_url":"drop"}]}]`},
		{"image-file", `"input":[{"role":"user","content":[{"type":"input_image","file_id":"f1"}]}]`},
		{"file-id", `"input":[{"role":"user","content":[{"type":"input_file","file_id":"f1"}]}]`},
		{"file-data", `"input":[{"role":"user","content":[{"type":"input_file","filename":"drop","file_data":"drop"}]}]`},
		{"audio", `"input":[{"role":"user","content":[{"type":"input_audio","input_audio":{"data":"drop","format":"wav"}}]}]`},
	} {
		t.Run("responses/"+tc.name, func(t *testing.T) { assertNonToolStrip(t, false, tc.fields, false) })
	}
	for _, tc := range []struct{ name, block string }{
		{"text", `{"type":"text","text":"drop","cache_control":{"type":"ephemeral"}}`},
		{"image-base64", `{"type":"image","source":{"type":"base64","media_type":"image/png","data":"drop"}}`},
		{"image-url", `{"type":"image","source":{"type":"url","url":"drop"}}`},
		{"image-file", `{"type":"image","source":{"type":"file","file_id":"f1"}}`},
		{"document-text", `{"type":"document","title":"drop","context":"drop","source":{"type":"text","media_type":"text/plain","data":"drop"}}`},
		{"document-pdf", `{"type":"document","source":{"type":"base64","media_type":"application/pdf","data":"drop"}}`},
		{"document-url", `{"type":"document","source":{"type":"url","url":"drop"}}`},
		{"document-file", `{"type":"document","source":{"type":"file","file_id":"f1"}}`},
		{"document-string", `{"type":"document","source":{"type":"content","content":"drop"}}`},
		{"document-blocks", `{"type":"document","source":{"type":"content","content":[{"type":"text","text":"drop"}]}}`},
		{"search-result", `{"type":"search_result","source":"https://example.invalid","title":"drop","content":[{"type":"text","text":"drop"}]}`},
		{"document-search", `{"type":"document","source":{"type":"content","content":[{"type":"search_result","source":"s","title":"drop","content":[{"type":"text","text":"drop"}]}]}}`},
		{"thinking", `{"type":"thinking","thinking":"drop","signature":"sig"}`},
		{"redacted-thinking", `{"type":"redacted_thinking","data":"drop"}`},
		{"container-upload", `{"type":"container_upload","file_id":"f1"}`},
	} {
		t.Run("messages/"+tc.name, func(t *testing.T) {
			assertNonToolStrip(t, true, `"messages":[{"role":"user","content":[`+tc.block+`]}],"system":[{"type":"text","text":"drop"}]`, false)
		})
	}
}

func TestNativeStripNonToolContentClosedShapes(t *testing.T) {
	for _, tc := range []struct{ name, item string }{
		{"reasoning-missing-summary", `{"type":"reasoning","id":"r1"}`},
		{"reasoning-summary-scalar", `{"type":"reasoning","summary":"drop"}`},
		{"reasoning-summary-text-number", `{"type":"reasoning","summary":[{"type":"summary_text","text":7}]}`},
		{"reasoning-summary-reference", `{"type":"reasoning","summary":[{"type":"function_call","name":"keep"}]}`},
		{"reasoning-id-number", `{"type":"reasoning","id":7,"summary":[]}`},
		{"reasoning-encrypted-number", `{"type":"reasoning","summary":[],"encrypted_content":7}`},
		{"unknown-item", `{"type":"future_call","name":"keep"}`},
		{"stored-item", `{"type":"item_reference","id":"r1"}`},
		{"unresolved-output", `{"type":"function_call_output","call_id":"c1","output":"hello"}`},
	} {
		t.Run("responses/"+tc.name, func(t *testing.T) { assertNonToolStrip(t, false, `"input":[`+tc.item+`]`, true) })
	}
	for _, tc := range []struct{ name, block string }{
		{"text-number", `{"type":"input_text","text":7}`},
		{"image-url-number", `{"type":"input_image","image_url":7}`},
		{"file-id-number", `{"type":"input_file","file_id":7}`},
		{"audio-scalar", `{"type":"input_audio","input_audio":"data"}`},
		{"unknown-content", `{"type":"future_content","text":"hello"}`},
	} {
		t.Run("responses/"+tc.name, func(t *testing.T) {
			assertNonToolStrip(t, false, `"input":[{"role":"user","content":[`+tc.block+`]}]`, true)
		})
	}
	for _, tc := range []struct{ name, block string }{
		{"search-source-number", `{"type":"search_result","source":7,"title":"t","content":[]}`},
		{"search-title-number", `{"type":"search_result","source":"s","title":7,"content":[]}`},
		{"search-content-scalar", `{"type":"search_result","source":"s","title":"t","content":"text"}`},
		{"search-nested-call", `{"type":"search_result","source":"s","title":"t","content":[{"type":"tool_use","name":"keep"}]}`},
		{"search-nested-unknown", `{"type":"search_result","source":"s","title":"t","content":[{"type":"future_block","text":"hello"}]}`},
		{"text-number", `{"type":"text","text":7}`},
		{"image-source-scalar", `{"type":"image","source":"file"}`},
		{"document-nested-result", `{"type":"document","source":{"type":"content","content":[{"type":"tool_result","tool_use_id":"c1","content":"hello"}]}}`},
		{"document-nested-unknown", `{"type":"document","source":{"type":"content","content":[{"type":"future_block","text":"hello"}]}}`},
		{"thinking-number", `{"type":"thinking","thinking":7,"signature":"sig"}`},
		{"redacted-number", `{"type":"redacted_thinking","data":7}`},
		{"container-file-number", `{"type":"container_upload","file_id":7}`},
		{"unknown-block", `{"type":"future_block","text":"hello"}`},
		{"unresolved-result", `{"type":"tool_result","tool_use_id":"c1","content":"hello"}`},
	} {
		t.Run("messages/"+tc.name, func(t *testing.T) {
			assertNonToolStrip(t, true, `"messages":[{"role":"user","content":[`+tc.block+`]}]`, true)
		})
	}
	for _, field := range []string{"previous_response_id", "conversation"} {
		t.Run("responses/"+field, func(t *testing.T) { assertNonToolStrip(t, false, `"input":"hello","`+field+`":"stored"`, true) })
	}
}
