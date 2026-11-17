package tenant

// legacyHashForOwnedDraft serializes ONLY a sacrificial structural copy. Call
// outside Gate, Store, candidate and cache locks. The scratch is never installed,
// shared with observers, reused, pooled, or copied back into a publication.
// Returned serializer errors retain hashConfig's historical pointer/time fallback;
// serializer panic becomes a fixed preparation failure with no exposed cause.
func legacyHashForOwnedDraft(draft *OrgConfig) (hash [32]byte, err error) {
	scratch, err := cloneConfigData(draft)
	if err != nil {
		return hash, err
	}
	completed := false
	defer func() {
		if !completed {
			_ = recover()
			hash = [32]byte{}
			err = ErrConfigPreparation
		}
	}()
	hash = hashConfig(scratch)
	completed = true
	return hash, nil
}
