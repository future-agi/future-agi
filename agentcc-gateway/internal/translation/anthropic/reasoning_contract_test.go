package anthropic_test

import (
	"encoding/json"
	"fmt"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestReasoningUnsupportedRequestContract(t *testing.T) {
	for _, thinking := range []string{"", `,"thinking":{"type":"adaptive"}`} {
		for _, stream := range []string{"false", "true"} {
			t.Run("stream="+stream+thinking, func(t *testing.T) {
				req, drops, err := tr.RequestToCanonical([]byte(`{"model":"mock-model","max_tokens":64,"messages":[{"role":"user","content":"hi"}],"stream":` + stream + thinking + `}`))
				if err != nil {
					t.Fatal(err)
				}
				var want []string
				if thinking != "" {
					want = []string{"thinking_unsupported"}
				}
				if len(drops) != len(want) || (len(want) > 0 && !reflect.DeepEqual(drops, want)) {
					t.Fatalf("translation drops = %v, want %v", drops, want)
				}
				if _, ok := req.Extra["thinking"]; ok {
					t.Fatal("unsupported thinking forwarded upstream")
				}
			})
		}
	}
}

// The same reasoning-bearing answer as the stream goldens must expose only
// supported content. Usage stays the upstream report; no reasoning text is
// folded into content or usage, and missing usage is not invented here.
func TestResponseSchemaDropsReasoningGolden(t *testing.T) {
	for _, tc := range []struct {
		name, content, reason, blocks string
		tools                         []models.ToolCall
	}{
		{"text", `"Hello"`, "stop", `[{"type":"text","text":"Hello"}]`, nil},
		{"tool", `null`, "tool_calls", `[{"type":"tool_use","id":"toolu_test","name":"weather","input":{}}]`, []models.ToolCall{{ID: "toolu_test", Type: "function", Function: models.FunctionCall{Name: "weather", Arguments: `{}`}}}},
		{"reasoning_only", `null`, "stop", `[]`, nil},
	} {
		for _, measured := range []bool{false, true} {
			for _, mapping := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/measured=%v/mapping=%v", tc.name, measured, mapping), func(t *testing.T) {
					resp := &models.ChatCompletionResponse{
						ID: "msg_test", Model: "mock-model",
						Choices: []models.Choice{{Message: models.Message{
							Role: "assistant", Content: json.RawMessage(tc.content),
							ReasoningContent: "Consider", ToolCalls: tc.tools,
						}, FinishReason: tc.reason}},
					}
					usage := `{"input_tokens":0,"output_tokens":0}`
					if measured {
						resp.Usage = models.MeasuredUsage(9, 4)
						usage = `{"input_tokens":9,"output_tokens":4}`
					}
					var body []byte
					var err error
					if mapping {
						body, err = tr.ResponseFromCanonicalWithMapping(resp, map[string]string{"weather": "weather"})
					} else {
						body, err = tr.ResponseFromCanonical(resp)
					}
					if err != nil {
						t.Fatal(err)
					}
					reason := map[string]string{"stop": "end_turn", "tool_calls": "tool_use"}[tc.reason]
					want := []byte(`{"id":"msg_test","type":"message","role":"assistant","model":"mock-model","content":` + tc.blocks + `,"stop_reason":"` + reason + `","stop_sequence":null,"usage":` + usage + `}`)
					if !reflect.DeepEqual(decodeResponse(t, body), decodeResponse(t, want)) {
						t.Fatalf("response\ngot  %s\nwant %s", body, want)
					}
					if resp.Choices[0].Message.ReasoningContent != "Consider" || (measured && resp.Usage.CompletionTokens != 4) {
						t.Fatal("translation mutated canonical evidence")
					}
				})
			}
		}
	}
}
