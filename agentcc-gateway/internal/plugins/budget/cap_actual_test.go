package budget

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestCapActualRequiresAuthoritativeUsage(t *testing.T) {
	for _, tc := range []struct {
		name                                  string
		dispatched, cache, estimated, metered bool
		wantKnown                             bool
	}{
		{"pre-dispatch", false, false, false, false, true},
		{"cache", true, true, false, false, true},
		{"unknown", true, false, false, false, false},
		{"estimated", true, false, true, true, false},
		{"reported-zero", true, false, false, true, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rc := models.AcquireRequestContext()
			defer rc.Release()
			a := &Admission{}
			a.attempted.Store(tc.dispatched)
			rc.Flags.CacheHit = tc.cache
			rc.Billing.UsageEstimated = tc.estimated
			if tc.metered {
				rc.Response = &models.ChatCompletionResponse{Usage: models.MeasuredUsage(0, 0)}
			}
			cost, known := a.ActualSpend(rc)
			if known != tc.wantKnown || cost != 0 {
				t.Fatalf("cost=%d known=%v", cost, known)
			}
		})
	}
}

func TestCapLateActualResolvesUnknownRequest(t *testing.T) {
	p := capMoneyPlugin(t)
	rc := capMoneyRequest(t, "org-a", "key-a", false)
	if err := p.reserveCaps(context.Background(), rc); err != nil {
		t.Fatal(err)
	}
	if !RequestAdmission(rc).ClaimAttempt() {
		t.Fatal("attempt refused")
	}
	p.finishCaps(rc)
	next := capMoneyRequest(t, "org-a", "key-b", false)
	if err := p.reserveCaps(context.Background(), next); err == nil || err.Code != "budget_state_unavailable" {
		t.Fatalf("unknown not fenced: %v", err)
	}
	rc.Response = &models.ChatCompletionResponse{Usage: models.MeasuredUsage(1, 0)}
	rc.Billing.UpstreamMicros = 1000
	p.finishCaps(rc)
	after := capMoneyRequest(t, "org-a", "key-b", false)
	if err := p.reserveCaps(context.Background(), after); err != nil {
		t.Fatalf("late actual did not resolve hold: %v", err)
	}
}
