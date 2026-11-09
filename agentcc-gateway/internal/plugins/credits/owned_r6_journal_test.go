package credits

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func ownedR6Journal(p *Plugin, a models.Attempt, hold int64) (billing.OwnedAttempt, billing.Settlement) {
	rc := &models.RequestContext{RequestID: "r6", Metadata: map[string]string{}}
	rc.Billing.OwnedRequest = true
	a.AttemptID, a.Model, a.Provider, a.CredentialType = "r6.1", "synthetic/token", "openrouter", models.CredentialManaged
	a.Pricing = models.NewAttemptPricing(&models.BillingState{AttemptHoldMicros: hold, ReservePrice: &models.PriceCard{Prompt: 1e-6, Source: models.PriceSourceCatalog}})
	rc.AddAttempt(a)
	rec := p.journalOf(rc)
	owned := billing.OwnedRequest{Attempts: rec.ManagedJournal, Record: rec}
	return rec.ManagedJournal["r6.1"], owned.RecoveryRecord("org-m", "r6", time.Now())
}

// A definitive upstream rejection without usage, cost, generation identity or
// output is a known zero liability: recovery releases its hold. Everything else
// that carries no evidence remains an unknown full-hold debt.
func TestOwnedR6JournalCleanRejectionIsKnownZero(t *testing.T) {
	p := New(true, nil)
	rejected := models.Attempt{ProviderRejected: true, Outcome: models.OutcomeError, ErrorClass: models.ErrClassUpstream5xx, ErrorOrigin: models.OriginUpstream, StatusCode: 502}
	zeroCounts := rejected
	zeroCounts.Usage = &models.Usage{}
	for name, a := range map[string]models.Attempt{
		"5xx":          rejected,
		"rate-limited": {ProviderRejected: true, Outcome: models.OutcomeError, ErrorClass: models.ErrClassRateLimited, ErrorOrigin: models.OriginUpstream, StatusCode: 429},
		"refused":      {ProviderRejected: true, Outcome: models.OutcomeError, ErrorClass: models.ErrClassInvalidRequest, ErrorOrigin: models.OriginUpstream, StatusCode: 400},
		"zero-counts":  zeroCounts,
		"null-cost":    {ProviderRejected: true, Usage: &models.Usage{CostDecimal: "null"}},
	} {
		t.Run(name, func(t *testing.T) {
			j, rec := ownedR6Journal(p, a, 41580)
			if j.Summary == nil || j.Summary.BilledMicros != 0 || j.Summary.UpstreamMicros != 0 || j.Anomaly != "" || rec.BilledMicros != 0 {
				t.Fatalf("clean rejection charged: journal=%+v recovery=%d", j.Summary, rec.BilledMicros)
			}
		})
	}
	withID := rejected
	withID.Generation.ID = "gen-started"
	withTokens := rejected
	withTokens.Usage = &models.Usage{PromptTokens: 5}
	withTokens.PromptTokens = 5
	for name, a := range map[string]models.Attempt{
		// The attempt as beginAttempt records it, before (or without) its finish.
		"in-flight":  {Outcome: models.OutcomeError, ErrorClass: models.ErrClassUnknown, ErrorOrigin: models.OriginGateway},
		"timeout":    {Outcome: models.OutcomeTimeout, ErrorClass: models.ErrClassTimeout, ErrorOrigin: models.OriginGateway, StatusCode: 504},
		"cancelled":  {Outcome: models.OutcomeCancelled, ErrorClass: models.ErrClassCancelled, ErrorOrigin: models.OriginClient, StatusCode: 499},
		"network":    {Outcome: models.OutcomeError, ErrorClass: models.ErrClassNetwork, ErrorOrigin: models.OriginUpstream, StatusCode: 502},
		"accepted":   {Outcome: models.OutcomeError, ErrorClass: models.ErrClassUnknown, ErrorOrigin: models.OriginUpstream, StatusCode: 200},
		"gateway":    {Outcome: models.OutcomeError, ErrorClass: models.ErrClassUpstream5xx, ErrorOrigin: models.OriginGateway, StatusCode: 502},
		"generation": withID,
	} {
		t.Run("unknown/"+name, func(t *testing.T) {
			j, rec := ownedR6Journal(p, a, 41580)
			if j.Summary == nil || j.Summary.BilledMicros != 41580 || rec.BilledMicros != 41580 {
				t.Fatalf("unknown outcome released: journal=%+v recovery=%d", j.Summary, rec.BilledMicros)
			}
		})
	}
	// Reported usage is evidence and is priced, never released as clean.
	if j, rec := ownedR6Journal(p, withTokens, 41580); j.Summary == nil || j.Summary.BilledMicros != 5 || rec.BilledMicros != 5 {
		t.Fatalf("counted tokens lost: journal=%+v recovery=%d", j.Summary, rec.BilledMicros)
	}
}

// Invalid preserved reports cannot borrow a native float. Valid JSON decimals
// are priced exactly; contested interim totals resolve to the full hold.
func TestOwnedR6JournalPricesValidCostWithRejectedLexeme(t *testing.T) {
	p := New(true, nil)
	cost := 0.00005
	for _, lexeme := range []string{`null`, `"0.00005"`, `-1`, `true`, `5e-0005`} {
		t.Run(lexeme, func(t *testing.T) {
			u := &models.Usage{PromptTokens: 1, CompletionTokens: 1, Cost: &cost}
			u.CostDecimal = lexeme
			j, rec := ownedR6Journal(p, models.Attempt{Outcome: models.OutcomeError, Usage: u}, 41580)
			want := int64(41580)
			if lexeme == "5e-0005" {
				want = 53
			}
			if j.Summary == nil || j.Summary.BilledMicros != want || rec.BilledMicros != want {
				t.Fatalf("valid total unpriced: journal=%+v recovery=%d", j.Summary, rec.BilledMicros)
			}
		})
	}
	// Merged from the wire, the earlier exact lexeme is kept with its value:
	// 9.4999... upstream micros stays 9, not the float's 9.5 (10, billed 11).
	var first, later models.Usage
	if json.Unmarshal([]byte(`{"prompt_tokens":1,"completion_tokens":1,"cost":0.00000949999999999999999}`), &first) != nil || json.Unmarshal([]byte(`{"cost":null}`), &later) != nil {
		t.Fatal("fixture")
	}
	j, _ := ownedR6Journal(p, models.Attempt{Outcome: models.OutcomeSuccess, Usage: models.MergeUsage(&first, &later)}, 41580)
	if j.Summary == nil || j.Summary.BilledMicros != 41580 {
		t.Fatalf("exact earlier total lost: %+v", j.Summary)
	}
}
