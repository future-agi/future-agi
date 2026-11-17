package batch

import (
	"encoding/json"
	"math"
)

func knownCharge(value float64, known bool) *float64 {
	if !known || math.IsNaN(value) || math.IsInf(value, 0) || value < 0 {
		return nil
	}
	return &value
}

// Internal subtotals remain available to existing accounting consumers. The
// batch wire contract only exposes a number for a complete final charge.
func (r Result) MarshalJSON() ([]byte, error) {
	type resultFields Result
	cost := knownCharge(r.Cost, r.CostKnown)
	return json.Marshal(struct {
		resultFields
		Cost      *float64 `json:"cost"`
		CostKnown bool     `json:"cost_known"`
	}{resultFields(r), cost, cost != nil})
}

func (s Summary) MarshalJSON() ([]byte, error) {
	type summaryFields Summary
	cost := knownCharge(s.TotalCost, s.CostKnown && s.UnpricedRequests == 0)
	return json.Marshal(struct {
		summaryFields
		TotalCost *float64 `json:"total_cost"`
		CostKnown bool     `json:"cost_known"`
	}{summaryFields(s), cost, cost != nil})
}
