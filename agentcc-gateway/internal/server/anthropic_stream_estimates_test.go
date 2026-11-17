package server

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/translation"
)

func TestAnthropicStreamEstimatesPreserveReportedDimensions(t *testing.T) {
	for _, tc := range []struct {
		name, report  string
		input, output int
	}{
		{"absent", "null", 8, 3},
		{"measured zero", `{"prompt_tokens":0,"completion_tokens":0}`, 0, 0},
		{"only input", `{"prompt_tokens":0}`, 0, 3},
		{"only output", `{"completion_tokens":0}`, 8, 0},
		{"measured", `{"prompt_tokens":2,"completion_tokens":1}`, 2, 1},
		{"cost only", `{"cost":0.001}`, 0, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var report *models.Usage
			if err := json.Unmarshal([]byte(tc.report), &report); err != nil {
				t.Fatal(err)
			}
			in := make(chan models.StreamChunk, 2)
			text := "Hello world"
			in <- models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}}
			in <- models.StreamChunk{Usage: report}
			close(in)
			prompt := models.MeasuredUsage(8, 0)
			prompt.Estimated = true
			var got []models.StreamChunk
			for chunk := range anthropicStreamEstimates(context.Background(), in, prompt) {
				got = append(got, chunk)
			}
			if len(got) != 3 {
				t.Fatalf("want original chunks plus terminal usage; got %v", got)
			}
			if got[0].Usage.PromptTokens != 8 || got[0].Usage.CompletionTokens != 0 {
				t.Fatalf("wrong initial estimate: %+v", got[0].Usage)
			}
			last := got[2].Usage
			if last.PromptTokens != tc.input || last.CompletionTokens != tc.output {
				t.Fatalf("final usage %+v, want input=%d output=%d", last, tc.input, tc.output)
			}
			if got[1].Usage != report || prompt.CompletionTokens != 0 {
				t.Fatal("changed provider evidence or the caller's estimate")
			}
			// The final report must also replace the start estimate inside the
			// translator's cumulative usage, including cost-only reports for
			// which chat deliberately does not estimate token counts.
			translated := make(chan models.StreamChunk, len(got)+1)
			for _, chunk := range got {
				translated <- chunk
			}
			stop := "stop"
			translated <- models.StreamChunk{Choices: []models.StreamChoice{{FinishReason: &stop}}}
			close(translated)
			tr, ok := translation.InboundFor("anthropic")
			if !ok {
				t.Fatal("translator missing")
			}
			events, errs := tr.StreamEventsFromCanonical(context.Background(), translated)
			found := false
			for frame := range events {
				if !strings.HasPrefix(string(frame), "event: message_delta\n") {
					continue
				}
				found = true
				var event struct {
					Usage struct {
						Input  int `json:"input_tokens"`
						Output int `json:"output_tokens"`
					} `json:"usage"`
				}
				data := strings.SplitN(string(frame), "data: ", 2)[1]
				if err := json.Unmarshal([]byte(data), &event); err != nil {
					t.Fatal(err)
				}
				if event.Usage.Input != tc.input || event.Usage.Output != tc.output {
					t.Errorf("stale start estimate in final event: %s", frame)
				}
			}
			for err := range errs {
				t.Fatal(err)
			}
			if !found {
				t.Fatal("missing message_delta")
			}
		})
	}
}
