package anthropic_test

import (
	"context"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"testing"
)

func TestStreamSplitToolIdentity(t *testing.T) {
	for _, order := range []string{"ina", "ian", "nia", "nai", "ain", "ani"} {
		t.Run(order, func(t *testing.T) {
			var chunks []models.StreamChunk
			for _, part := range order {
				d := models.ToolCallDelta{Index: 0}
				switch part {
				case 'i':
					d.ID = "split_id"
				case 'n':
					d.Function = &models.FunctionCall{Name: "lookup"}
				case 'a':
					d.Function = &models.FunctionCall{Arguments: "{}"}
				}
				chunks = append(chunks, models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{ToolCalls: []models.ToolCallDelta{d}}}}})
			}
			chunks = append(chunks, models.StreamChunk{Choices: []models.StreamChoice{{FinishReason: strPtr("tool_calls")}}, Usage: &models.Usage{CompletionTokens: 2}})
			events, errs := tr.StreamEventsFromCanonical(context.Background(), feedChunks(chunks))
			got := collectEvents(t, events, errs)
			starts, deltas, stops := 0, 0, 0
			for _, e := range got {
				switch e["_event"] {
				case "content_block_start":
					starts++
					b := e["content_block"].(map[string]interface{})
					if b["id"] != "split_id" || b["name"] != "lookup" {
						t.Errorf("identity=%v", b)
					}
				case "content_block_delta":
					deltas++
				case "content_block_stop":
					stops++
				}
			}
			if starts != 1 || deltas != 1 || stops != 1 {
				t.Fatalf("starts=%d deltas=%d stops=%d", starts, deltas, stops)
			}
		})
	}
}
