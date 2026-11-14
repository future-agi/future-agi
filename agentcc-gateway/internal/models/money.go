package models

import "math"

// SpendMicros converts a cost in USD to the whole micro-dollars a spend ledger
// records (credits, budgets, metrics). It rounds to the nearest micro-dollar,
// except that a positive cost is never recorded as 0: anything smaller than
// half a micro-dollar counts as 1. Zero stays 0 and negative amounts (refunds)
// round as before.
//
// It saturates instead of overflowing. Converting a float64 outside the int64
// range is implementation-defined in Go (amd64 gives MinInt64), so a huge cost
// (org custom pricing has no upper bound) would turn into a huge refund and the
// budget would stop blocking. A cost at or above MaxInt64 micro-dollars, +Inf
// included, records MaxInt64; one at or below MinInt64 records MinInt64. NaN is
// not a cost and records 0.
func SpendMicros(usd float64) int64 {
	if math.IsNaN(usd) {
		return 0
	}
	micros := math.Round(usd * 1_000_000)
	// float64(math.MaxInt64) is 2^63, one above MaxInt64; -2^63 is MinInt64.
	if micros >= math.MaxInt64 {
		return math.MaxInt64
	}
	if micros <= math.MinInt64 {
		return math.MinInt64
	}
	m := int64(micros)
	if m == 0 && usd > 0 {
		return 1
	}
	return m
}

// SpendUSD is usd as a spend ledger records it: SpendMicros, in dollars.
func SpendUSD(usd float64) float64 {
	return float64(SpendMicros(usd)) / 1_000_000
}
