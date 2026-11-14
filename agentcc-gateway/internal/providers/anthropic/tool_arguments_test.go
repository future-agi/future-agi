package anthropic

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Replay a completed tool call through the provider's HTTP path. In addition to
// the normalized input, retain the assistant text, other calls and tool results.
func TestChatCompletion_ReplayToolArguments(t *testing.T) {
	for _, tt := range []struct {
		name      string
		arguments string
	}{
		{"empty", ""},
		{"null", "null"},
		{"whitespace", " \t\r\n "},
		{"padded_null", " \nnull\t "},
		{"truncated_object", `{"value":`},
		{"plain_text", "not JSON"},
		{"trailing_comma", `{"value":1,}`},
		{"multiple_values", `{} {}`},
	} {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPost || r.URL.Path != "/v1/messages" {
					t.Errorf("request = %s %s, want POST /v1/messages", r.Method, r.URL.Path)
				}
				var req anthropicRequest
				if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
					t.Errorf("decode replay: %v", err)
					http.Error(w, "invalid replay", http.StatusBadRequest)
					return
				}
				if len(req.Messages) != 4 {
					t.Errorf("messages = %d, want 4", len(req.Messages))
					http.Error(w, "missing messages", http.StatusBadRequest)
					return
				}
				var blocks []anthropicContentBlock
				if err := json.Unmarshal(req.Messages[1].Content, &blocks); err != nil || len(blocks) != 3 {
					t.Errorf("assistant content = %s, want text and two tool_use blocks (error: %v)", req.Messages[1].Content, err)
					http.Error(w, "missing assistant blocks", http.StatusBadRequest)
					return
				}
				if req.Messages[1].Role != "assistant" || blocks[0].Type != "text" || blocks[0].Text != "Checking both tools." {
					t.Errorf("assistant text was not preserved: %+v", blocks[0])
				}
				if blocks[1].Type != "tool_use" || blocks[1].ID != "call_ping" || blocks[1].Name != "ping" || string(blocks[1].Input) != `{}` {
					t.Errorf("normalized tool_use = %+v, input = %s, want ping/call_ping with {}", blocks[1], blocks[1].Input)
					http.Error(w, "invalid tool input", http.StatusBadRequest)
					return
				}
				if blocks[2].Type != "tool_use" || blocks[2].ID != "call_lookup" || blocks[2].Name != "lookup" || string(blocks[2].Input) != `{"value":7,"nested":{"enabled":true}}` {
					t.Errorf("valid tool_use was not preserved: %+v, input = %s", blocks[2], blocks[2].Input)
				}
				for i, id := range []string{"call_ping", "call_lookup"} {
					msg := req.Messages[i+2]
					var results []anthropicContentBlock
					if err := json.Unmarshal(msg.Content, &results); err != nil || len(results) != 1 {
						t.Errorf("tool result = %s, want one block (error: %v)", msg.Content, err)
					} else if msg.Role != "user" || results[0].Type != "tool_result" || results[0].ToolUseID != id {
						t.Errorf("tool result ID was not preserved: %+v, want %s", results, id)
					}
				}
				w.Header().Set("Content-Type", "application/json")
				_ = json.NewEncoder(w).Encode(anthropicResponse{
					ID: "msg_replay", Type: "message", Role: "assistant", Model: "test-model",
					Content:    []anthropicContentBlock{{Type: "text", Text: "Both tools completed."}},
					StopReason: "end_turn", Usage: anthropicUsage{InputTokens: 20, OutputTokens: 5},
				})
			}))
			defer server.Close()
			p := newTestProvider(t, server.URL)
			defer p.Close()

			resp, err := p.ChatCompletion(context.Background(), &models.ChatCompletionRequest{
				Model: "test-model",
				Messages: []models.Message{
					{Role: "user", Content: json.RawMessage(`"Check both tools."`)},
					{Role: "assistant", Content: json.RawMessage(`"Checking both tools."`), ToolCalls: []models.ToolCall{
						{ID: "call_ping", Type: "function", Function: models.FunctionCall{Name: "ping", Arguments: tt.arguments}},
						{ID: "call_lookup", Type: "function", Function: models.FunctionCall{Name: "lookup", Arguments: " \n{\"value\":7,\"nested\":{\"enabled\":true}}\t "}},
					}},
					{Role: "tool", ToolCallID: "call_ping", Content: json.RawMessage(`"pong"`)},
					{Role: "tool", ToolCallID: "call_lookup", Content: json.RawMessage(`"found"`)},
				},
				Tools: []models.Tool{
					{Type: "function", Function: models.ToolFunction{Name: "ping", Parameters: json.RawMessage(`{"type":"object","properties":{}}`)}},
					{Type: "function", Function: models.ToolFunction{Name: "lookup", Parameters: json.RawMessage(`{"type":"object","properties":{"value":{"type":"integer"},"nested":{"type":"object"}}}`)}},
				},
			})
			if err != nil {
				t.Fatalf("tool replay failed: %v", err)
			}
			if len(resp.Choices) != 1 || resp.Choices[0].FinishReason != "stop" || string(resp.Choices[0].Message.Content) != `"Both tools completed."` {
				t.Errorf("unexpected replay response: %+v", resp)
			}
		})
	}
}
