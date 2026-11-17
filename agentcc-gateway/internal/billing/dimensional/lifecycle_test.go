package dimensional

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

type ambiguousSettle struct {
	billing.Ledger
	calls int
}

func (l *ambiguousSettle) Settle(ctx context.Context, org, hold string, rec billing.Settlement) (billing.SettleResult, error) {
	l.calls++
	r, err := l.Ledger.Settle(ctx, org, hold, rec)
	if l.calls == 1 {
		return r, errors.New("synthetic lost acknowledgement after write")
	}
	return r, err
}

func TestDimensionalAmbiguousSettlementReleasesOnce(t *testing.T) {
	ctx := context.Background()
	l := &ambiguousSettle{Ledger: billing.NewMemoryLedger()}
	_, _ = l.Grant(ctx, "org", billing.GrantPush{GrantedTotalMicros: 1_000_000, Version: 1})
	p, evidence := fixture(t)
	c, err := p.Execute(ctx, l, true, billing.Settlement{OrgID: "org", RequestID: "retry"}, time.Minute, func(context.Context) (Evidence, error) { return evidence, nil })
	account, _ := l.Account(ctx, "org")
	pending, _ := l.OutboxPending(ctx, "org")
	if err != nil || l.calls != 2 || account.HeldMicros != 0 || account.SpentMicros != c.BilledMicros || pending != 1 {
		t.Fatalf("calls=%d account=%+v pending=%d charge=%+v error=%v", l.calls, account, pending, c, err)
	}
}

type cancelAfterHold struct {
	billing.Ledger
	cancel context.CancelFunc
}

func (l *cancelAfterHold) Reserve(ctx context.Context, org, hold string, micros int64, ttl time.Duration, mode billing.ReserveMode) (billing.ReserveResult, error) {
	r, err := l.Ledger.Reserve(ctx, org, hold, micros, ttl, mode)
	l.cancel()
	return r, err
}
func TestDimensionalCancellationBeforeDispatchVoidsOnce(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	l := &cancelAfterHold{Ledger: billing.NewMemoryLedger(), cancel: cancel}
	_, _ = l.Grant(ctx, "org", billing.GrantPush{GrantedTotalMicros: 1_000_000, Version: 1})
	p, _ := fixture(t)
	_, err := p.Execute(ctx, l, true, billing.Settlement{OrgID: "org", RequestID: "cancel"}, time.Minute, func(context.Context) (Evidence, error) {
		t.Fatal("dispatch after cancellation")
		return Evidence{}, nil
	})
	account, _ := l.Account(context.Background(), "org")
	pending, _ := l.OutboxPending(context.Background(), "org")
	if !errors.Is(err, context.Canceled) || account.HeldMicros != 0 || account.SpentMicros != 0 || pending != 1 {
		t.Fatalf("account=%+v pending=%d error=%v", account, pending, err)
	}
}
