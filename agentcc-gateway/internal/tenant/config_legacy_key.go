package tenant

// LegacyConfigKey identifies ordinary cache DATA, not currentness, a provider,
// or native authority. There is deliberately no key-to-view conversion.
type LegacyConfigKey struct {
	identity *legacyIdentity
	variant  string
}
type legacyIdentity struct{ image *nativeImage }
type legacyProvenance struct{ key LegacyConfigKey }

func (k LegacyConfigKey) expected() (*OrgConfig, bool) {
	if k.identity == nil || k.identity.image == nil {
		return nil, false
	}
	return k.identity.image.variant(k.variant)
}

// LegacyCacheKey checks the entire expected variant, including private fields
// and concrete dynamic types. The caller keeps its data stable during the call.
func (c *OrgConfig) LegacyCacheKey() (LegacyConfigKey, bool) {
	if c == nil || c.legacy == nil {
		return LegacyConfigKey{}, false
	}
	k := c.legacy.key
	expected, ok := k.expected()
	if !ok {
		return LegacyConfigKey{}, false
	}
	data, err := cloneConfigData(c)
	if err != nil || !equalConfigData(data, expected) {
		return LegacyConfigKey{}, false
	}
	return k, true
}

// CopyForLegacyCache supplies a private cache-build graph, never an indefinitely
// borrowed getter graph. A failed shared-key check must not fall back upstream.
// Input stability covers validation, copying AND revalidation.
func (c *OrgConfig) CopyForLegacyCache(expected LegacyConfigKey) (*OrgConfig, bool, error) {
	if c == nil || c.legacy == nil || c.legacy.key != expected {
		return nil, false, nil
	}
	want, ok := expected.expected()
	if !ok {
		return nil, false, nil
	}
	before, err := cloneConfigData(c)
	if err != nil {
		return nil, false, err
	}
	if !equalConfigData(before, want) {
		return nil, false, nil
	}
	out, err := cloneConfigData(c)
	if err != nil {
		return nil, false, err
	}
	after, err := cloneConfigData(c)
	if err != nil {
		return nil, false, err
	}
	if c.legacy == nil || c.legacy.key != expected || !equalConfigData(out, want) || !equalConfigData(after, want) {
		return nil, false, nil
	}
	out.legacy = &legacyProvenance{key: expected}
	return out, true, nil
}

func legacyData(image *nativeImage, identity *legacyIdentity, variant string) *OrgConfig {
	data, ok := image.variant(variant)
	if !ok || data == nil {
		return nil
	}
	copy, err := cloneConfigData(data)
	if err != nil {
		return nil
	} // Checked immutable image; refusal is defensive.
	if identity != nil {
		copy.legacy = &legacyProvenance{key: LegacyConfigKey{identity: identity, variant: variant}}
	}
	return copy
}

// Candidate provenance is explicit. A shallow inherited stable tag never
// authenticates an overlay. Modified input/candidate stays ordinary untagged data.
func (c *OrgConfig) tagLegacyCandidate(candidate *OrgConfig) {
	candidate.legacy = nil
	key, ok := c.LegacyCacheKey()
	if !ok || key.variant != PolicyVariantStable {
		return
	}
	key.variant = PolicyVariantCandidate
	expected, ok := key.expected()
	if !ok {
		return
	}
	data, err := cloneConfigData(candidate)
	if err == nil && equalConfigData(data, expected) {
		candidate.legacy = &legacyProvenance{key: key}
	}
}
