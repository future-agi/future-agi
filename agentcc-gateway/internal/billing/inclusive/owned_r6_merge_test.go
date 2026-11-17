package inclusive

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func ownedR6Usage(t *testing.T, raw string) *models.Usage {
	t.Helper()
	var u models.Usage
	if err := json.Unmarshal([]byte(raw), &u); err != nil {
		t.Fatal(err)
	}
	return &u
}

// Cost and its wire lexeme move together across merged stream events. A later
// null, string, negative or non-numeric cost can neither replace an earlier
// valid total nor leave the pair disagreeing; for inclusive settlement it
// still contests the total, which is charged at the full hold pending
// reconciliation, never at the earlier partial total.
func TestOwnedR6MergedCostKeepsLexemeWithItsValue(t *testing.T) {
	const exact = "0.00000949999999999999999"
	for _, later := range []string{`null`, `"0.0000095"`, `-1`, `true`, `{"usd":1}`, `[1]`} {
		t.Run(later, func(t *testing.T) {
			previous := ownedR6Usage(t, `{"prompt_tokens":1,"completion_tokens":1,"cost":`+exact+`}`)
			merged := models.MergeUsage(previous, ownedR6Usage(t, `{"prompt_tokens":1,"completion_tokens":1,"cost":`+later+`}`))
			if !merged.HasReportedCost() || *merged.Cost != *previous.Cost || merged.CostDecimal != exact {
				t.Fatalf("cost/lexeme pair split: cost=%v lexeme=%q", merged.Cost, merged.CostDecimal)
			}
			if got := Price(merged, 84000); got.BilledMicros != 84000 || !got.Estimated {
				t.Fatalf("contested terminal total borrowed the earlier partial: %+v", got)
			}
			// An omitted cost later on cannot clear the contest.
			if got := Price(models.MergeUsage(merged, ownedR6Usage(t, `{}`)), 84000); got.BilledMicros != 84000 || !got.Estimated {
				t.Fatalf("omitted cost cleared the contested total: %+v", got)
			}
			// A later valid cumulative total supersedes both.
			resolved := models.MergeUsage(merged, ownedR6Usage(t, `{"cost":0.00002}`))
			if got := Price(resolved, 84000); got.BilledMicros != 21 || got.Estimated || resolved.CostDecimal != "0.00002" {
				t.Fatalf("later valid total not settled: %+v lexeme=%q", got, resolved.CostDecimal)
			}
		})
	}
	// An omitted later cost retains cumulative evidence without contesting it.
	kept := models.MergeUsage(ownedR6Usage(t, `{"cost":0.0000095}`), ownedR6Usage(t, `{}`))
	if got := Price(kept, 84000); got.BilledMicros != 11 || kept.CostDecimal != "0.0000095" {
		t.Fatalf("omitted cost lost the total: %+v", got)
	}
	// Without an earlier valid total, an invalid lexeme stays invalid.
	invalid := models.MergeUsage(ownedR6Usage(t, `{"cost":null}`), ownedR6Usage(t, `{"cost":-1}`))
	if invalid.HasReportedCost() || invalid.CostDecimal != "-1" {
		t.Fatalf("invalid cost became a total: %+v", invalid)
	}
}
