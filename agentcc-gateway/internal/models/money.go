package models

import "math"

// SpendMicros converts a cost in USD to the whole micro-dollars a spend ledger
// records (credits, budgets, metrics). It rounds to the nearest micro-dollar,
// except that a positive cost is never recorded as 0: anything smaller than
// half a micro-dollar counts as 1. Zero stays 0 and negative amounts (refunds)
// round as before.
func SpendMicros(usd float64) int64 {
	m := int64(math.Round(usd * 1_000_000))
	if m == 0 && usd > 0 {
		return 1
	}
	return m
}

// SpendUSD is usd as a spend ledger records it: SpendMicros, in dollars.
func SpendUSD(usd float64) float64 {
	return float64(SpendMicros(usd)) / 1_000_000
}
