package anthropic_test

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Partial usage is not a terminal report. Retain presence across the finish
// and merge the later counter, including an explicitly measured zero.
func TestStreamSplitUsageAtFinish(t *testing.T) {
	for _, completion := range []string{`{"completion_tokens":3}`, `{"completion_tokens":0}`} {
		for _, atFinish := range []bool{false, true} {
			name := completion + "/prefix"
			if atFinish {
				name = completion + "/finish"
			}
			t.Run(name, func(t *testing.T) {
				var prompt, output models.Usage
				if err := json.Unmarshal([]byte(`{"prompt_tokens":8}`), &prompt); err != nil {
					t.Fatal(err)
				}
				if err := json.Unmarshal([]byte(completion), &output); err != nil {
					t.Fatal(err)
				}
				start := models.StreamChunk{ID: "split", Model: "acme/token", Usage: &prompt}
				finish := models.StreamChunk{ID: "split", Model: "acme/token", Choices: []models.StreamChoice{{FinishReason: strPtr("stop")}}}
				if atFinish {
					start.Usage, finish.Usage = nil, &prompt
				}
				chunks := []models.StreamChunk{start, finish}
				for i := 0; i < 300; i++ {
					chunks = append(chunks, models.StreamChunk{ID: "split", Model: "acme/token"})
				}
				chunks = append(chunks, models.StreamChunk{ID: "split", Model: "acme/token", Usage: &output})
				events, errs := tr.StreamEventsFromCanonical(context.Background(), feedChunks(chunks))
				seen := collectEvents(t, events, errs)
				starts, stops, deltas := 0, 0, 0
				for _, event := range seen {
					switch event["_event"] {
					case "message_start":
						starts++
					case "message_stop":
						stops++
					case "message_delta":
						deltas++
						u := event["usage"].(map[string]interface{})
						if u["input_tokens"] != float64(8) || u["output_tokens"] != float64(output.CompletionTokens) {
							t.Errorf("terminal usage = %v, want input=8 output=%d", u, output.CompletionTokens)
						}
					}
				}
				if starts != 1 || stops != 1 || deltas != 1 {
					t.Errorf("message starts=%d stops=%d deltas=%d, want one of each", starts, stops, deltas)
				}
			})
		}
	}
}
