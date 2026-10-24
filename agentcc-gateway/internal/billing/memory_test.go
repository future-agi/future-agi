package billing

import (
	"context"
	"sync"
	"testing"
	"time"
)

var ctx = context.Background()

func grant(t *testing.T, l Ledger, org string, micros, version, floor int64) GrantResult {
	t.Helper()
	res, err := l.Grant(ctx, org, GrantPush{GrantedTotalMicros: micros, Version: version, SettledSpendFloorMicros: floor})
	if err != nil {
		t.Fatal(err)
	}
	return res
}

func TestMemoryReserveDecisions(t *testing.T) {
	l := NewMemoryLedger()
	if res, _ := l.Reserve(ctx, "org", "r0", 10, time.Minute, ReserveNew); res.Status != ReserveNoAccount {
		t.Fatalf("no account: %v", res.Status)
	}
	grant(t, l, "org", 100, 1, 0)
	for _, step := range []struct {
		hold   string
		micros int64
		want   ReserveStatus
	}{
		{"r1", 60, ReserveOK},
		{"r2", 60, ReserveInFlight},      // 100 left, 60 held: would fit once r1 ends
		{"r3", 150, ReserveInsufficient}, // more than is left even with no holds
		{"r4", 40, ReserveOK},
	} {
		if res, _ := l.Reserve(ctx, "org", step.hold, step.micros, time.Minute, ReserveNew); res.Status != step.want {
			t.Fatalf("reserve %s %d: %v, want %v", step.hold, step.micros, res.Status, step.want)
		}
	}
	if _, err := l.Settle(ctx, "org", "r1", Settlement{BilledMicros: 100}); err != nil {
		t.Fatal(err)
	}
	if res, _ := l.Reserve(ctx, "org", "r5", 1, time.Minute, ReserveNew); res.Status != ReserveExhausted {
		t.Fatalf("spent the whole grant: %v, want exhausted", res.Status)
	}
}

func TestMemoryReserveNewIsIdempotentAndAddGrows(t *testing.T) {
	l := NewMemoryLedger()
	grant(t, l, "org", 1000, 1, 0)
	l.Reserve(ctx, "org", "r", 100, time.Minute, ReserveNew)
	l.Reserve(ctx, "org", "r", 100, time.Minute, ReserveNew)
	if a, _ := l.Account(ctx, "org"); a.HeldMicros != 100 {
		t.Fatalf("a second RESERVE new held again: held %d", a.HeldMicros)
	}
	l.Reserve(ctx, "org", "r", 50, time.Minute, ReserveAdd)
	if a, _ := l.Account(ctx, "org"); a.HeldMicros != 150 {
		t.Fatalf("RESERVE add: held %d, want 150", a.HeldMicros)
	}
	res, _ := l.Settle(ctx, "org", "r", Settlement{BilledMicros: 120})
	if a := res.Account; a.HeldMicros != 0 || a.SpentMicros != 120 {
		t.Fatalf("settle released the whole hold? %+v", a)
	}
}

func TestMemorySweepExpiredHolds(t *testing.T) {
	l := NewMemoryLedger()
	now := time.Unix(1_000, 0)
	l.now = func() time.Time { return now }
	grant(t, l, "org", 100, 1, 0)
	l.Reserve(ctx, "org", "old", 90, time.Second, ReserveNew)
	now = now.Add(2 * time.Second)
	res, _ := l.Reserve(ctx, "org", "new", 90, time.Second, ReserveNew)
	if res.Status != ReserveOK || res.Account.HeldMicros != 90 {
		t.Fatalf("the expired hold was not swept: %+v", res)
	}
}

func TestMemorySettleAndVoidOncePerHold(t *testing.T) {
	l := NewMemoryLedger()
	grant(t, l, "org", 100, 1, 0)
	l.Reserve(ctx, "org", "r", 40, time.Minute, ReserveNew)
	first, _ := l.Settle(ctx, "org", "r", Settlement{RequestID: "r", BilledMicros: 30})
	second, _ := l.Settle(ctx, "org", "r", Settlement{RequestID: "r", BilledMicros: 30})
	void, _ := l.Void(ctx, "org", "r", Settlement{RequestID: "r"})
	if !first.Applied || second.Applied || void.Applied {
		t.Fatalf("applied: settle %v, settle again %v, void after %v", first.Applied, second.Applied, void.Applied)
	}
	if a, _ := l.Account(ctx, "org"); a.SpentMicros != 30 || a.HeldMicros != 0 {
		t.Fatalf("account = %+v", a)
	}
	if out := l.Outbox("org"); len(out) != 1 || out[0]["kind"] != KindSpend || out[0]["billed_micros"] != "30" {
		t.Fatalf("outbox = %v", out)
	}
	if !l.HasOutboxOrg("org") {
		t.Fatal("org not in the outbox discovery set")
	}

	l.Reserve(ctx, "org", "v", 20, time.Minute, ReserveNew)
	res, _ := l.Void(ctx, "org", "v", Settlement{RequestID: "v", BilledMicros: 99, HoldMicros: 20})
	out := l.Outbox("org")
	if !res.Applied || res.Account.HeldMicros != 0 || res.Account.SpentMicros != 30 {
		t.Fatalf("void: %+v", res)
	}
	if last := out[len(out)-1]; last["kind"] != KindVoid || last["billed_micros"] != "0" || last["hold_micros"] != "20" {
		t.Fatalf("void record = %v, want zero amounts and the released hold", last)
	}
}

func TestMemoryGrantVersionAndFloor(t *testing.T) {
	l := NewMemoryLedger()
	if res := grant(t, l, "org", 100, 1, 0); !res.Applied {
		t.Fatal("first push not applied")
	}
	l.Reserve(ctx, "org", "r", 10, time.Minute, ReserveNew)
	l.Settle(ctx, "org", "r", Settlement{BilledMicros: 25})
	if res := grant(t, l, "org", 500, 1, 0); res.Applied || res.Account.GrantedMicros != 100 {
		t.Fatalf("a stale version was applied: %+v", res)
	}
	if res := grant(t, l, "org", 500, 1, 60); res.Account.SpentMicros != 60 {
		t.Fatalf("the floor is always applied: %+v", res)
	}
	if res := grant(t, l, "org", 500, 2, 10); !res.Applied || res.Account.GrantedMicros != 500 || res.Account.SpentMicros != 60 {
		t.Fatalf("v2 with a lower floor: %+v", res)
	}
}

func TestMemorySettleOnMissingAccountWritesRecordOnly(t *testing.T) {
	l := NewMemoryLedger()
	grant(t, l, "org", 100, 1, 0)
	l.Reserve(ctx, "org", "r", 10, time.Minute, ReserveNew)
	l.DropAccount("org")
	res, _ := l.Settle(ctx, "org", "r", Settlement{BilledMicros: 10})
	if !res.Applied || !res.AccountMissing || res.Account.Exists {
		t.Fatalf("settle on a lost account: %+v", res)
	}
	if r, _ := l.Reserve(ctx, "org", "next", 1, time.Minute, ReserveNew); r.Status != ReserveNoAccount {
		t.Fatalf("a settle recreated a partial account: next reserve %v", r.Status)
	}
	if len(l.Outbox("org")) != 1 {
		t.Fatal("the spend record was not written")
	}
}

func TestMemoryConcurrentReserve(t *testing.T) {
	l := NewMemoryLedger()
	grant(t, l, "org", 1_000_000, 1, 0)
	var wg sync.WaitGroup
	var mu sync.Mutex
	ok := 0
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			res, _ := l.Reserve(ctx, "org", "r"+string(rune('A'+i)), 30_000, time.Minute, ReserveNew)
			if res.Status == ReserveOK {
				mu.Lock()
				ok++
				mu.Unlock()
			}
		}(i)
	}
	wg.Wait()
	if ok != 33 {
		t.Fatalf("%d holds of $0.03 fit in $1.00, want 33", ok)
	}
}

func TestMemoryGrantRecreateCountsHoldsInFlight(t *testing.T) {
	l := NewMemoryLedger()
	grant(t, l, "org", 1000, 1, 0)
	l.Reserve(ctx, "org", "in-flight", 300, time.Minute, ReserveNew)
	l.DropAccount("org")
	if res := grant(t, l, "org", 1000, 1, 0); !res.Applied || res.Account.HeldMicros != 300 {
		t.Fatalf("recreated account = %+v, want applied with the in-flight hold held", res)
	}
}
