package redisstate

import (
	"context"
	"fmt"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

type ownedContract interface {
	ReserveInclusive(context.Context, string, string, string, string, int64, time.Duration, billing.ReserveMode) (billing.ReserveResult, error)
	BeginInclusive(context.Context, string, string, string) error
}

func ownerFor(t *testing.T, s *ManagedCreditStore) ownedContract {
	t.Helper()
	o, ok := any(s).(ownedContract)
	if !ok {
		return legacyOwnedAdapter{s}
	}
	return o
}

// The RED baseline uses its actual reserve/cleanup operations. This compatibility
// shim adds no retention or deduplication, so failures demonstrate lost money,
// not a missing symbol or unavailable interface.
type legacyOwnedAdapter struct{ *ManagedCreditStore }

func (l legacyOwnedAdapter) ReserveInclusive(ctx context.Context, org, req, attempt, model string, amount int64, ttl time.Duration, mode billing.ReserveMode) (billing.ReserveResult, error) {
	return l.Reserve(ctx, org, req, amount, ttl, mode)
}
func (l legacyOwnedAdapter) BeginInclusive(context.Context, string, string, string) error { return nil }
func ownedRecord(id string, billed int64) billing.Settlement {
	r := spendRecord("org-owned", id, billed)
	r.HoldMicros = 100
	r.MarkupBps = 500
	r.Dimensional = &billing.DimensionalReceipt{Contract: "openrouter-inclusive-r601-image-1", Model: "google/gemini-3.5-flash", Endpoint: "openrouter", Snapshot: "synthetic", State: "provider_total", Lines: []billing.DimensionalLine{}, InclusiveAttempts: []models.InclusiveAttempt{{AttemptID: id + ".1", Model: "google/gemini-3.5-flash", ServedModel: "google/gemini-3.5-flash", GenerationID: "synthetic-generation", HoldMicros: 100, BilledMicros: billed}}}
	return r
}
func TestOwnedReserveDeduplicatesConcurrentAttempts(t *testing.T) {
	s, _, _ := managedTestStore(t)
	o := ownerFor(t, s)
	mustGrant(t, s, "org-owned", 10000, 1, 0)
	var wg sync.WaitGroup
	errs := make(chan error, 32)
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			r, e := o.ReserveInclusive(bg, "org-owned", "req", "req.1", "google/gemini-3.5-flash", 100, time.Hour, billing.ReserveAdd)
			if e != nil || r.Status != billing.ReserveOK {
				errs <- fmt.Errorf("%s %v", r.Status, e)
			}
		}()
	}
	wg.Wait()
	close(errs)
	for e := range errs {
		t.Error(e)
	}
	if a := mustAccount(t, s, "org-owned"); a.HeldMicros != 100 {
		t.Fatalf("duplicate hold: %+v", a)
	}
	if _, e := o.ReserveInclusive(bg, "org-owned", "req", "req.1", "google/gemini-3.5-flash", 101, time.Hour, billing.ReserveAdd); e == nil {
		t.Fatal("conflicting attempt accepted")
	}
}
func TestOwnedDispatchSurvivesExpiryAndCleanup(t *testing.T) {
	s, _, _ := managedTestStore(t)
	o := ownerFor(t, s)
	mustGrant(t, s, "org-owned", 10000, 1, 0)
	now := time.Now()
	s.now = func() time.Time { return now }
	if _, e := o.ReserveInclusive(bg, "org-owned", "req", "req.1", "google/gemini-3.5-flash", 100, time.Second, billing.ReserveNew); e != nil {
		t.Fatal(e)
	}
	if e := o.BeginInclusive(bg, "org-owned", "req", "req.1"); e != nil {
		t.Fatal(e)
	}
	now = now.Add(48 * time.Hour)
	if _, e := s.Reserve(bg, "org-owned", "sweep", 1, time.Hour, billing.ReserveNew); e != nil {
		t.Fatal(e)
	}
	if _, e := s.Void(bg, "org-owned", "req", ownedRecord("req", 0)); e == nil {
		t.Fatal("cleanup voided dispatched liability")
	}
	if a := mustAccount(t, s, "org-owned"); a.HeldMicros != 101 {
		t.Fatalf("expiry released owned hold: %+v", a)
	}
}
func TestOwnedPermanentTerminalAndAtomicQuarantine(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	o := ownerFor(t, s)
	mustGrant(t, s, "org-owned", 10000, 1, 0)
	_, e := o.ReserveInclusive(bg, "org-owned", "req", "req.1", "google/gemini-3.5-flash", 100, time.Hour, billing.ReserveNew)
	if e != nil {
		t.Fatal(e)
	}
	if e = o.BeginInclusive(bg, "org-owned", "req", "req.1"); e != nil {
		t.Fatal(e)
	}
	rec := ownedRecord("req", 105)
	var wg sync.WaitGroup
	errs := make(chan error, 16)
	for i := 0; i < 16; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, e := s.Settle(bg, "org-owned", "req", rec)
			if e != nil {
				errs <- e
			}
		}()
	}
	wg.Wait()
	close(errs)
	for e := range errs {
		t.Error(e)
	}
	if a := mustAccount(t, s, "org-owned"); a.SpentMicros != 105 || a.HeldMicros != 0 {
		t.Fatalf("not single spend: %+v", a)
	}
	if ttl := rdb.TTL(bg, s.keys("org-owned").settled("req")).Val(); ttl != -1 {
		t.Fatalf("terminal expires: %s", ttl)
	}
	if n := rdb.XLen(bg, s.keys("org-owned").outbox).Val(); n != 1 {
		t.Fatalf("%d spends", n)
	}
	entries, _ := rdb.XRange(bg, s.keys("org-owned").outbox, "-", "+").Result()
	if entries[0].Values["v"] != "1" || entries[0].Values["dimensions"] != nil {
		t.Fatal("non-v1 financial outbox")
	}
	if _, e = o.ReserveInclusive(bg, "org-owned", "later", "later.1", "google/gemini-3.5-flash", 100, time.Hour, billing.ReserveNew); e == nil {
		t.Fatal("quarantined listing admitted")
	}
	s.now = func() time.Time { return time.Now().Add(30 * 24 * time.Hour) }
	if res, e := s.Settle(bg, "org-owned", "req", rec); e != nil || res.Applied {
		t.Fatalf("terminal replay: %+v %v", res, e)
	}
	rec.BilledMicros++
	if _, e = s.Settle(bg, "org-owned", "req", rec); e == nil {
		t.Fatal("conflicting spend accepted")
	}
}
func TestOwnedExpiryWinsBeforeBegin(t *testing.T) {
	s, _, _ := managedTestStore(t)
	o := ownerFor(t, s)
	mustGrant(t, s, "org-owned", 10000, 1, 0)
	now := time.Now()
	s.now = func() time.Time { return now }
	_, e := o.ReserveInclusive(bg, "org-owned", "req", "req.1", "google/gemini-3.5-flash", 100, time.Second, billing.ReserveNew)
	if e != nil {
		t.Fatal(e)
	}
	now = now.Add(time.Second)
	if e = o.BeginInclusive(bg, "org-owned", "req", "req.1"); e == nil {
		t.Fatal("dispatch after TTL")
	}
	if _, e = s.Void(bg, "org-owned", "req", ownedRecord("req", 0)); e != nil {
		t.Fatal(e)
	}
	if a := mustAccount(t, s, "org-owned"); a.HeldMicros != 0 || a.SpentMicros != 0 {
		t.Fatalf("undispatched charged %+v", a)
	}
}
