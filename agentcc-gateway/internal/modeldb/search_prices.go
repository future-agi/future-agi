package modeldb

// SearchPriceKey is where the price table files a search on searchProvider:
// "<search provider>/search". A search has no model, so its price is the
// provider's.
func SearchPriceKey(searchProvider string) string {
	return searchProvider + "/search"
}

// SearchPrice returns the configured per-query price before the bundled
// default. Lookups are exact: another provider's or an unqualified search
// entry cannot price this provider. Missing, zero, invalid and tiered-only
// prices remain unpriced. This lookup does not admit managed-credit calls.
func (db *ModelDB) SearchPrice(searchProvider string) (float64, bool) {
	if db == nil || searchProvider == "" {
		return 0, false
	}
	key := SearchPriceKey(searchProvider)
	fallback := bundledExt()[key].PerQuery
	price := fallback
	if info, ok := db.models[key]; ok {
		price = nonTokenPrice(info.Pricing.InputPerQuery, info.Pricing.inputPerQuerySet, fallback)
	} else {
		price = nonTokenPrice(0, false, fallback)
	}
	return price, price > 0
}
