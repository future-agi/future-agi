package budget

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func capBusyAttempt() models.Attempt {
	return models.Attempt{
		CredentialType: models.CredentialBYOK,
		Outcome:        models.OutcomeError,
		ErrorClass:     models.ErrClassUpstream5xx,
		ErrorOrigin:    models.OriginGateway,
		StatusCode:     http.StatusServiceUnavailable,
	}
}

func TestCapActualGatewayBusyIsKnownZero(t *testing.T) {
	for _, credential := range []models.CredentialType{models.CredentialBYOK, models.CredentialManaged} {
		for _, skippedSuffix := range []bool{false, true} {
			t.Run(string(credential)+map[bool]string{false: "", true: "/skipped-fallback"}[skippedSuffix], func(t *testing.T) {
				rc := models.AcquireRequestContext()
				defer rc.Release()
				a := &Admission{managed: credential == models.CredentialManaged}
				a.attempted.Store(true)
				attempt := capBusyAttempt()
				attempt.CredentialType = credential
				// This is the typed, server-owned signal set by attemptRun.finish
				// only after models.IsProviderBusy confirms non-dispatch.
				rc.Billing.BusyCredential = credential
				rc.AddAttempt(attempt)
				if skippedSuffix {
					rc.AddAttempt(models.Attempt{Outcome: models.OutcomeSkipped})
				}
				if cost, known := a.ActualSpend(rc); cost != 0 || !known {
					t.Fatalf("non-dispatched provider_busy must release cap holds: cost=%d known=%v", cost, known)
				}
			})
		}
	}
}

func TestCapActualBusyDoesNotEraseOtherSpend(t *testing.T) {
	for _, managed := range []bool{false, true} {
		rc := models.AcquireRequestContext()
		a := &Admission{managed: managed}
		a.attempted.Store(true)
		rc.AddAttempt(models.Attempt{Outcome: models.OutcomeSuccess, Usage: models.MeasuredUsage(1, 1),
			UpstreamCostMicros: 100, BilledCostMicros: 105})
		rc.AddAttempt(capBusyAttempt())
		rc.Billing.BusyCredential = models.CredentialBYOK
		want := int64(100)
		if managed {
			want = 105
		}
		cost, known := a.ActualSpend(rc)
		rc.Release()
		if !known || cost != want {
			t.Fatalf("managed=%v: prior measured spend lost: cost=%d known=%v want=%d", managed, cost, known, want)
		}
	}
}

func TestCapActualBusyStillFencesAmbiguousDispatch(t *testing.T) {
	for _, tc := range []struct {
		name   string
		mutate func(*models.RequestContext, *models.Attempt)
	}{
		{"no-server-signal", func(rc *models.RequestContext, _ *models.Attempt) { rc.Billing.BusyCredential = "" }},
		{"wrong-credential", func(rc *models.RequestContext, _ *models.Attempt) {
			rc.Billing.BusyCredential = models.CredentialManaged
		}},
		{"unknown-credential", func(rc *models.RequestContext, a *models.Attempt) {
			rc.Billing.BusyCredential = "unknown"
			a.CredentialType = "unknown"
		}},
		{"message-only", func(rc *models.RequestContext, a *models.Attempt) {
			rc.Billing.BusyCredential = ""
			a.ErrorMessage = models.ErrProviderBusy("synthetic").Message
			rc.AddError(models.ErrProviderBusy("synthetic"))
		}},
		{"upstream-refusal", func(_ *models.RequestContext, a *models.Attempt) {
			a.ErrorOrigin = models.OriginUpstream
			a.ProviderRejected = true
		}},
		{"gateway-timeout", func(_ *models.RequestContext, a *models.Attempt) {
			a.Outcome = models.OutcomeTimeout
			a.ErrorClass = models.ErrClassTimeout
		}},
		{"gateway-network-error", func(_ *models.RequestContext, a *models.Attempt) { a.ErrorClass = models.ErrClassNetwork }},
		{"in-flight", func(_ *models.RequestContext, a *models.Attempt) { a.InFlight = true }},
		{"partial-usage", func(_ *models.RequestContext, a *models.Attempt) { a.Usage = &models.Usage{PromptTokens: 1} }},
		{"estimated-usage", func(_ *models.RequestContext, a *models.Attempt) { a.UsageEstimated = true }},
		{"charge-evidence", func(_ *models.RequestContext, a *models.Attempt) { a.UpstreamCostMicros = 10 }},
		{"billable", func(_ *models.RequestContext, a *models.Attempt) { a.Billable = true }},
		{"token-evidence", func(_ *models.RequestContext, a *models.Attempt) { a.PromptTokens = 1 }},
		{"prior-unknown-attempt", func(rc *models.RequestContext, _ *models.Attempt) {
			rc.AddAttempt(models.Attempt{Outcome: models.OutcomeError})
		}},
		{"prior-busy-not-proven", func(rc *models.RequestContext, _ *models.Attempt) { rc.AddAttempt(capBusyAttempt()) }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rc := models.AcquireRequestContext()
			defer rc.Release()
			a := &Admission{}
			a.attempted.Store(true)
			rc.Billing.BusyCredential = models.CredentialBYOK
			attempt := capBusyAttempt()
			tc.mutate(rc, &attempt)
			rc.AddAttempt(attempt)
			if cost, known := a.ActualSpend(rc); known {
				t.Fatalf("ambiguous dispatch must remain fenced: cost=%d known=%v", cost, known)
			}
		})
	}
}
