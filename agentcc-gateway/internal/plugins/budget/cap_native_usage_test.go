package budget

import (
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Presence survives the shallow attempt copy. A billed native hold floor is an
// estimate, not proof that the request spent exactly that amount or spent zero.
func TestCapActualNativePresence(t *testing.T) {
	zero, prompt, output := 0, 10, 5
	estimated := models.MeasuredUsage(10, 5)
	estimated.Estimated = true
	for _, tc := range []struct {
		name  string
		usage *models.Usage
		known bool
	}{
		{"absent", nil, false},
		{"literal-empty", &models.Usage{}, false},
		{"literal-counts", &models.Usage{PromptTokens: 10, CompletionTokens: 5}, false},
		{"native-empty", models.NewUsage(nil, nil), false},
		{"native-input-only", models.NewUsage(&prompt, nil), false},
		{"native-output-only", models.NewUsage(nil, &output), false},
		{"native-zero-input-only", models.NewUsage(&zero, nil), false},
		{"native-zero", models.NewUsage(&zero, &zero), true},
		{"native-complete", models.NewUsage(&prompt, &output), true},
		{"native-estimated", estimated, false},
	} {
		for _, managed := range []bool{false, true} {
			for _, recorded := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/managed=%v/recorded=%v", tc.name, managed, recorded), func(t *testing.T) {
					rc := models.AcquireRequestContext()
					defer rc.Release()
					rc.Response = &models.ChatCompletionResponse{Usage: tc.usage}
					rc.Billing.UpstreamMicros, rc.Billing.BilledMicros = 1000, 1050
					if recorded {
						rc.AddAttempt(models.Attempt{Usage: tc.usage, UpstreamCostMicros: 1000, BilledCostMicros: 1050, CostSource: models.CostSourceCatalog})
					}
					a := &Admission{managed: managed}
					a.attempted.Store(true)
					actual, known := a.ActualSpend(rc)
					want := int64(0)
					if tc.known {
						want = 1000
						if managed {
							want = 1050
						}
					}
					if known != tc.known || actual != want {
						t.Fatalf("actual=%d known=%v want=%d/%v", actual, known, want, tc.known)
					}
				})
			}
		}
	}
}
