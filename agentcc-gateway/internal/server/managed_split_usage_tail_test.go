package server

import (
	"encoding/json"
	"fmt"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A usage counter can precede an empty completion and a later usage counter
// can complete it. These are precisely the split-usage semantics the managed
// accounting fixes preserve. A non-error finish must start the empty answer,
// not turn the partial usage into a reason to cancel the remaining evidence.
func TestFinishOnlySplitUsage(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, tail := range []int{0, 300} {
			for _, reason := range []string{"stop", "length", "content_filter", "tool_calls"} {
				t.Run(fmt.Sprintf("%s/tail=%d/%s", surface, tail, reason), func(t *testing.T) {
					g := recheck3Gateway(t, "")
					p := &preparedStream{script: func(model string) []models.StreamChunk {
						empty := ""
						var prompt, completion models.Usage
						if err := json.Unmarshal([]byte(`{"prompt_tokens":8}`), &prompt); err != nil {
							t.Fatal(err)
						}
						if err := json.Unmarshal([]byte(`{"completion_tokens":3}`), &completion); err != nil {
							t.Fatal(err)
						}
						out := []models.StreamChunk{
							{ID: "fx_split", Model: model, Choices: []models.StreamChoice{{Delta: models.Delta{Role: "assistant", Content: &empty}}}},
							{ID: "fx_split", Model: model, Usage: &prompt},
							{ID: "fx_split", Model: model, Choices: []models.StreamChoice{{FinishReason: &reason}}},
						}
						for i := 0; i < tail; i++ {
							out = append(out, models.StreamChunk{ID: "fx_split", Model: model})
						}
						return append(out, models.StreamChunk{ID: "fx_split", Model: model, Usage: &completion})
					}}
					prepareManagedStream(t, g, p)
					status, body, complete := sendManagedWithin(t, g, surface, "acme/token", 5*time.Second)
					spent, held := settledAccount(t, g)
					t.Logf("split usage status=%d complete=%t spent=%d held=%d", status, complete, spent, held)
					if !complete || status != 200 || p.calls.Load() != 1 || spent != 48 || held != 0 {
						t.Errorf("empty completion lost trailing measured usage: status=%d complete=%t calls=%d spent=%d want=48 held=%d body=%.300s", status, complete, p.calls.Load(), spent, held, body)
					}
					assertSplitUsageWire(t, surface, body)
				})
			}
		}
	}
}
