package billing

import (
	"context"
	"sort"
	"sync"
	"time"
)

// MemoryLedger is a Ledger held in this process's memory, with the Redis
// ledger's semantics. Only a gateway with managed.require_redis=false uses
// it, which is for tests: each replica holds its own balance in memory.
type MemoryLedger struct {
	mu              sync.Mutex
	now             func() time.Time
	overdraftMicros int64
	orgs            map[string]*memOrg
	outboxOrgs      map[string]bool
	needsPush       map[string]bool
}

type memOrg struct {
	acct    Account
	holds   map[string]int64
	holdExp map[string]time.Time
	settled map[string]time.Time // hold id → when the marker expires
	outbox  []map[string]string
}

// NewMemoryLedger returns an empty in-memory ledger.
func NewMemoryLedger() *MemoryLedger {
	return &MemoryLedger{
		now:        time.Now,
		orgs:       map[string]*memOrg{},
		outboxOrgs: map[string]bool{},
		needsPush:  map[string]bool{},
	}
}

// SetMaxOverdraftMicros lets RESERVE take a balance down to -micros
// (managed.max_overdraft_usd). Call it before use.
func (l *MemoryLedger) SetMaxOverdraftMicros(micros int64) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.overdraftMicros = max(micros, 0)
}

// Kind is KindMemory.
func (l *MemoryLedger) Kind() string { return KindMemory }

func (l *MemoryLedger) org(id string) *memOrg {
	o := l.orgs[id]
	if o == nil {
		o = &memOrg{holds: map[string]int64{}, holdExp: map[string]time.Time{}, settled: map[string]time.Time{}}
		l.orgs[id] = o
	}
	return o
}

// Reserve implements Ledger.
func (l *MemoryLedger) Reserve(_ context.Context, org, holdID string, micros int64, ttl time.Duration, mode ReserveMode) (ReserveResult, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	o := l.org(org)
	if !o.acct.Exists {
		return ReserveResult{Status: ReserveNoAccount}, nil
	}
	now := l.now()
	o.sweep(now)
	micros = max(micros, 0)
	if _, held := o.holds[holdID]; held && mode != ReserveAdd {
		return ReserveResult{Status: ReserveOK, Account: o.acct}, nil
	}
	status := decideReserve(o.acct, micros, l.overdraftMicros)
	if status == ReserveOK {
		o.holds[holdID] += micros
		o.holdExp[holdID] = now.Add(ttl)
		o.acct.HeldMicros += micros
	}
	return ReserveResult{Status: status, Account: o.acct}, nil
}

// sweep releases up to SweepLimit holds that expired by now, oldest first.
func (o *memOrg) sweep(now time.Time) {
	var expired []string
	for id, at := range o.holdExp {
		if !at.After(now) {
			expired = append(expired, id)
		}
	}
	sort.Slice(expired, func(i, j int) bool { return o.holdExp[expired[i]].Before(o.holdExp[expired[j]]) })
	if len(expired) > SweepLimit {
		expired = expired[:SweepLimit]
	}
	for _, id := range expired {
		o.release(id)
	}
}

// release drops holdID's hold and returns its amount.
func (o *memOrg) release(holdID string) int64 {
	amt := o.holds[holdID]
	delete(o.holds, holdID)
	delete(o.holdExp, holdID)
	o.acct.HeldMicros = max(o.acct.HeldMicros-amt, 0)
	return amt
}

// Settle implements Ledger.
func (l *MemoryLedger) Settle(_ context.Context, org, holdID string, rec Settlement) (SettleResult, error) {
	return l.finish(org, holdID, rec.asSpend(), rec.BilledMicros), nil
}

// Void implements Ledger.
func (l *MemoryLedger) Void(_ context.Context, org, holdID string, rec Settlement) (SettleResult, error) {
	return l.finish(org, holdID, rec.asVoid(), 0), nil
}

// finish is SETTLE and VOID: once per hold id, release the hold, add spend
// to spent when the account exists (0 for a void), and append the record.
func (l *MemoryLedger) finish(org, holdID string, rec Settlement, spend int64) SettleResult {
	l.mu.Lock()
	defer l.mu.Unlock()
	o := l.org(org)
	now := l.now()
	if until, ok := o.settled[holdID]; ok && until.After(now) {
		return SettleResult{Account: o.acct}
	}
	o.release(holdID)
	missing := !o.acct.Exists
	if !missing {
		o.acct.SpentMicros += max(spend, 0)
	}
	o.outbox = append(o.outbox, rec.FieldMap())
	o.settled[holdID] = now.Add(SettledMarkerTTL)
	l.outboxOrgs[org] = true
	return SettleResult{Applied: true, AccountMissing: missing, Account: o.acct}
}

// Grant implements Ledger.
func (l *MemoryLedger) Grant(_ context.Context, org string, push GrantPush) (GrantResult, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	o := l.org(org)
	created := !o.acct.Exists
	if created {
		// A recreated account counts the holds still in flight as held.
		var held int64
		for _, amt := range o.holds {
			held += amt
		}
		o.acct = Account{Exists: true, HeldMicros: held}
	}
	applied := created || push.Version > o.acct.GrantedVersion
	if applied {
		o.acct.GrantedMicros, o.acct.GrantedVersion = push.GrantedTotalMicros, push.Version
	}
	o.acct.SpentMicros = max(o.acct.SpentMicros, push.SettledSpendFloorMicros)
	return GrantResult{Applied: applied, Account: o.acct}, nil
}

// Account implements Ledger.
func (l *MemoryLedger) Account(_ context.Context, org string) (Account, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if o := l.orgs[org]; o != nil {
		return o.acct, nil
	}
	return Account{}, nil
}

// OutboxPending implements Ledger: nothing drains a memory outbox, so every
// record is pending.
func (l *MemoryLedger) OutboxPending(_ context.Context, org string) (int64, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if o := l.orgs[org]; o != nil {
		return int64(len(o.outbox)), nil
	}
	return 0, nil
}

// MarkNeedsPush implements Ledger.
func (l *MemoryLedger) MarkNeedsPush(_ context.Context, org string) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.needsPush[org] = true
	return nil
}

// Outbox returns a copy of the org's outbox records, oldest first.
func (l *MemoryLedger) Outbox(org string) []map[string]string {
	l.mu.Lock()
	defer l.mu.Unlock()
	o := l.orgs[org]
	if o == nil {
		return nil
	}
	out := make([]map[string]string, len(o.outbox))
	copy(out, o.outbox)
	return out
}

// NeedsPush reports whether the org is in the needs-push set.
func (l *MemoryLedger) NeedsPush(org string) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.needsPush[org]
}

// HasOutboxOrg reports whether the org is in the outbox discovery set.
func (l *MemoryLedger) HasOutboxOrg(org string) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.outboxOrgs[org]
}

// DropAccount deletes the org's account hash, as a Redis loss would.
func (l *MemoryLedger) DropAccount(org string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if o := l.orgs[org]; o != nil {
		o.acct = Account{}
	}
}
