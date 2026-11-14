package budget

import (
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// fakeLedger is a GlobalBudgetBackend whose reads and writes can fail, lose
// their reply, or block until released.
type fakeLedger struct {
	mu           sync.Mutex
	readOK       bool
	apply, ack   bool
	total        float64
	models       map[string]float64
	getBlock     chan struct{}
	recordBlock  chan struct{}
	gets, writes int
}

func (f *fakeLedger) GetSpend(org, level, key, period string) (float64, map[string]float64, bool) {
	f.mu.Lock()
	f.gets++
	block := f.getBlock
	f.mu.Unlock()
	if block != nil {
		<-block
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if !f.readOK {
		return 0, nil, false
	}
	m := make(map[string]float64, len(f.models))
	for k, v := range f.models {
		m[k] = v
	}
	return f.total, m, true
}

func (f *fakeLedger) RecordSpend(org, level, key, period, model string, cost float64) (float64, bool) {
	f.mu.Lock()
	f.writes++
	block := f.recordBlock
	f.mu.Unlock()
	if block != nil {
		<-block
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.apply {
		f.total += cost
		if f.models == nil {
			f.models = map[string]float64{}
		}
		f.models[model] += cost
	}
	return f.total, f.ack
}

func (f *fakeLedger) counts() (gets, writes int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.gets, f.writes
}

func ledgerTracker(l *fakeLedger) *Tracker {
	t := NewTracker(config.BudgetsConfig{Enabled: true, DefaultPeriod: "total", Org: &config.BudgetLevelConfig{Limit: 10}})
	t.SetGlobalBudgetBackend(l)
	return t
}

func waitFor(t *testing.T, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatal("condition not reached")
		}
		time.Sleep(5 * time.Millisecond)
	}
}

// TestReviewOrdinaryCheckFallsBackWhenLedgerUnreadable: an unreadable shared
// ledger must not refuse ordinary requests as if the budget were spent. The
// replica's own counter still enforces the ceiling.
func TestReviewOrdinaryCheckFallsBackWhenLedgerUnreadable(t *testing.T) {
	l := &fakeLedger{}
	tr := ledgerTracker(l)
	if r := tr.CheckBudget("", "", "", "gpt-4o", nil); !r.Allowed {
		t.Fatalf("ledger outage refused an ordinary request: %+v", r)
	}
	tr.RecordSpend("", "", "", "gpt-4o", nil, 11)
	r := tr.CheckBudget("", "", "", "gpt-4o", nil)
	if r.Allowed || r.BlockedBy != "org:org" {
		t.Fatalf("local counter did not enforce the ceiling during the outage: %+v", r)
	}
	if got, _ := tr.GetSpend("org", ""); got != 11 {
		t.Fatalf("fallback spend=%v want 11", got)
	}
}

// TestReviewLedgerIOOutsideLevelLock: a slow ledger call must not hold the
// level lock that every other request on that level needs.
func TestReviewLedgerIOOutsideLevelLock(t *testing.T) {
	t.Run("read does not block record", func(t *testing.T) {
		release := make(chan struct{})
		l := &fakeLedger{readOK: true, apply: true, ack: true, getBlock: release}
		tr := ledgerTracker(l)
		defer close(release)
		go tr.CheckBudget("", "", "", "gpt-4o", nil)
		waitFor(t, func() bool { g, _ := l.counts(); return g == 1 })
		done := make(chan struct{})
		go func() { tr.RecordSpend("", "", "", "gpt-4o", nil, 1); close(done) }()
		select {
		case <-done:
		case <-time.After(2 * time.Second):
			t.Error("RecordSpend waited for another request's ledger read")
		}
	})
	t.Run("record does not block read", func(t *testing.T) {
		release := make(chan struct{})
		l := &fakeLedger{readOK: true, apply: true, ack: true, recordBlock: release}
		tr := ledgerTracker(l)
		defer close(release)
		go tr.RecordSpend("", "", "", "gpt-4o", nil, 1)
		waitFor(t, func() bool { _, w := l.counts(); return w == 1 })
		done := make(chan struct{})
		go func() { tr.CheckBudget("", "", "", "gpt-4o", nil); close(done) }()
		select {
		case <-done:
		case <-time.After(2 * time.Second):
			t.Error("CheckBudget waited for another request's ledger write")
		}
	})
}

// TestReviewLostLedgerReplyCountedOnce: a write the ledger applied but whose
// reply was lost must not be counted a second time from the local counter.
func TestReviewLostLedgerReplyCountedOnce(t *testing.T) {
	l := &fakeLedger{readOK: true, apply: true, ack: false}
	tr := ledgerTracker(l)
	tr.RecordSpend("", "", "", "gpt-4o", nil, 2)
	if got, _ := tr.GetSpend("org", ""); got != 2 {
		t.Fatalf("spend=%v want 2", got)
	}
	if r := tr.CheckBudget("", "", "", "gpt-4o", nil); r.Remaining != 8 {
		t.Fatalf("remaining=%v want 8", r.Remaining)
	}
}

// TestReviewVideoScopesUseConfiguredCeiling: the durable reservation compares
// the shared total (which already holds this replica's ordinary spend) with
// the scope limit, so the scope must carry the configured ceiling.
func TestReviewVideoScopesUseConfiguredCeiling(t *testing.T) {
	l := &fakeLedger{readOK: true, apply: true, ack: true}
	tr := ledgerTracker(l)
	tr.RecordSpend("", "", "", "video/model", nil, 3)
	scopes := tr.VideoScopes("", "", "", "video/model", nil)
	if len(scopes) != 1 || scopes[0].Limit != 10 {
		t.Fatalf("scopes=%+v", scopes)
	}
}
