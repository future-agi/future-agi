package tenant

func (s *Store) mergeNative(life *tenantLifecycle, parsed map[string]*OrgConfig, all map[string]struct{}, fetchedAt uint64) error {
	// Copy the response membership before serializers run; retained input maps
	// must not change removal/preservation decisions after this preparation.
	ids := make(map[string]struct{}, len(all))
	for org := range all {
		ids[org] = struct{}{}
	}
	drafts, err := prepareNativeBatch(parsed, true)
	if err != nil {
		return err
	}
	return s.publishNative(life, func() (*nativeMutation, error) {
		if s.loadedGen > fetchedAt {
			return nil, nil
		}
		m := newNativeMutation("fetch")
		m.fetchedAt = fetchedAt
		noticed := make(map[string]bool)
		notice := func(org string) {
			if !noticed[org] {
				m.notify = append(m.notify, org)
				noticed[org] = true
			}
		}
		for org, entry := range s.configs {
			if _, present := ids[org]; entry.present && !present && s.pushedGen[org] <= fetchedAt {
				m.edits[org] = nativeReplacement{entry: &storedConfig{}, fresh: true}
				m.changed = append(m.changed, org)
				notice(org)
			}
		}
		for org, draft := range drafts {
			if s.pushedGen[org] > fetchedAt {
				continue
			}
			old := s.configs[org]
			oldHash, hashed := s.configHash[org]
			if removal, ok := m.edits[org]; ok && !removal.entry.present {
				old = nil
				hashed = false
			}
			entry := replacement(draft.image)
			same := old != nil && old.present && equalConfigData(old.image.stable, draft.image.stable)
			if same {
				// EXACT image/stamp survives; only non-authority replacement identity advances.
				entry.image = old.image
				entry.stamp = old.stamp
				entry.legacy = &legacyIdentity{image: old.image}
			}
			m.edits[org] = nativeReplacement{entry: entry, fresh: !same}
			m.hashes[org] = draft.hash
			if !hashed || oldHash != draft.hash {
				m.changed = append(m.changed, org)
				notice(org)
			}
			if !same {
				notice(org)
			} // Native-only/private/type differences still invalidate dependents.
		}
		return m, nil
	})
}
