package tenant

type nativeNotice struct {
	observer *configObserver
	org      string
	data     *OrgConfig
}

// Prepare all observers before publication, with a distinct graph for EACH
// observer/event. No provenance or canonical interior escapes to callbacks.
// Callbacks retain the existing bounded no-reentry/no-I/O contract even though
// payload isolation also protects against a callback retaining/mutating data.
func (s *Store) prepareNativeObserversLocked(m *nativeMutation) ([]nativeNotice, error) {
	notices := make([]nativeNotice, 0)
	for _, org := range m.notify {
		entry := s.configs[org]
		if edit, ok := m.edits[org]; ok {
			entry = edit.entry
		}
		for observer := range s.observers {
			data, err := cloneConfigData(entry.data())
			if err != nil {
				return nil, err
			}
			notices = append(notices, nativeNotice{observer: observer, org: org, data: data})
		}
	}
	return notices, nil
}
