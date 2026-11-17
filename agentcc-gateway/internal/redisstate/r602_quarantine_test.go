package redisstate

import (
	"fmt"
	"github.com/futureagi/agentcc-gateway/internal/billing"
	"sync"
	"testing"
	"time"
)

func TestR602GlobalQuarantine(t *testing.T) {
	s, _, _ := managedTestStore(t)
	mustGrant(t, s, "org-owned", 10000, 1, 0)
	mustGrant(t, s, "org-other", 10000, 1, 0)
	model := "google/gemini-3.5-flash"
	if _, e := s.ReserveInclusive(bg, "org-owned", "req", "req.1", model, 100, time.Hour, billing.ReserveNew); e != nil {
		t.Fatal(e)
	}
	if e := s.BeginInclusive(bg, "org-owned", "req", "req.1"); e != nil {
		t.Fatal(e)
	}
	if _, e := s.ReserveInclusive(bg, "org-other", "earlier", "earlier.1", model, 100, time.Hour, billing.ReserveNew); e != nil {
		t.Fatal(e)
	}
	if _, e := s.Settle(bg, "org-owned", "req", ownedRecord("req", 105)); e != nil {
		t.Fatal(e)
	}
	restart := NewManagedCreditStore(s.client, s.prefix)
	if q, e := restart.InclusiveQuarantined(bg, "org-other", model); e != nil || !q {
		t.Fatalf("cross-org quarantine absent %t %v", q, e)
	}
	if e := restart.BeginInclusive(bg, "org-other", "earlier", "earlier.1"); e == nil {
		t.Error("pre-fence hold dispatched after quarantine")
	}
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			id := fmt.Sprintf("late-%d", i)
			if _, e := restart.ReserveInclusive(bg, "org-other", id, id+".1", model, 100, time.Hour, billing.ReserveNew); e == nil {
				t.Error("cross-org reservation after fence")
			}
		}(i)
	}
	wg.Wait()
	if a := mustAccount(t, s, "org-other"); a.HeldMicros != 100 {
		t.Fatalf("rejected reservations leaked holds %+v", a)
	}
}
