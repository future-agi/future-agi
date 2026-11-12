package credits

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// These rows exercise the journal and recovery, not just the final total.
// Rejection provenance and its empty-field variants also run through real HTTP
// in TestOwnedR7CleanFailureWithEmptyUsage and the accepted-EOF sequence.
func TestOwnedR7LiabilityRows(t *testing.T) {
	for _, tc := range []struct {
		name, usage string
		want        int64
	}{
		{"reported-cost", `{"cost":0.0000095}`, 11},
		{"reported-zero-cost", `{"cost":0}`, 0},
		{"reported-tokens", `{"prompt_tokens":5,"completion_tokens":0}`, 5},
		{"missing", `null`, 41580},
		{"empty", `{}`, 41580},
		{"null", `{"cost":null,"prompt_tokens":null,"completion_tokens":null}`, 41580},
		{"partial", `{"prompt_tokens":5}`, 41580},
		{"empty-tools", `{"server_tool_use":{}}`, 41580},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var usage *models.Usage
			if err := json.Unmarshal([]byte(tc.usage), &usage); err != nil {
				t.Fatal(err)
			}
			_, rec := ownedR6Journal(New(true, nil), models.Attempt{Outcome: models.OutcomeSuccess, Usage: usage}, 41580)
			if rec.BilledMicros != tc.want {
				t.Fatalf("liability=%d want=%d", rec.BilledMicros, tc.want)
			}
		})
	}
	t.Run("interim", func(t *testing.T) {
		var first, next models.Usage
		_ = json.Unmarshal([]byte(`{"cost":0.0000095}`), &first)
		_ = json.Unmarshal([]byte(`{"cost":null}`), &next)
		_, rec := ownedR6Journal(New(true, nil), models.Attempt{Outcome: models.OutcomeError, Usage: models.MergeUsage(&first, &next)}, 41580)
		if rec.BilledMicros != 41580 {
			t.Fatalf("contested interim cost=%d want full hold", rec.BilledMicros)
		}
	})
}
