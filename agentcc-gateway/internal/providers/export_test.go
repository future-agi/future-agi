package providers

// NewInertOrgProviderCacheForTest seeds synthetic providers before escape. Nil
// base configs ensure that a subsequent cache miss cannot construct an adapter.
// This helper is compiled only as part of this package's own test variant.
func NewInertOrgProviderCacheForTest(entries map[string]map[string]Provider) *OrgProviderCache {
	cache := NewOrgProviderCache(nil, false)
	for org, providers := range entries {
		for id, provider := range providers {
			cache.providers[org+":"+id] = provider
		}
	}
	return cache
}
