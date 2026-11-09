package redisstate

import (
	"encoding/json"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
)

// The wallet invariants of PLAN section 4.4 on the gateway side (GW-WALLET):
// I6 (the gateway holds the granted value Django pushed, clamped at 0, and
// its spent never falls below the settled floor), I11 (available is granted
// minus spent minus held) and the G2a rule that no 402
// managed_credits_exhausted is one the ledger disputes. "The ledger" here is
// what the settlement drain writes from the outbox: one spend entry per
// spend:<request_id> (ee/usage/tests/test_managed_settlement.py proves that
// half), so ledger spend = the sum of the outbox's billed_micros.

// outboxBilled sums billed_micros over the org's kind=spend outbox records,
// which the drain settles into the ledger exactly once each.
func outboxBilled(t *testing.T, s *ManagedCreditStore, org string) int64 {
	t.Helper()
	var total int64
	for _, e := range s.client.Redis().XRange(bg, s.keys(org).outbox, "-", "+").Val() {
		if e.Values["kind"] != "spend" {
			continue
		}
		n, err := strconv.ParseInt(e.Values["billed_micros"].(string), 10, 64)
		if err != nil {
			t.Fatalf("outbox billed_micros %v: %v", e.Values["billed_micros"], err)
		}
		total += n
	}
	return total
}

// I6: granted is exactly the pushed value; a later push never lowers spent,
// a higher floor raises it, and a push of the clamped 0 (Django's value for
// credits below zero) refuses every managed request.
func TestWalletI6GrantedIsThePushAndSpentNeverBelowTheFloor(t *testing.T) {
	s, _, _ := managedTestStore(t)
	const org = "org-i6"
	mustGrant(t, s, org, 1_000_000, 1, 0)
	s.Reserve(bg, org, "r1", 300_000, time.Minute, billing.ReserveNew)
	s.Settle(bg, org, "r1", spendRecord(org, "r1", 300_000))

	res := mustGrant(t, s, org, 1_200_000, 2, 200_000)
	if res.Account.GrantedMicros != 1_200_000 || res.Account.SpentMicros != 300_000 {
		t.Fatalf("push with a floor below spent: %+v, want granted 1200000 and spent kept at 300000", res.Account)
	}
	res = mustGrant(t, s, org, 1_200_000, 3, 500_000)
	if res.Account.SpentMicros != 500_000 {
		t.Fatalf("push with a floor above spent: %+v, want spent raised to the floor 500000", res.Account)
	}
	if spent := mustAccount(t, s, org).SpentMicros; spent < 500_000 {
		t.Fatalf("spent %d fell below the settled floor 500000", spent)
	}

	res = mustGrant(t, s, org, 0, 4, 500_000)
	if res.Account.GrantedMicros != 0 || res.Account.AvailableMicros() > 0 {
		t.Fatalf("clamped push of 0: %+v, want nothing available", res.Account)
	}
	if r, _ := s.Reserve(bg, org, "r2", 1, time.Minute, billing.ReserveNew); r.Status != billing.ReserveExhausted {
		t.Fatalf("reserve after the clamped push: %v, want exhausted", r.Status)
	}
}

// I11: with credits F, settled spend S and an open hold H, the gateway's
// available is F - S - H, and S is exactly what the drain will settle into
// the ledger. Past an overshoot it is negative, never clamped.
func TestWalletI11AvailableIsGrantedMinusSpentMinusHeld(t *testing.T) {
	s, _, _ := managedTestStore(t)
	const org = "org-i11"
	const granted = 2_000_000
	mustGrant(t, s, org, granted, 1, 0)
	for i, billed := range []int64{120_000, 333_333, 46_667} {
		id := "req-" + strconv.Itoa(i)
		s.Reserve(bg, org, id, 400_000, time.Minute, billing.ReserveNew)
		s.Settle(bg, org, id, spendRecord(org, id, billed))
	}
	s.Reserve(bg, org, "open", 250_000, time.Minute, billing.ReserveNew)

	a := mustAccount(t, s, org)
	ledgerSpend := outboxBilled(t, s, org)
	if ledgerSpend != 500_000 || a.SpentMicros != ledgerSpend {
		t.Fatalf("gateway spent %d, outbox %d, want both 500000", a.SpentMicros, ledgerSpend)
	}
	if got, want := a.AvailableMicros(), int64(granted-500_000-250_000); got != want {
		t.Fatalf("available = %d, want F - S - H = %d", got, want)
	}

	// An overshoot (settled above the hold) leaves available negative, and
	// the ledger's F - S says the same.
	s.Settle(bg, org, "open", spendRecord(org, "open", 1_600_000))
	a = mustAccount(t, s, org)
	ledgerSpend = outboxBilled(t, s, org)
	if a.AvailableMicros() != granted-ledgerSpend || a.AvailableMicros() >= 0 {
		t.Fatalf("after the overshoot: available %d, ledger F - S %d, want equal and negative",
			a.AvailableMicros(), granted-ledgerSpend)
	}
}

// fixedFeeModel is a model the catalogue prices at a fixed fee per request
// only. One Future AGI key serves only a model the catalogue prices: the
// credits plugin refuses an unpriced one with a 403 before any hold. With the
// 5% markup every request of this model holds exactly 50000 micros ($0.05).
const fixedFeeModel = "vendor/fixed-fee"

func fixedFeePrices(providerID, model, _ string) (models.PriceCard, bool) {
	if providerID == "openrouter" && model == fixedFeeModel {
		return models.PriceCard{Request: 0.05 / 1.05}, true
	}
	return models.PriceCard{}, false
}

// managedRequest is a managed chat request of org through the credits
// plugin, held at fixedFeeModel's price.
func managedRequest(org, id string) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.RequestID = id
	rc.Provider = "openrouter"
	rc.Request = &models.ChatCompletionRequest{Model: fixedFeeModel,
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}}}
	rc.Metadata["billing_mode"] = models.BillingModeManaged
	rc.Metadata["org_id"] = org
	return rc
}

// G2a: every 402 managed_credits_exhausted is one the ledger agrees with.
// Requests run through the credits plugin until it refuses; at each refusal
// the ledger's balance (F minus the outbox spend the drain settles) is at or
// below zero for exhausted and below the request's hold for insufficient,
// and the refusal's available_usd is exactly that balance.
func TestWallet402ExhaustedMatchesTheLedger(t *testing.T) {
	s, _, _ := managedTestStore(t)
	const org = "org-402"
	const granted = 250_000 // $0.25
	mustGrant(t, s, org, granted, 1, 0)
	plugin := creditsplugin.New(true, nil)
	plugin.SetOrgLedger(creditsplugin.NewOrgLedger(s), true)
	plugin.SetSettings(creditsplugin.DefaultSettings())
	plugin.SetPrices(fixedFeePrices) // every request holds 50000

	var refused *models.APIError
	for i := 0; i < 20 && refused == nil; i++ {
		rc := managedRequest(org, "req-402-"+strconv.Itoa(i))
		res := plugin.ProcessRequest(bg, rc)
		if res.Error != nil {
			refused = res.Error
			ledgerBalance := granted - outboxBilled(t, s, org)
			meta, ok := refused.Metadata.(models.PaymentMeta)
			if !ok || meta.AvailableUSD == nil {
				t.Fatalf("refusal %+v carries no available_usd", refused)
			}
			if want := billing.MicrosToUSD(max(ledgerBalance, 0)); *meta.AvailableUSD != want {
				t.Fatalf("402 available_usd %v, the ledger's balance is %v", *meta.AvailableUSD, want)
			}
			switch refused.Code {
			case "managed_credits_exhausted":
				if ledgerBalance > 0 {
					t.Fatalf("402 exhausted while the ledger holds %d micros", ledgerBalance)
				}
			case "managed_credits_insufficient":
				if ledgerBalance >= 50_000 {
					t.Fatalf("402 insufficient while the ledger covers the hold (%d micros)", ledgerBalance)
				}
			default:
				t.Fatalf("refusal code %q", refused.Code)
			}
			rc.Release()
			break
		}
		if rc.Billing.HoldMicros != 50_000 {
			t.Fatalf("request %d held %d micros, want 50000", i, rc.Billing.HoldMicros)
		}
		// Settled at exactly what was held: the balance falls 50000 a request.
		rc.Billing.BilledMicros, rc.Billing.UpstreamMicros = 50_000, 47_619
		plugin.ProcessResponse(bg, rc)
		rc.Release()
	}
	if refused == nil || refused.Code != "managed_credits_exhausted" {
		t.Fatalf("refusal = %+v, want managed_credits_exhausted once the $0.25 is spent", refused)
	}
	if spent := outboxBilled(t, s, org); spent != granted {
		t.Fatalf("ledger spend %d, want the whole grant %d", spent, granted)
	}
}

// A refusal short of a hold is insufficient, and its available_usd is the
// ledger's remaining balance, not zero.
func TestWallet402InsufficientNamesTheLedgerBalance(t *testing.T) {
	s, _, _ := managedTestStore(t)
	const org = "org-402i"
	mustGrant(t, s, org, 70_000, 1, 0)
	s.Reserve(bg, org, "earlier", 40_000, time.Minute, billing.ReserveNew)
	s.Settle(bg, org, "earlier", spendRecord(org, "earlier", 40_000))
	plugin := creditsplugin.New(true, nil)
	plugin.SetOrgLedger(creditsplugin.NewOrgLedger(s), true)
	plugin.SetSettings(creditsplugin.DefaultSettings())
	plugin.SetPrices(fixedFeePrices) // the request needs a 50000 hold

	rc := managedRequest(org, "req-short")
	defer rc.Release()
	res := plugin.ProcessRequest(bg, rc)
	if res.Error == nil || res.Error.Code != "managed_credits_insufficient" {
		t.Fatalf("result %+v, want managed_credits_insufficient", res.Error)
	}
	meta := res.Error.Metadata.(models.PaymentMeta)
	ledgerBalance := int64(70_000) - outboxBilled(t, s, org)
	if *meta.AvailableUSD != billing.MicrosToUSD(ledgerBalance) || ledgerBalance != 30_000 {
		t.Fatalf("available_usd %v, ledger balance %d micros", *meta.AvailableUSD, ledgerBalance)
	}
}
