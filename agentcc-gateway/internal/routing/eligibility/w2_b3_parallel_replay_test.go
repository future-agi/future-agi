package eligibility

import (
	"encoding/json"
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Gemini signs the parallel-call group, not every sibling call.
// That signature must not cover an unsigned call in another assistant turn.
func TestW2B3ParallelSignatureScope(t *testing.T) {
	for _, managed := range []bool{false, true} {
		for _, unsignedTurn := range []string{"none", "before", "after"} {
			t.Run(fmt.Sprintf("managed=%t/unsigned=%s", managed, unsignedTurn), func(t *testing.T) {
				var group models.Message
				if err := json.Unmarshal([]byte(`{"role":"assistant","tool_calls":[{"id":"call_weather","type":"function","function":{"name":"weather","arguments":"{}"},"extra_content":{"google":{"thought_signature":"synthetic-signature"}}},{"id":"call_time","type":"function","function":{"name":"time","arguments":"{}"}}]}`), &group); err != nil {
					t.Fatal(err)
				}
				messages := []models.Message{group,
					{Role: "tool", ToolCallID: "call_weather", Content: json.RawMessage(`"Sunny"`)},
					{Role: "tool", ToolCallID: "call_time", Content: json.RawMessage(`"Noon"`)},
				}
				unsigned := []models.Message{
					{Role: "assistant", ToolCalls: []models.ToolCall{{ID: "call_unsigned", Type: "function", Function: models.FunctionCall{Name: "weather", Arguments: "{}"}}}},
					{Role: "tool", ToolCallID: "call_unsigned", Content: json.RawMessage(`"Done"`)},
				}
				switch unsignedTurn {
				case "before":
					messages = append(unsigned, messages...)
				case "after":
					messages = append(messages, unsigned...)
				}
				in := byokIn(nil)
				if managed {
					in = managedIn(nil, nil)
				}
				in.Model, in.Aggregator = "google/gemini-3.5-flash", true
				in.Request = &models.ChatCompletionRequest{Model: in.Model, Messages: messages}
				got := Check(&in)
				if got.OK != (unsignedTurn == "none") {
					t.Fatalf("parallel group replay: %+v, unsigned separate turn=%s", got, unsignedTurn)
				}
				if !got.OK && got.Code != CodeToolHistoryIncompatible {
					t.Fatalf("wrong refusal: %+v", got)
				}
			})
		}
	}
}
