package inclusive

import (
	"context"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

// OwnedLedger is an activation prerequisite, not an optional best-effort hook.
// The legacy Ledger cannot keep dispatched holds past its expiry sweep or its
// seven-day settled marker. MemoryLedger and ManagedCreditStore implement this
// protocol; admission still fails closed for any legacy or unavailable ledger.
//
// States: reserved -> dispatched (reconciliation-owned, no expiry) -> spend.
// Only reserved may become void. Begin and expiry race atomically; if expiry
// wins, Begin refuses dispatch. Settle must atomically persist the single
// immutable request spend, release all attempt holds and append one outbox row.
// A retry of the same terminal amount is a no-op forever; conflicting terminal
// amounts fail. A dispatched hold is never voidable, even by request cleanup.
// ReserveInclusive deduplicates each attemptID, unlike legacy ReserveAdd.
// It refuses quarantined listings across replicas and process restarts.
// Any spend anomaly quarantines its listing atomically with that spend.
// Missing evidence becomes a FULL-hold spend, pending reconciliation; it is not
// an expiring pending hold and must never be silently released or waived.
type OwnedLedger interface {
	billing.Ledger
	ReserveInclusive(ctx context.Context, org, requestID, attemptID, model string, micros int64, ttl time.Duration, mode billing.ReserveMode) (billing.ReserveResult, error)
	BeginInclusive(ctx context.Context, org, requestID, attemptID string) error
}
