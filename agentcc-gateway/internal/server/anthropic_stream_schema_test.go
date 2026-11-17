package server

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
)

func TestManagedMessagesStreamSchemaAndEstimatedUsage(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))
	g.managed.noUsage.Store(true)
	status, body := streamManaged(t, g, "/v1/messages", false)
	if status != 200 {
		t.Fatalf("status %d: %s", status, body)
	}
	var start, end map[string]any
	for _, frame := range strings.Split(body, "\n\n") {
		parts := strings.SplitN(frame, "\ndata: ", 2)
		if len(parts) != 2 {
			continue
		}
		var event map[string]any
		if err := json.Unmarshal([]byte(parts[1]), &event); err != nil {
			t.Fatal(err)
		}
		switch event["type"] {
		case "message_start":
			message := event["message"].(map[string]any)
			for _, field := range []string{"stop_reason", "stop_sequence"} {
				if v, ok := message[field]; !ok || v != nil {
					t.Errorf("message_start.%s must be explicit null: %v", field, message)
				}
			}
			if content, ok := message["content"].([]any); !ok || len(content) != 0 {
				t.Errorf("message_start.content must be []: %v", message)
			}
			start = message["usage"].(map[string]any)
		case "content_block_start":
			block := event["content_block"].(map[string]any)
			if block["type"] == "text" && block["text"] != "" {
				t.Errorf("text block must start with explicit empty text: %v", block)
			}
		case "message_delta":
			end = event["usage"].(map[string]any)
		}
	}
	status, chat := streamManaged(t, g, "/v1/chat/completions", false)
	if status != 200 {
		t.Fatalf("chat status %d: %s", status, chat)
	}
	var usage struct {
		PromptTokens     int `json:"prompt_tokens"`
		CompletionTokens int `json:"completion_tokens"`
	}
	for _, line := range strings.Split(chat, "\n") {
		var event struct {
			Usage json.RawMessage `json:"usage"`
		}
		if strings.HasPrefix(line, "data: ") && json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &event) == nil && len(event.Usage) > 0 && string(event.Usage) != "null" {
			if err := json.Unmarshal(event.Usage, &usage); err != nil {
				t.Fatal(err)
			}
		}
	}
	if usage.PromptTokens <= 0 || usage.CompletionTokens <= 0 {
		t.Fatalf("chat did not report an estimate: %s", chat)
	}
	if start["input_tokens"] != float64(usage.PromptTokens) || start["output_tokens"] != float64(0) || end["input_tokens"] != float64(usage.PromptTokens) || end["output_tokens"] != float64(usage.CompletionTokens) {
		t.Fatalf("Messages estimates start=%v final=%v differ from chat %+v", start, end, usage)
	}
}
