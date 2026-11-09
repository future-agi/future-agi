package openai

import (
	"encoding/json"
	"testing"
)

// The answer the output checks read from a Responses API body: the text of
// every output_text part of its message items (not a refusal, not reasoning),
// and its function calls.
func TestResponsesAnswer(t *testing.T) {
	body := []byte(`{"id":"resp_1","status":"completed","output":[
		{"type":"reasoning","summary":[{"type":"summary_text","text":"not the answer"}]},
		{"type":"message","role":"assistant","content":[{"type":"output_text","text":"You can run DROP "},{"type":"refusal","refusal":"no"},{"type":"output_text","text":"TABLE users;"}]},
		{"type":"function_call","call_id":"call_1","name":"db_write","arguments":"{\"sql\":\"DROP TABLE users\"}"}],
		"usage":{"input_tokens":5,"output_tokens":9,"total_tokens":14}}`)
	answer, err := ResponsesAnswer(body)
	if err != nil {
		t.Fatal(err)
	}
	if len(answer.Choices) != 1 {
		t.Fatalf("choices %+v, want one", answer.Choices)
	}
	var text string
	if err := json.Unmarshal(answer.Choices[0].Message.Content, &text); err != nil || text != "You can run DROP TABLE users;" {
		t.Errorf("text %q (%v), want the output_text parts joined", text, err)
	}
	calls := answer.Choices[0].Message.ToolCalls
	if len(calls) != 1 || calls[0].Function.Name != "db_write" || calls[0].ID != "call_1" {
		t.Errorf("tool calls %+v, want db_write", calls)
	}
	if answer.Choices[0].FinishReason != "tool_calls" {
		t.Errorf("finish %q, want tool_calls", answer.Choices[0].FinishReason)
	}
	if _, err := ResponsesAnswer([]byte("not json")); err == nil {
		t.Error("a body that is not JSON was read")
	}
}
