package budget

import (
	"encoding/json"
	"fmt"
	"math"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestCapActualDecodedUsageEvidence(t *testing.T) {
	for _, tc := range []struct {
		usage string
		known bool
	}{
		{`{}`, false},
		{`{"total_tokens":1500}`, false},
		{`{"prompt_tokens":1000}`, false},
		{`{"completion_tokens":500}`, false},
		{`{"prompt_tokens":null,"completion_tokens":0}`, false},
		{`{"prompt_tokens":0,"completion_tokens":null}`, false},
		{`{"prompt_tokens":-1,"completion_tokens":1}`, false},
		{`{"prompt_tokens":1,"completion_tokens":-1}`, false},
		{`{"cost":null}`, false},
		{`{"cost":-1}`, false},
		{`{"prompt_tokens":0,"completion_tokens":0}`, true},
		{`{"prompt_tokens":1000,"completion_tokens":500}`, true},
		{`{"cost":0}`, true},
		{`{"cost":0.007}`, true},
	} {
		for _, managed := range []bool{false, true} {
			for _, recorded := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/managed=%v/recorded=%v", tc.usage, managed, recorded), func(t *testing.T) {
					var usage models.Usage
					if err := json.Unmarshal([]byte(tc.usage), &usage); err != nil {
						t.Fatal(err)
					}
					rc := models.AcquireRequestContext()
					defer rc.Release()
					rc.Response = &models.ChatCompletionResponse{Usage: &usage}
					rc.Billing.UpstreamMicros, rc.Billing.BilledMicros = 1000, 1050
					if recorded {
						rc.AddAttempt(models.Attempt{Usage: &usage, UpstreamCostMicros: 1000, BilledCostMicros: 1050, CostSource: models.CostSourceCatalog})
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
						t.Fatalf("actual=%d known=%v want=%d known=%v", actual, known, want, tc.known)
					}
				})
			}
		}
	}
}

func TestCapActualRejectsInvalidSettlementAmounts(t *testing.T) {
	for _, recorded := range []bool{false, true} {
		for _, managed := range []bool{false, true} {
			for _, source := range []models.CostSource{models.CostSourceCatalog, models.CostSourceUnpriced} {
				t.Run(fmt.Sprintf("recorded=%v/managed=%v/source=%s", recorded, managed, source), func(t *testing.T) {
					rc := models.AcquireRequestContext()
					defer rc.Release()
					usage := models.MeasuredUsage(10, 10)
					rc.Response = &models.ChatCompletionResponse{Usage: usage}
					rc.Billing.UpstreamMicros, rc.Billing.BilledMicros, rc.Billing.CostSource = -1, -1, source
					if recorded {
						rc.AddAttempt(models.Attempt{Usage: usage, UpstreamCostMicros: -1, BilledCostMicros: -1, CostSource: source})
					}
					a := &Admission{managed: managed}
					a.attempted.Store(true)
					if actual, known := a.ActualSpend(rc); known || actual != 0 {
						t.Fatalf("invalid amount settled: %d %v", actual, known)
					}
				})
			}
		}
	}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	for range 2 {
		rc.AddAttempt(models.Attempt{Usage: models.MeasuredUsage(0, 0), UpstreamCostMicros: math.MaxInt64})
	}
	a := &Admission{}
	a.attempted.Store(true)
	if actual, known := a.ActualSpend(rc); known || actual != 0 {
		t.Fatalf("overflow settled: %d %v", actual, known)
	}
}
