package batch

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestBatchCostKnowledge(t *testing.T) {
	for _, tc := range []struct {
		name, cost, known string
		wantKnown         bool
	}{
		{"priced", ".125", "true", true}, {"zero", "0", "true", true},
		{"missing", "", "true", false}, {"invalid", "invalid", "true", false},
		{"nan", "NaN", "true", false}, {"infinite", "+Inf", "true", false},
		{"negative", "-.1", "true", false}, {"missing evidence", ".125", "", false},
		{"partial zero", "0", "false", false}, {"partial positive", ".125", "false", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("x-agentcc-cost", tc.cost)
				w.Header().Set("x-agentcc-charge", tc.cost)
				w.Header().Set("x-agentcc-charge-known", tc.known)
				w.Header().Set("x-agentcc-charge-billable", "true")
				w.Header().Set("x-agentcc-request-id", "run-item")
				_ = json.NewEncoder(w).Encode(&models.ChatCompletionResponse{Model: "custom"})
			})
			p := &Processor{handler: RunAsChatRequest(func() http.Handler { return handler })}
			b := &Batch{Status: "processing", MaxConc: 1, Results: make([]*Result, 1)}
			p.processBatch(context.Background(), b, []*models.ChatCompletionRequest{{Model: "custom"}})
			data, err := json.Marshal(struct {
				Results []*Result `json:"results"`
				Summary Summary   `json:"summary"`
			}{b.GetResults(), b.Summary()})
			if err != nil {
				t.Fatal(err)
			}
			var got struct {
				Results []struct {
					Cost      *float64 `json:"cost"`
					Known     bool     `json:"cost_known"`
					RequestID string   `json:"request_id"`
				} `json:"results"`
				Summary struct {
					Cost     *float64 `json:"total_cost"`
					Known    bool     `json:"cost_known"`
					Unpriced int      `json:"unpriced_requests"`
				} `json:"summary"`
			}
			if err := json.Unmarshal(data, &got); err != nil {
				t.Fatal(err)
			}
			if got.Results[0].Known != tc.wantKnown || got.Summary.Known != tc.wantKnown || (got.Results[0].Cost != nil) != tc.wantKnown || (got.Summary.Cost != nil) != tc.wantKnown {
				t.Errorf("amount/knowledge mismatch: %s", data)
			}
			wantUnknown := 1
			if tc.wantKnown {
				wantUnknown = 0
			}
			if got.Summary.Unpriced != wantUnknown || got.Results[0].RequestID != "run-item" {
				t.Errorf("count/identity mismatch: %s", data)
			}
		})
	}
}

func TestBatchUnpricedCountIncludesUnfinishedAndFailedItems(t *testing.T) {
	b := &Batch{Results: []*Result{nil, {Status: "pending"}, {Status: "cancelled", CostKnown: true}, {Status: "error"}}}
	data, err := json.Marshal(b.Summary())
	if err != nil {
		t.Fatal(err)
	}
	var got struct {
		Cost     *float64 `json:"total_cost"`
		Unpriced int      `json:"unpriced_requests"`
	}
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatal(err)
	}
	if got.Cost != nil || got.Unpriced != 3 {
		t.Fatalf("incomplete and failed charges were hidden: %s", data)
	}
}
