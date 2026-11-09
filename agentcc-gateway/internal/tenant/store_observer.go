package tenant

type configObserver struct{ invalidate func(string, *OrgConfig) }

// ObserveChanges installs a lifecycle invalidator and returns its unsubscribe.
// Unlike SetOnChange, every mutation (including Set/Delete/LoadBulk) invokes
// these synchronously under the store lock, in write order. A revoke followed
// immediately by reconnect must not coalesce away the revoke. The callback may
// only invalidate owned state: no store calls, blocking I/O, or config mutation.
func (s *Store) ObserveChanges(invalidate func(string, *OrgConfig)) func() {
	o := &configObserver{invalidate: invalidate}
	s.mu.Lock()
	if s.observers == nil {
		s.observers = make(map[*configObserver]struct{})
	}
	s.observers[o] = struct{}{}
	s.mu.Unlock()
	return func() { s.mu.Lock(); delete(s.observers, o); s.mu.Unlock() }
}

func (s *Store) notifyLocked(orgID string) {
	for o := range s.observers {
		o.invalidate(orgID, s.configs[orgID])
	}
}
