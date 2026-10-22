package credits

import (
	"context"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

// OrgLedger is the managed-credit hot path the gateway holds managed
// requests on and the admin routes read and push to: the Redis ledger
// (redisstate.ManagedCreditStore), the in-memory one (only with
// managed.require_redis=false, for tests), or, with no store, a ledger that
// fails closed: every operation returns billing.ErrUnavailable, so managed
// traffic gets 503 managed_billing_unavailable.
type OrgLedger struct {
	l billing.Ledger // nil: no store, fail closed
}

// NewOrgLedger returns a ledger over l. A nil l is an in-memory ledger.
func NewOrgLedger(l billing.Ledger) *OrgLedger {
	if l == nil {
		l = billing.NewMemoryLedger()
	}
	return &OrgLedger{l: l}
}

// NewUnavailableOrgLedger returns the ledger of a gateway that requires
// Redis for managed billing and has none.
func NewUnavailableOrgLedger() *OrgLedger { return &OrgLedger{} }

// Ledger returns the store behind the ledger, or nil when it has none. A
// caller outside the request pipeline (a managed tool or capability call)
// reserves, settles and voids through it with its own hold id and TTL.
func (o *OrgLedger) Ledger() billing.Ledger { return o.l }

// Kind is the credits-v1 ledger name: redis or memory; "" with no store.
func (o *OrgLedger) Kind() string {
	if o.l == nil {
		return ""
	}
	return o.l.Kind()
}

// Reserve is billing.Ledger.Reserve.
func (o *OrgLedger) Reserve(ctx context.Context, org, holdID string, micros int64, ttl time.Duration, mode billing.ReserveMode) (billing.ReserveResult, error) {
	if o.l == nil {
		return billing.ReserveResult{}, billing.ErrUnavailable
	}
	return o.l.Reserve(ctx, org, holdID, micros, ttl, mode)
}

// Settle is billing.Ledger.Settle.
func (o *OrgLedger) Settle(ctx context.Context, org, holdID string, rec billing.Settlement) (billing.SettleResult, error) {
	if o.l == nil {
		return billing.SettleResult{}, billing.ErrUnavailable
	}
	return o.l.Settle(ctx, org, holdID, rec)
}

// Void is billing.Ledger.Void.
func (o *OrgLedger) Void(ctx context.Context, org, holdID string, rec billing.Settlement) (billing.SettleResult, error) {
	if o.l == nil {
		return billing.SettleResult{}, billing.ErrUnavailable
	}
	return o.l.Void(ctx, org, holdID, rec)
}

// Grant is billing.Ledger.Grant.
func (o *OrgLedger) Grant(ctx context.Context, org string, push billing.GrantPush) (billing.GrantResult, error) {
	if o.l == nil {
		return billing.GrantResult{}, billing.ErrUnavailable
	}
	return o.l.Grant(ctx, org, push)
}

// Account is billing.Ledger.Account.
func (o *OrgLedger) Account(ctx context.Context, org string) (billing.Account, error) {
	if o.l == nil {
		return billing.Account{}, billing.ErrUnavailable
	}
	return o.l.Account(ctx, org)
}

// OutboxPending is billing.Ledger.OutboxPending.
func (o *OrgLedger) OutboxPending(ctx context.Context, org string) (int64, error) {
	if o.l == nil {
		return 0, billing.ErrUnavailable
	}
	return o.l.OutboxPending(ctx, org)
}

// MarkNeedsPush is billing.Ledger.MarkNeedsPush.
func (o *OrgLedger) MarkNeedsPush(ctx context.Context, org string) error {
	if o.l == nil {
		return billing.ErrUnavailable
	}
	return o.l.MarkNeedsPush(ctx, org)
}

// BalanceMicros is the org's available credit (granted - spent - held), 0
// when it has no account or the store is unreachable.
func (o *OrgLedger) BalanceMicros(orgID string) int64 {
	acct, err := o.Account(context.Background(), orgID)
	if err != nil {
		return 0
	}
	return acct.AvailableMicros()
}

// BalanceUSD is BalanceMicros in dollars.
func (o *OrgLedger) BalanceUSD(orgID string) float64 {
	return billing.MicrosToUSD(o.BalanceMicros(orgID))
}

// SetMicros makes the org's available credit micros by pushing a grant one
// version newer, creating the account if needed. It is for tests and local
// tools: in a gateway, granted is pushed by the control plane.
func (o *OrgLedger) SetMicros(orgID string, micros int64) {
	acct, err := o.Account(context.Background(), orgID)
	if err != nil {
		return
	}
	_, _ = o.Grant(context.Background(), orgID, billing.GrantPush{
		GrantedTotalMicros: micros + acct.SpentMicros + acct.HeldMicros,
		Version:            acct.GrantedVersion + 1,
	})
}
