package budget

import (
	"context"
	"sync"
	"testing"

	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// outageLedger is a Redis budget backend that can be up, down (breaker open),
// or up but failing reads.
type outageLedger struct {
	mu        sync.Mutex
	available bool
	readOK    bool
	total     float64
	gets      int
}

func (l *outageLedger) RecordSpend(org, level, key, period, model string, cost float64) (float64, bool) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if !l.available {
		return -1, false
	}
	l.total += cost
	return l.total, true
}

func (l *outageLedger) GetSpend(org, level, key, period string) (float64, map[string]float64, bool) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.gets++
	if !l.available || !l.readOK {
		return 0, nil, false
	}
	return l.total, nil, true
}

func (l *outageLedger) Available() bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.available
}

func outageRequest(endpoint string) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.EndpointType = endpoint
	rc.Model = "gpt-4o"
	return rc
}

func globalTracker() *budgetpkg.Tracker {
	return budgetpkg.NewTracker(config.BudgetsConfig{Enabled: true, DefaultPeriod: "total", Org: &config.BudgetLevelConfig{Limit: 10}})
}

// TestReviewLedgerOutageNeverReportedAsBudgetExceeded: with the global levels
// on the shared ledger (a video-enabled gateway), a Redis outage must not
// refuse chat as budget_exceeded, and video must get 503, not 429.
func TestReviewLedgerOutageNeverReportedAsBudgetExceeded(t *testing.T) {
	for _, tc := range []struct {
		name              string
		available, readOK bool
	}{{"breaker open", false, false}, {"read fails", true, false}} {
		t.Run(tc.name, func(t *testing.T) {
			ledger := &outageLedger{available: tc.available, readOK: tc.readOK}
			tracker := globalTracker()
			p := New(tracker, nil, true, nil)
			p.SetRedisBudget(ledger)
			tracker.SetGlobalBudgetBackend(ledger)

			chat := outageRequest("chat")
			defer chat.Release()
			if r := p.ProcessRequest(context.Background(), chat); r.Error != nil {
				t.Errorf("chat refused during ledger outage: %d %s %s", r.Error.Status, r.Error.Code, r.Error.Message)
			}

			vid := outageRequest("video")
			defer vid.Release()
			r := p.ProcessRequest(context.Background(), vid)
			if r.Error == nil || r.Error.Status != 503 || r.Error.Code != "budget_store_unavailable" {
				t.Errorf("video during ledger outage: %+v", r.Error)
			}
		})
	}
}

// TestReviewChatOverLocalCeilingStillRefusedDuringOutage: the fallback is the
// replica's own counter, so a ceiling it has already passed still applies.
func TestReviewChatOverLocalCeilingStillRefusedDuringOutage(t *testing.T) {
	ledger := &outageLedger{}
	tracker := globalTracker()
	p := New(tracker, nil, true, nil)
	p.SetRedisBudget(ledger)
	tracker.SetGlobalBudgetBackend(ledger)
	tracker.RecordSpend("", "", "", "gpt-4o", nil, 12)
	chat := outageRequest("chat")
	defer chat.Release()
	r := p.ProcessRequest(context.Background(), chat)
	if r.Error == nil || r.Error.Status != 429 || r.Error.Code != "budget_exceeded" || chat.Metadata["budget_blocked_by"] != "org:org" {
		t.Fatalf("local ceiling not enforced: %+v %q", r.Error, chat.Metadata["budget_blocked_by"])
	}
}

// TestReviewSetRedisBudgetKeepsGlobalLevelsLocal: attaching Redis for tenant
// budgets alone (video off) must leave config-file global levels pod-local,
// exactly as before video existed: no Redis read on the chat path.
func TestReviewSetRedisBudgetKeepsGlobalLevelsLocal(t *testing.T) {
	ledger := &outageLedger{}
	p := New(globalTracker(), nil, true, nil)
	p.SetRedisBudget(ledger)
	chat := outageRequest("chat")
	defer chat.Release()
	if r := p.ProcessRequest(context.Background(), chat); r.Error != nil {
		t.Fatalf("chat refused: %+v", r.Error)
	}
	ledger.mu.Lock()
	defer ledger.mu.Unlock()
	if ledger.gets != 0 {
		t.Fatalf("global check read Redis %d times with video off", ledger.gets)
	}
}
