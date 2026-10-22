// Package billing is the managed-credit hot path's domain: the worst-case
// estimate a request is held at, the managed-settlement-v1 record, the
// Ledger the reserve, settle, void and grant operations go through, and an
// in-memory Ledger for tests.
//
// The Django ledger is the system of record. A Ledger keeps only the hot-path
// counters (granted, spent, held) and a per-org settlement outbox the drain
// reads. The Redis Ledger is redisstate.ManagedCreditStore; it and
// MemoryLedger implement the same semantics, so callers never tell them
// apart.
package billing

import (
	"context"
	"errors"
	"time"
)

// ReserveMode is how RESERVE treats a hold id that already holds credit.
type ReserveMode string

const (
	// ReserveNew takes a request's first hold. A second RESERVE new for the
	// same hold id adds nothing and reports ok.
	ReserveNew ReserveMode = "new"
	// ReserveAdd adds to the hold id's hold: a managed fallback hop takes its
	// own hold on top of the request's.
	ReserveAdd ReserveMode = "add"
)

// ReserveStatus is what RESERVE decided.
type ReserveStatus string

const (
	// ReserveOK: the hold was taken.
	ReserveOK ReserveStatus = "ok"
	// ReserveExhausted: nothing is left even with every hold released
	// (granted - spent <= 0).
	ReserveExhausted ReserveStatus = "exhausted"
	// ReserveInsufficient: something is left, but less than the hold needs,
	// even with every hold released.
	ReserveInsufficient ReserveStatus = "insufficient"
	// ReserveInFlight: the hold would fit if requests in flight released
	// theirs; the caller may retry shortly.
	ReserveInFlight ReserveStatus = "in_flight"
	// ReserveNoAccount: the org has no account hash. After a Redis loss the
	// drain re-pushes it (mc:needs_push); never a 402.
	ReserveNoAccount ReserveStatus = "no_account"
)

// ErrUnavailable is returned by every Ledger operation that cannot reach its
// store. Managed billing fails closed on it.
var ErrUnavailable = errors.New("billing: managed credit store unavailable")

// SettledMarkerTTL is how long SETTLE and VOID remember a hold id, so a second
// settlement of the same hold is a no-op.
const SettledMarkerTTL = 7 * 24 * time.Hour

// SweepLimit is how many expired holds one RESERVE releases.
const SweepLimit = 50

// Ledger names (credits-v1 "ledger").
const (
	KindRedis  = "redis"
	KindMemory = "memory"
)

// Account is an org's hot-path account (mc:{<org>}:acct). All amounts are
// micros. Exists is false when the org has no account hash.
type Account struct {
	Exists         bool
	GrantedMicros  int64
	GrantedVersion int64
	SpentMicros    int64
	HeldMicros     int64
}

// AvailableMicros is granted - spent - held. It is negative
// after an overshoot.
func (a Account) AvailableMicros() int64 {
	return a.GrantedMicros - a.SpentMicros - a.HeldMicros
}

// ReserveResult is RESERVE's decision and the account after it.
type ReserveResult struct {
	Status  ReserveStatus
	Account Account
}

// SettleResult is what SETTLE or VOID did. Applied is false when the hold id
// was already settled or voided, in which case nothing was written.
// AccountMissing is true when the org had no account hash: the outbox record
// was still written, and the counters were left for the drain's re-push.
type SettleResult struct {
	Applied        bool
	AccountMissing bool
	Account        Account
}

// GrantPush is credits-granted-push-v1's request.
type GrantPush struct {
	GrantedTotalMicros      int64 `json:"granted_total_micros"`
	Version                 int64 `json:"version"`
	SettledSpendFloorMicros int64 `json:"settled_spend_floor_micros"`
}

// GrantResult is GRANT's outcome: Applied is true when the push's version was
// newer than the stored one (or the account did not exist).
type GrantResult struct {
	Applied bool
	Account Account
}

// Ledger is the managed-credit hot path. Every operation is atomic per org.
//
// Callers outside the request pipeline (managed MCP tool calls, capability
// calls, sandbox usage) use the same operations with their own hold id and
// hold TTL, so there is one code path, one outbox record and one set of
// return codes.
type Ledger interface {
	// Reserve sweeps up to SweepLimit expired holds, then holds micros for
	// holdID until ttl passes, unless it refuses.
	Reserve(ctx context.Context, org, holdID string, micros int64, ttl time.Duration, mode ReserveMode) (ReserveResult, error)
	// Settle releases holdID's hold, adds rec.BilledMicros to spent
	// (overshoot allowed) and appends rec as a spend record to the org's
	// outbox, at most once per hold id.
	Settle(ctx context.Context, org, holdID string, rec Settlement) (SettleResult, error)
	// Void releases holdID's hold and appends rec as a void record (zero
	// amounts), at most once per hold id.
	Void(ctx context.Context, org, holdID string, rec Settlement) (SettleResult, error)
	// Grant applies a granted push: granted only when push.Version is newer
	// than the stored version, and spent = max(spent, floor) always. It
	// creates a missing account.
	Grant(ctx context.Context, org string, push GrantPush) (GrantResult, error)
	// Account reads the org's account.
	Account(ctx context.Context, org string) (Account, error)
	// OutboxPending is how many outbox records the drain has not
	// acknowledged: the consumer group's pending plus lag, or the stream
	// length before the group exists.
	OutboxPending(ctx context.Context, org string) (int64, error)
	// MarkNeedsPush asks the drain to re-push the org's account
	// (mc:needs_push).
	MarkNeedsPush(ctx context.Context, org string) error
	// Kind is KindRedis or KindMemory.
	Kind() string
}
