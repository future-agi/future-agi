package billing

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestCompleteUsagePresence(t *testing.T) {
	estimate := models.MeasuredUsage(8, 3)
	estimate.Estimated = true
	for _, tc := range []struct {
		raw                string
		prompt, completion int
		estimated          bool
	}{
		{`{}`, 8, 3, true},
		{`{"prompt_tokens":0}`, 0, 3, true},
		{`{"completion_tokens":0}`, 8, 0, true},
		{`{"prompt_tokens":1000}`, 1000, 3, true},
		{`{"completion_tokens":500}`, 8, 500, true},
		{`{"prompt_tokens":0,"completion_tokens":0}`, 0, 0, false},
		{`{"prompt_tokens":10,"completion_tokens":5}`, 10, 5, false},
		{`{"cost":0}`, 0, 0, false},
		{`{"cost":0.001}`, 0, 0, false},
	} {
		t.Run(tc.raw, func(t *testing.T) {
			var reported models.Usage
			if err := json.Unmarshal([]byte(tc.raw), &reported); err != nil {
				t.Fatal(err)
			}
			before := reported
			got := CompleteUsage(&reported, estimate)
			if got.PromptTokens != tc.prompt || got.CompletionTokens != tc.completion || got.Estimated != tc.estimated || got.NeedsTokenEstimate() {
				t.Fatalf("incomplete estimate: %+v", got)
			}
			if reported.PromptTokens != before.PromptTokens || reported.CompletionTokens != before.CompletionTokens || reported.Estimated != before.Estimated {
				t.Fatal("mutated provider report")
			}
			if again := CompleteUsage(got, EstimateUsage(nil, 4000)); again != got {
				t.Fatal("re-estimated a completed reading")
			}
		})
	}
	got := CompleteUsage(&models.Usage{}, estimate)
	if got.NeedsTokenEstimate() || !got.Estimated || got.PromptTokens != 8 || got.CompletionTokens != 3 {
		t.Fatalf("literal skipped estimate: %+v", got)
	}
}
