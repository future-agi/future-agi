package credits

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/billing"
)

func TestOrgLedgerSetMicrosAndBalance(t *testing.T) {
	l := NewOrgLedger(nil)
	if l.Kind() != billing.KindMemory {
		t.Fatalf("NewOrgLedger(nil) kind = %q, want memory", l.Kind())
	}
	l.SetMicros("org-1", auth.USDToMicros(2))
	l.SetMicros("org-2", auth.USDToMicros(5))
	if l.BalanceUSD("org-1") != 2 || l.BalanceUSD("org-2") != 5 || l.BalanceUSD("org-3") != 0 {
		t.Fatalf("balances leak across orgs: %v %v %v", l.BalanceUSD("org-1"), l.BalanceUSD("org-2"), l.BalanceUSD("org-3"))
	}
	ctx := context.Background()
	l.Reserve(ctx, "org-1", "r", 500_000, time.Minute, billing.ReserveNew)
	if got := l.BalanceMicros("org-1"); got != 1_500_000 {
		t.Fatalf("balance with a hold = %d, want available 1500000", got)
	}
	l.Settle(ctx, "org-1", "r", billing.Settlement{BilledMicros: 2_500_000})
	if got := l.BalanceMicros("org-1"); got != -500_000 {
		t.Fatalf("a settlement past the grant is recorded: balance %d, want -500000", got)
	}
	l.SetMicros("org-1", auth.USDToMicros(1))
	if acct, _ := l.Account(ctx, "org-1"); acct.AvailableMicros() != 1_000_000 || acct.SpentMicros != 2_500_000 || acct.GrantedVersion != 2 {
		t.Fatalf("SetMicros keeps spent and pushes a newer version: %+v", acct)
	}
}

func TestUnavailableOrgLedgerFailsClosed(t *testing.T) {
	l := NewUnavailableOrgLedger()
	ctx := context.Background()
	if _, err := l.Reserve(ctx, "org", "r", 1, time.Minute, billing.ReserveNew); !errors.Is(err, billing.ErrUnavailable) {
		t.Fatalf("reserve: %v", err)
	}
	if _, err := l.Grant(ctx, "org", billing.GrantPush{GrantedTotalMicros: 1, Version: 1}); !errors.Is(err, billing.ErrUnavailable) {
		t.Fatalf("grant: %v", err)
	}
	if l.BalanceMicros("org") != 0 || l.Kind() != "" || l.Ledger() != nil {
		t.Fatal("an unavailable ledger reports a balance or a store")
	}
}
