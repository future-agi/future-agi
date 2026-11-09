package billing

import (
	"context"
	"encoding/json"
	"time"
)

// ReserveInclusive mirrors the Redis transaction. Memory remains test-only;
// its support is not a substitute for Redis persistence in shipped profiles.
func (l *MemoryLedger) ReserveInclusive(ctx context.Context, org, req, attempt, model string, micros int64, ttl time.Duration, mode ReserveMode) (ReserveResult, error) {
	return l.ReserveOwned(ctx, org, req, attempt, model, micros, ttl, mode, false, Settlement{})
}
func (l *MemoryLedger) ReserveOwned(_ context.Context, org, req, attempt, model string, micros int64, ttl time.Duration, _ ReserveMode, tokenPriced bool, rec Settlement) (ReserveResult, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	o := l.org(org)
	now := l.now()
	if req == "" || attempt == "" || model == "" || micros < 0 || micros > 1<<50 || ttl <= 0 {
		return ReserveResult{}, ErrOwnership
	}
	if err := l.sealOwnedJournal(o, org, req, rec); err != nil {
		return ReserveResult{}, err
	}
	if l.catalogueQuarantine[model] {
		return ReserveResult{}, ErrOwnership
	}
	owned := o.owned[req]
	if owned != nil {
		if owned.Terminal != "" {
			return ReserveResult{}, ErrOwnership
		}
		if prior, ok := owned.Attempts[attempt]; ok {
			_, held := o.holds[req]
			if prior.Model != model || prior.Micros != micros || prior.TokenPriced != tokenPriced || (!prior.Dispatched && (prior.Expires <= now.UnixMilli() || !held)) {
				return ReserveResult{}, ErrOwnership
			}
			return ReserveResult{Status: ReserveOK, Account: o.acct}, nil
		}
	}
	if until, ok := o.settled[req]; ok && until.After(now) {
		return ReserveResult{}, ErrOwnership
	}
	if !o.acct.Exists {
		return ReserveResult{Status: ReserveNoAccount}, nil
	}
	o.sweep(now)
	status := decideReserve(o.acct, micros, l.overdraftMicros)
	if status != ReserveOK {
		return ReserveResult{Status: status, Account: o.acct}, nil
	}
	if owned == nil {
		owned = &OwnedRequest{Attempts: map[string]OwnedAttempt{}}
		o.owned[req] = owned
	}
	if err := owned.ImportJournal(rec, o.holds[req]); err != nil {
		return ReserveResult{}, err
	}
	owned.Attempts[attempt] = OwnedAttempt{Model: model, Micros: micros, Expires: now.Add(ttl).UnixMilli(), TokenPriced: tokenPriced}
	o.holds[req] += micros
	o.acct.HeldMicros += micros
	if !owned.Dispatched {
		o.holdExp[req] = now.Add(ttl)
	} else {
		delete(o.holdExp, req)
	}
	return ReserveResult{Status: ReserveOK, Account: o.acct}, nil
}
func (l *MemoryLedger) BeginInclusive(ctx context.Context, org, req, attempt string) error {
	return l.BeginInclusiveWithRecord(ctx, org, req, attempt, Settlement{OrgID: org, RequestID: req})
}
func (l *MemoryLedger) BeginInclusiveWithRecord(_ context.Context, org, req, attempt string, rec Settlement) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	o := l.org(org)
	owned := o.owned[req]
	if owned == nil || owned.Terminal != "" || rec.OrgID != org || rec.RequestID != req {
		return ErrOwnership
	}
	if err := l.sealOwnedJournal(o, org, req, rec); err != nil {
		return err
	}
	a, ok := owned.Attempts[attempt]
	if !ok {
		return ErrOwnership
	}
	if a.Dispatched {
		return nil
	}
	_, held := o.holds[req]
	if a.Expires <= l.now().UnixMilli() || !held || l.catalogueQuarantine[a.Model] {
		return ErrOwnership
	}
	if err := owned.ImportJournal(rec, o.holds[req]); err != nil {
		return err
	}
	if binding, ok := rec.InclusiveBindings[attempt]; ok {
		copyBinding := binding
		a.Binding = &copyBinding
	}
	a.Dispatched = true
	owned.Attempts[attempt] = a
	owned.Dispatched = true
	data, _ := json.Marshal(rec)
	_ = json.Unmarshal(data, &owned.Record)
	delete(o.holdExp, req)
	return nil
}
func (l *MemoryLedger) InclusiveQuarantined(_ context.Context, org, model string) (bool, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.catalogueQuarantine[model], nil
}

// OwnedSettlement returns a detached copy of the retained billing evidence.
func (l *MemoryLedger) OwnedSettlement(org, req string) *Settlement {
	l.mu.Lock()
	defer l.mu.Unlock()
	o := l.org(org).owned[req]
	if o == nil || o.Terminal == "" {
		return nil
	}
	data, _ := json.Marshal(o.Record)
	var rec Settlement
	_ = json.Unmarshal(data, &rec)
	return &rec
}
func (l *MemoryLedger) finishOwnedMemory(o *memOrg, org, req string, rec Settlement) (SettleResult, error) {
	owned := o.owned[req]
	fingerprint := OwnedFingerprint(rec)
	if owned.Terminal != "" {
		if owned.Terminal != fingerprint {
			return SettleResult{}, ErrOwnership
		}
		return SettleResult{Account: o.acct}, nil
	}
	if rec.OrgID != org || rec.RequestID != req || rec.BilledMicros < 0 || rec.BilledMicros > 1<<50 || ((rec.Kind == KindVoid) == owned.Dispatched) {
		return SettleResult{}, ErrOwnership
	}
	if owned.Dispatched {
		if rec.Dimensional == nil {
			return SettleResult{}, ErrOwnership
		}
		seen := map[string]bool{}
		var sum int64
		for _, e := range rec.Dimensional.InclusiveAttempts {
			a, ok := owned.Attempts[e.AttemptID]
			if !ok || seen[e.AttemptID] || !a.Dispatched || a.TokenPriced || e.Model != a.Model || e.HoldMicros != a.Micros || e.BilledMicros < 0 || (e.Estimated && e.BilledMicros != a.Micros) {
				return SettleResult{}, ErrOwnership
			}
			if a.Binding != nil && rec.InclusiveBindings[e.AttemptID] != *a.Binding {
				return SettleResult{}, ErrOwnership
			}
			seen[e.AttemptID] = true
			sum += e.BilledMicros
		}
		for _, e := range rec.Attempts {
			a, ok := owned.Attempts[e.AttemptID]
			if !ok || !a.TokenPriced || !a.Dispatched {
				continue
			}
			if seen[e.AttemptID] || e.Model != a.Model || e.CredentialType != "managed" || e.BilledMicros < 0 || (a.Summary != nil && e.BilledMicros < a.Summary.BilledMicros) {
				return SettleResult{}, ErrOwnership
			}
			seen[e.AttemptID] = true
			sum += e.BilledMicros
		}
		for id, a := range owned.Attempts {
			if a.Dispatched && !seen[id] {
				return SettleResult{}, ErrOwnership
			}
		}
		if sum > rec.BilledMicros {
			return SettleResult{}, ErrOwnership
		}
	}
	o.release(req)
	if o.acct.Exists {
		o.acct.SpentMicros += rec.BilledMicros
	}
	owned.Terminal = fingerprint
	data, _ := json.Marshal(rec)
	_ = json.Unmarshal(data, &owned.Record)
	for _, model := range OwnedQuarantines(rec) {
		l.catalogueQuarantine[model] = true
	}
	financial := rec
	financial.Dimensional = nil
	o.outbox = append(o.outbox, financial.FieldMap())
	l.outboxOrgs[org] = true
	return SettleResult{Applied: true, AccountMissing: !o.acct.Exists, Account: o.acct}, nil
}
