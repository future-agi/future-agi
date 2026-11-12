package redisstate

import (
	"context"
	"fmt"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
)

type generationMock struct{}

func (generationMock) Generation(_ context.Context, id string) (inclusive.Generation, error) {
	return inclusive.Generation{ID: id, Model: "google/gemini-3.5-flash", CostUSD: "0.0000095"}, nil
}

type refundMock struct {
	mu        sync.Mutex
	amounts   map[string]int64
	ambiguous bool
}

func (f *refundMock) Refund(_ context.Context, org, req string, n int64, _ string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	id := org + "/" + req
	if prior, ok := f.amounts[id]; ok && prior != n {
		return fmt.Errorf("conflicting refund")
	}
	f.amounts[id] = n
	if f.ambiguous {
		f.ambiguous = false
		return fmt.Errorf("ack lost after commit")
	}
	return nil
}
func TestOwnedReconciliationCrashAndReplay(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	mustGrant(t, s, "org-owned", 10000, 1, 0)
	if _, e := s.ReserveInclusive(bg, "org-owned", "req", "req.1", "google/gemini-3.5-flash", 100, time.Hour, billing.ReserveNew); e != nil {
		t.Fatal(e)
	}
	if e := s.BeginInclusive(bg, "org-owned", "req", "req.1"); e != nil {
		t.Fatal(e)
	}
	rec := ownedRecord("req", 100)
	rec.Kind = billing.KindSpend
	rec.UsageEstimated = true
	rec.Dimensional.InclusiveAttempts[0].Estimated = true
	if _, e := s.Settle(bg, "org-owned", "req", rec); e != nil {
		t.Fatal(e)
	}
	refunds := &refundMock{amounts: map[string]int64{}, ambiguous: true}
	if _, e := inclusive.Reconcile(bg, rec, generationMock{}, s, refunds); e == nil {
		t.Fatal("lost ack not reported")
	}
	if rdb.SCard(bg, s.ownedKeys("org-owned", "")[8]).Val() != 1 {
		t.Fatal("ambiguous refund lost pending work")
	}
	// New adapter models gateway process restart with no local ownership state.
	restarted := NewManagedCreditStore(s.client, s.prefix)
	var wg sync.WaitGroup
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, e := inclusive.Reconcile(bg, rec, generationMock{}, restarted, refunds); e != nil {
				t.Error(e)
			}
		}()
	}
	wg.Wait()
	if refunds.amounts["org-owned/req"] != 89 || len(refunds.amounts) != 1 {
		t.Fatalf("refunds %+v", refunds.amounts)
	}
	if rdb.SCard(bg, s.ownedKeys("org-owned", "")[8]).Val() != 0 {
		t.Fatal("finished work still pending")
	}
	if n := rdb.XLen(bg, s.keys("org-owned").outbox).Val(); n != 1 {
		t.Fatalf("reconciliation emitted %d spends", n)
	}
}
func TestOwnedRecoverAfterDispatchCrash(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	mustGrant(t, s, "org-owned", 10000, 1, 0)
	_, e := s.ReserveInclusive(bg, "org-owned", "req", "req.1", "google/gemini-3.5-flash", 100, time.Hour, billing.ReserveNew)
	if e != nil {
		t.Fatal(e)
	}
	rec := ownedRecord("req", 0)
	rec.APIKeyID = "synthetic-audit"
	rec.AgentID = "agent"
	rec.AgentRunID = "run"
	if e = s.BeginInclusiveWithRecord(bg, "org-owned", "req", "req.1", rec); e != nil {
		t.Fatal(e)
	}
	restart := NewManagedCreditStore(s.client, s.prefix)
	restart.now = func() time.Time { return time.Now().Add(25 * time.Hour) }
	if _, e = restart.RecoverInclusive(bg, "org-owned", "req"); e != nil {
		t.Fatal(e)
	}
	a := mustAccount(t, s, "org-owned")
	if a.SpentMicros != 100 || a.HeldMicros != 0 {
		t.Fatalf("crash waived liability %+v", a)
	}
	rows, _ := rdb.XRange(bg, s.keys("org-owned").outbox, "-", "+").Result()
	if len(rows) != 1 || rows[0].Values["api_key_id"] != "synthetic-audit" || rows[0].Values["agent_run_id"] != "run" {
		t.Fatalf("lost audit attribution %+v", rows)
	}
	if q, e := s.InclusiveQuarantined(bg, "org-owned", "google/gemini-3.5-flash"); e != nil || !q {
		t.Fatalf("missing identity not quarantined %t %v", q, e)
	}
	if e = s.ReconcileOrg(bg, "org-owned", generationMock{}, &refundMock{amounts: map[string]int64{}}); e == nil {
		t.Fatal("missing generation waived")
	}
	if rdb.SCard(bg, s.ownedKeys("org-owned", "")[8]).Val() != 1 {
		t.Fatal("unresolved liability expired")
	}
}
