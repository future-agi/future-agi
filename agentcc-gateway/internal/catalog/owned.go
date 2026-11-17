package catalog

// OwnsManagedAttempts conservatively selects durable ownership for every
// managed hop in the opt-in mixed catalogue profile. Checking only the primary
// price card is too late for token -> inclusive chains (including auto routing
// and BYOK bridges). The immutable catalogue caches this profile-wide decision;
// shipped profiles with inclusive billing off retain their legacy protocol.
func (c *Catalog) OwnsManagedAttempts() bool {
	if c == nil {
		return false
	}
	c.ownedOnce.Do(func() {
		for _, s := range c.byProvider {
			for _, m := range s.Models {
				if m.InclusiveEnabled {
					c.owned = true
					return
				}
			}
		}
	})
	return c.owned
}
