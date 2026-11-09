package dimensional

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

func fixture(t *testing.T) (*Plan, Evidence) {
	t.Helper()
	id := Identity{"synthetic/gemini-flat", "mock-endpoint", "snapshot-1"}
	card := Card{Identity: id, Contract: "synthetic-flat-v1", Rates: map[Unit]string{
		InputToken: "0.000002", CacheRead: "0.0000001", CacheWrite: "0.0000001875",
		OutputToken: "0.00001", Reasoning: "0.00001", InputImage: "0.000002", Search: "0.014",
	}}
	p, err := Admit(card, BoundRequest{id, map[Unit]int64{InputToken: 1000, CacheRead: 100, CacheWrite: 50, OutputToken: 400, Reasoning: 50, InputImage: 2, Search: 1}})
	if err != nil {
		t.Fatal(err)
	}
	// Mutating caller maps after admission cannot change the immutable plan.
	card.Rates[InputImage] = "100"
	return p, Evidence{Identity: id, Quantities: map[Unit]int64{InputToken: 850, CacheRead: 100, CacheWrite: 50, OutputToken: 150, Reasoning: 50, InputImage: 2, Search: 1}}
}

func TestDimensionalExactMicros(t *testing.T) {
	p, e := fixture(t)
	if p.HoldMicros() != 21551 {
		t.Fatalf("hold=%d want 21551", p.HoldMicros())
	}
	c, err := p.Price(e)
	if err != nil || c.UpstreamMicros != 17723 || c.BilledMicros != 18609 || c.Estimated {
		t.Fatalf("charge=%+v error=%v", c, err)
	}
	if len(c.Receipt.Lines) != 7 || c.Receipt.Snapshot != "snapshot-1" {
		t.Fatalf("receipt=%+v", c.Receipt)
	}
	for _, l := range c.Receipt.Lines {
		if l.Quantity == nil || l.Estimated {
			t.Fatal("missing measured line")
		}
	}
}

func TestDimensionalMissingUsesBoundNeverZero(t *testing.T) {
	p, e := fixture(t)
	delete(e.Quantities, InputImage)
	c, err := p.Price(e)
	if err != nil || !c.Estimated || c.UpstreamMicros != 17724 || c.BilledMicros != 18611 {
		t.Fatalf("charge=%+v error=%v", c, err)
	}
	c, err = p.Price(Evidence{})
	if err != nil || !c.Estimated || c.BilledMicros != p.HoldMicros() {
		t.Fatalf("missing all: %+v %v", c, err)
	}
	for _, l := range c.Receipt.Lines {
		if l.Quantity != nil || !l.Estimated {
			t.Fatal("unknown became measured")
		}
	}
}

func TestDimensionalInclusiveAndExplicitZero(t *testing.T) {
	for _, tc := range []struct {
		total string
		want  int64
	}{{"0", 0}, {"0.017723", 18609}} {
		t.Run(tc.total, func(t *testing.T) {
			p, e := fixture(t)
			e.Quantities = nil
			e.InclusiveTotal = &tc.total
			c, err := p.Price(e)
			if err != nil || c.Estimated || c.BilledMicros != tc.want || c.Receipt.State != "provider_total" {
				t.Fatalf("%+v %v", c, err)
			}
			for _, l := range c.Receipt.Lines {
				if l.Quantity != nil || !l.CoveredByTotal {
					t.Fatal("total coverage lost")
				}
			}
		})
	}
}

func TestDimensionalRejectUnknownNegativeAndUnbounded(t *testing.T) {
	for _, rate := range []string{"-1", "NaN", "Inf", "", "1e-3", "0", "9999999999999999999999999"} {
		t.Run(rate, func(t *testing.T) {
			id := Identity{"synthetic/paid", "mock", "snapshot"}
			_, err := Admit(Card{id, "flat-v1", map[Unit]string{Song: rate}}, BoundRequest{id, map[Unit]int64{Song: 1}})
			if err == nil {
				t.Fatal("invalid rate admitted")
			}
		})
	}
	id := Identity{"synthetic/paid", "mock", "snapshot"}
	for _, caps := range []map[Unit]int64{nil, {Song: -1}, {Song: 1_000_000_001}, {Song: 1, Unit("unknown"): 1}} {
		if _, err := Admit(Card{id, "flat-v1", map[Unit]string{Song: "0.08"}}, BoundRequest{id, caps}); err == nil {
			t.Fatal("invalid caps admitted")
		}
	}
	p, e := fixture(t)
	e.Identity.Snapshot = "refreshed"
	if _, err := p.Price(e); err == nil {
		t.Fatal("changed identity accepted")
	}
	p, e = fixture(t)
	e.Quantities[InputImage] = 3
	if _, err := p.Price(e); err == nil {
		t.Fatal("over-cap evidence accepted")
	}
	p, e = fixture(t)
	bad := "-1"
	e.InclusiveTotal = &bad
	if _, err := p.Price(e); err == nil {
		t.Fatal("negative total treated as zero")
	}
}

func TestDimensionalOutputUnitsExactMicros(t *testing.T) {
	// These are explicitly SYNTHETIC contracts, not executable Lyria tariffs.
	for _, tc := range []struct {
		unit                    Unit
		rate                    string
		count, upstream, billed int64
	}{
		{Song, "0.08", 2, 160000, 168000}, {Clip, "0.04", 3, 120000, 126000}, {OutputImage, "0.039", 2, 78000, 81900},
	} {
		t.Run(string(tc.unit), func(t *testing.T) {
			id := Identity{"synthetic/media", "mock", "snapshot"}
			p, err := Admit(Card{id, "synthetic-flat-v1", map[Unit]string{tc.unit: tc.rate}}, BoundRequest{id, map[Unit]int64{tc.unit: tc.count}})
			if err != nil {
				t.Fatal(err)
			}
			c, err := p.Price(Evidence{Identity: id, Quantities: map[Unit]int64{tc.unit: tc.count}})
			if err != nil || c.UpstreamMicros != tc.upstream || c.BilledMicros != tc.billed || p.HoldMicros() != tc.billed {
				t.Fatalf("%+v %v", c, err)
			}
		})
	}
}

func TestDimensionalHoldSettleExactlyOnce(t *testing.T) {
	ctx := context.Background()
	ledger := billing.NewMemoryLedger()
	_, _ = ledger.Grant(ctx, "org", billing.GrantPush{GrantedTotalMicros: 1_000_000, Version: 1})
	p, evidence := fixture(t)
	var calls atomic.Int32
	dispatch := func(context.Context) (Evidence, error) {
		calls.Add(1)
		acct, _ := ledger.Account(ctx, "org")
		if acct.HeldMicros != p.HoldMicros() || acct.SpentMicros != 0 {
			t.Errorf("dispatch without hold: %+v", acct)
		}
		return evidence, nil
	}
	var successes atomic.Int32
	var wg sync.WaitGroup
	for i := 0; i < 10; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			c, err := p.Execute(ctx, ledger, true, billing.Settlement{OrgID: "org", RequestID: "req"}, time.Minute, dispatch)
			if err == nil {
				successes.Add(1)
				if c.BilledMicros != 18609 {
					t.Errorf("charge=%+v", c)
				}
			}
		}()
	}
	wg.Wait()
	acct, _ := ledger.Account(ctx, "org")
	pending, _ := ledger.OutboxPending(ctx, "org")
	if calls.Load() != 1 || successes.Load() != 1 || acct.HeldMicros != 0 || acct.SpentMicros != 18609 || pending != 1 {
		t.Fatalf("calls=%d success=%d account=%+v pending=%d", calls.Load(), successes.Load(), acct, pending)
	}
}

func TestDimensionalRefusedHoldAndBYOK(t *testing.T) {
	p, e := fixture(t)
	ledger := billing.NewMemoryLedger()
	called := false
	dispatch := func(context.Context) (Evidence, error) { called = true; return e, nil }
	rec := billing.Settlement{OrgID: "missing", RequestID: "req"}
	if _, err := p.Execute(context.Background(), ledger, true, rec, time.Minute, dispatch); err == nil || called {
		t.Fatal("dispatched without funded account")
	}
	p, _ = fixture(t)
	c, err := p.Execute(context.Background(), nil, false, rec, time.Minute, dispatch)
	if err != nil || !called || c.BilledMicros != 17723 {
		t.Fatalf("BYOK %+v %v", c, err)
	}
}

func TestDimensionalPartialFailureSettlesEstimate(t *testing.T) {
	ctx := context.Background()
	ledger := billing.NewMemoryLedger()
	_, _ = ledger.Grant(ctx, "org", billing.GrantPush{GrantedTotalMicros: 1_000_000, Version: 1})
	p, _ := fixture(t)
	failure := errors.New("synthetic interrupted generation")
	c, err := p.Execute(ctx, ledger, true, billing.Settlement{OrgID: "org", RequestID: "partial"}, time.Minute, func(context.Context) (Evidence, error) { return Evidence{}, failure })
	acct, _ := ledger.Account(ctx, "org")
	if !errors.Is(err, failure) || !c.Estimated || c.BilledMicros != 21551 || acct.SpentMicros != 21551 || acct.HeldMicros != 0 {
		t.Fatalf("%+v %v %+v", c, err, acct)
	}
}
