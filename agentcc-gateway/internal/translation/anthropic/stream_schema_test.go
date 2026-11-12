package anthropic_test

import (
	"context"
	"encoding/json"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Compare entire event payloads, not unmarshaled zero values: absent, null,
// empty string and empty array have different meanings to SDK accumulators.
func TestStreamSchemaGolden(t *testing.T) {
	for _, tc := range []struct {
		name, reason string
		deltas       []models.Delta
		blocks       []string
	}{
		{"text", "stop", []models.Delta{{Content: strPtr("Hello")}, {Content: strPtr(" world")}}, []string{
			`{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}`,
			`{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}`,
			`{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" world"}}`,
			`{"type":"content_block_stop","index":0}`,
		}},
		{"tool", "tool_calls", []models.Delta{
			{ToolCalls: []models.ToolCallDelta{{Index: 0, ID: "toolu_test", Function: &models.FunctionCall{Name: "weather", Arguments: `{"city":`}}}},
			{ToolCalls: []models.ToolCallDelta{{Index: 0, Function: &models.FunctionCall{Arguments: `"Paris"}`}}}},
		}, []string{
			`{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_test","name":"weather","input":{}}}`,
			`{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\"city\":"}}`,
			`{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"\"Paris\"}"}}`,
			`{"type":"content_block_stop","index":0}`,
		}},
		// thinking is unsupported on requests translated to canonical format.
		// Reasoning must not create blocks, split text or alter reported usage.
		{"reasoning_before_text", "stop", []models.Delta{{ReasoningContent: strPtr("Consider")}, {Content: strPtr("Hello")}}, []string{
			`{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}`,
			`{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}`,
			`{"type":"content_block_stop","index":0}`,
		}},
		{"reasoning_interleaved", "stop", []models.Delta{
			{Content: strPtr("Hello"), ReasoningContent: strPtr("Consider")},
			{ReasoningContent: strPtr("more")},
			{Content: strPtr(" world")},
			{ReasoningContent: strPtr("after")},
		}, []string{
			`{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}`,
			`{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}`,
			`{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" world"}}`,
			`{"type":"content_block_stop","index":0}`,
		}},
		{"reasoning_with_tool", "tool_calls", []models.Delta{
			{ReasoningContent: strPtr("Consider")},
			{ReasoningContent: strPtr("more"), ToolCalls: []models.ToolCallDelta{{Index: 0, ID: "toolu_test", Function: &models.FunctionCall{Name: "weather", Arguments: `{}`}}}},
		}, []string{
			`{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_test","name":"weather","input":{}}}`,
			`{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{}"}}`,
			`{"type":"content_block_stop","index":0}`,
		}},
		{"reasoning_only", "stop", []models.Delta{{ReasoningContent: strPtr("Consider")}}, nil},
		{"empty", "length", nil, nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			chunks := []models.StreamChunk{{ID: "msg_test", Model: "mock-model", Usage: models.MeasuredUsage(7, 0)}}
			for _, delta := range tc.deltas {
				chunks = append(chunks, models.StreamChunk{Choices: []models.StreamChoice{{Delta: delta}}})
			}
			chunks = append(chunks, models.StreamChunk{Choices: []models.StreamChoice{{FinishReason: &tc.reason}}}, models.StreamChunk{Usage: models.MeasuredUsage(9, 4)})
			events, errs := tr.StreamEventsFromCanonical(context.Background(), feedChunks(chunks))
			var got []any
			for frame := range events {
				parsed := parseSSEFrame(t, frame)
				if parsed["_event"] != parsed["type"] {
					t.Errorf("event name mismatch: %s", frame)
				}
				delete(parsed, "_event")
				got = append(got, parsed)
			}
			for err := range errs {
				t.Fatal(err)
			}
			wantJSON := []string{`{"type":"message_start","message":{"id":"msg_test","type":"message","role":"assistant","model":"mock-model","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":7,"output_tokens":0}}}`}
			wantJSON = append(wantJSON, tc.blocks...)
			reason := map[string]string{"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}[tc.reason]
			wantJSON = append(wantJSON, `{"type":"message_delta","delta":{"stop_reason":"`+reason+`","stop_sequence":null},"usage":{"input_tokens":9,"output_tokens":4}}`, `{"type":"message_stop"}`)
			var want []any
			for _, raw := range wantJSON {
				var value any
				if err := json.Unmarshal([]byte(raw), &value); err != nil {
					t.Fatal(err)
				}
				want = append(want, value)
			}
			if !reflect.DeepEqual(got, want) {
				g, _ := json.Marshal(got)
				w, _ := json.Marshal(want)
				t.Fatalf("event sequence\ngot  %s\nwant %s", g, w)
			}
		})
	}
}
