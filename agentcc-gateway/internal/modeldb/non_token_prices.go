package modeldb

import "math"

// nonTokenPrice gives an operator's configured value precedence over the
// bundled default, including an explicit invalid or zero value (unpriced).
// It is used by accounting lookups, never to expand supported endpoint kinds.
func nonTokenPrice(configured float64, overridden bool, fallback float64) float64 {
	price := configured
	if !overridden && configured == 0 {
		price = fallback
	}
	if price <= 0 || math.IsNaN(price) || math.IsInf(price, 0) {
		return 0
	}
	return price
}
