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
// budget would stop blocking. A cost at or above MaxSpendMicros, +Inf included,
// records MaxSpendMicros; one at or below -MaxSpendMicros records
// -MaxSpendMicros. NaN is not a cost and records 0.
func SpendMicros(usd float64) int64 {
	if math.IsNaN(usd) {
		return 0
	}
	micros := math.Round(usd * 1_000_000)
	if micros >= MaxSpendMicros {
		return MaxSpendMicros
	}
	if micros <= -MaxSpendMicros {
		return -MaxSpendMicros
	}
	m := int64(micros)
	if m == 0 && usd > 0 {
		return 1
	}
	return m
}

// MaxSpendMicros is the largest single charge a spend ledger records: 2^53
// micro-dollars, about $9.007 billion, far above any one request. It is exact
// as a float64 (and as a Lua number in the Redis scripts), and the ledgers that
// add charges up (Redis counters, credit balances, metrics) can take over a
// thousand (1023) such charges before an int64 would overflow, where saturating at
// MaxInt64 would overflow on the next charge.
const MaxSpendMicros = 1 << 53

// SpendUSD is usd as a spend ledger records it: SpendMicros, in dollars.
func SpendUSD(usd float64) float64 {
	return float64(SpendMicros(usd)) / 1_000_000
}
