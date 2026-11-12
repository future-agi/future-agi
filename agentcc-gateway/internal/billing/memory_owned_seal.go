package billing

import "context"

func (l *MemoryLedger) SealOwnedJournal(_ context.Context, org, req string, rec Settlement) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.sealOwnedJournal(l.org(org), org, req, rec)
}

func (l *MemoryLedger) sealOwnedJournal(o *memOrg, org, req string, rec Settlement) error {
	if len(rec.ManagedJournal) == 0 {
		if owned := o.owned[req]; owned != nil {
			l.sealSavedOwned(owned)
		}
		return nil
	}
	if rec.OrgID != org || rec.RequestID != req {
		return ErrOwnership
	}
	owned := o.owned[req]
	if owned != nil && owned.Terminal != "" {
		return ErrOwnership
	}
	held, ok := o.holds[req]
	if !ok {
		return ErrOwnership
	}
	if owned == nil {
		owned = &OwnedRequest{Attempts: map[string]OwnedAttempt{}}
	}
	if err := owned.ImportJournal(rec, held); err != nil {
		return err
	}
	o.owned[req] = owned
	if owned.Dispatched {
		delete(o.holdExp, req)
	}
	l.sealSavedOwned(owned)
	return nil
}

func (l *MemoryLedger) sealSavedOwned(owned *OwnedRequest) {
	saved := owned.Record
	saved.ManagedJournal = owned.Attempts
	for _, model := range OwnedQuarantines(saved) {
		l.catalogueQuarantine[model] = true
	}
}
