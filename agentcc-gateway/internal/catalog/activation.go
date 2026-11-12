package catalog

// CAT-2 activates image and music listings only. Router billing remains OFF
// independently of all five CAT-2 activation requirements.
func cat2FamilyMember(id string) bool {
	return imageFamily[id] ||
		id == "google/lyria-3-pro-preview" || id == "google/lyria-3-clip-preview"
}

// MixedCatalogueDisabled is true for every operator-configured catalogue.
// The CAT-2 opt-in enables individual family calls, not the in-process mixed
// catalogue/token-chain test profile. It cannot be overridden by an org.
func (c *Catalog) MixedCatalogueDisabled() bool {
	return c != nil && c.standaloneCAT2
}
