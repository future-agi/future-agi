package server

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/batch"
)

func TestBatchGetResponsePreservesCostKnowledge(t *testing.T) {
	for _, status := range []string{"completed", "cancelled"} {
		t.Run(status, func(t *testing.T) {
			var results []*batch.Result
			if err := json.Unmarshal([]byte(`[
				{"index":0,"status":"success","cost":0.125,"cost_known":true},
				{"index":1,"status":"success","cost_known":false},
				{"index":2,"status":"success","cost_known":true}
			]`), &results); err != nil {
				t.Fatal(err)
			}
			b := &batch.Batch{ID: "batch-pricing", Status: status, Total: 3, Results: results}
			data, err := json.Marshal(batchGetResponse(b))
			if err != nil {
				t.Fatal(err)
			}
			var got struct {
				Summary struct {
					Unpriced *int     `json:"unpriced_requests"`
					Total    *float64 `json:"total_cost"`
				} `json:"summary"`
				Results []struct {
					CostKnown *bool `json:"cost_known"`
				} `json:"results"`
			}
			if err := json.Unmarshal(data, &got); err != nil {
				t.Fatal(err)
			}
			if got.Summary.Unpriced == nil || *got.Summary.Unpriced != 1 || got.Summary.Total != nil {
				t.Fatalf("terminal summary lost its unpriced count: %s", data)
			}
			if len(got.Results) != 3 {
				t.Fatalf("got %d results, want 3", len(got.Results))
			}
			for i, known := range []bool{true, false, true} {
				if got.Results[i].CostKnown == nil || *got.Results[i].CostKnown != known {
					t.Errorf("result %d lost cost_known=%v", i, known)
				}
			}
		})
	}
}
