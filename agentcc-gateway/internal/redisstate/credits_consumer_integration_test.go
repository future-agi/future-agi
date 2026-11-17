package redisstate_test

import (
	"encoding/json"
	"fmt"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// A caller with no request context (a managed MCP tool call, a capability
// call) reserves, settles and voids by its own hold id and TTL, and writes
// the same outbox record shape as the credits plugin.
func TestStoreUsableOutsidePipeline(t *testing.T) {
	s, rdb, _ := redisstate.ManagedTestStoreForConsumer(t)
	const org = "org-o"
	redisstate.MustGrantForConsumer(t, s, org, 1_000_000, 1, 0)
	k := redisstate.ManagedKeysForConsumer(s, org)

	// The plugin path: a managed request reserved by the credits plugin and
	// settled with what the cost plugin billed.
	plugin := creditsplugin.New(true, nil)
	plugin.SetOrgLedger(creditsplugin.NewOrgLedger(s), true)
	plugin.SetPrices(func(string, string, string) (models.PriceCard, bool) {
		return models.PriceCard{Request: 0.002, Source: models.PriceSourceCatalog}, true
	})
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req-plugin"
	rc.Provider = "openrouter"
	rc.Request = &models.ChatCompletionRequest{Model: "vendor/model", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}}}
	rc.Metadata["billing_mode"] = models.BillingModeManaged
	rc.Metadata["org_id"] = org
	if res := plugin.ProcessRequest(redisstate.BackgroundForConsumer, rc); res.Error != nil {
		t.Fatalf("plugin reserve: %v", res.Error)
	}
	rc.Billing.BilledMicros, rc.Billing.UpstreamMicros = 2_100, 2_000
	plugin.ProcessResponse(redisstate.BackgroundForConsumer, rc)

	// Outside the pipeline: a tool call keyed by its own id, with its own TTL.
	held, err := s.Reserve(redisstate.BackgroundForConsumer, org, "call_123", 25_000, 30*time.Second, billing.ReserveNew)
	if err != nil || held.Status != billing.ReserveOK {
		t.Fatalf("reserve by hold id: %+v %v", held, err)
	}
	if ttl := rdb.ZScore(redisstate.BackgroundForConsumer, k.HoldExpiry, "call_123").Val(); ttl <= float64(time.Now().UnixMilli()) {
		t.Fatalf("hold expiry %v is not in the future", ttl)
	}
	tool := billing.Settlement{RequestID: "req-tool", OrgID: org, BilledMicros: 1_050, UpstreamMicros: 1_000,
		MarkupMicros: 50, MarkupBps: 500, HoldMicros: 25_000, Model: "fagi.search/web_search", Provider: "mcp",
		CostSource: "price_card", TsMs: time.Now().UnixMilli()}
	if res, err := s.Settle(redisstate.BackgroundForConsumer, org, "call_123", tool); err != nil || !res.Applied {
		t.Fatalf("settle by hold id: %+v %v", res, err)
	}
	if res, _ := s.Settle(redisstate.BackgroundForConsumer, org, "call_123", tool); res.Applied {
		t.Fatal("a second SETTLE of the same hold id wrote again")
	}
	s.Reserve(redisstate.BackgroundForConsumer, org, "cap_1.1", 5_000, time.Minute, billing.ReserveNew)
	if res, err := s.Void(redisstate.BackgroundForConsumer, org, "cap_1.1", billing.Settlement{RequestID: "cap_1", OrgID: org, Provider: "exa"}); err != nil || !res.Applied {
		t.Fatalf("void by hold id: %+v %v", res, err)
	}

	entries := rdb.XRange(redisstate.BackgroundForConsumer, k.Outbox, "-", "+").Val()
	if len(entries) != 3 {
		t.Fatalf("outbox has %d records, want plugin spend, tool spend and void", len(entries))
	}
	want := redisstate.SchemaFieldsForConsumer(t)
	for i, e := range entries {
		if got := redisstate.FieldNamesForConsumer(e.Values); fmt.Sprint(got) != fmt.Sprint(want) {
			t.Fatalf("record %d fields %v, want the managed-settlement-v1 fields %v", i, got, want)
		}
	}
	if entries[0].Values["request_id"] != "req-plugin" || entries[1].Values["provider"] != "mcp" || entries[2].Values["kind"] != "void" {
		t.Fatalf("records = %v", entries)
	}
	if a := redisstate.MustAccountForConsumer(t, s, org); a.SpentMicros != 2_100+1_050 || a.HeldMicros != 0 {
		t.Fatalf("account = %+v", a)
	}

	// A hold whose TTL has passed is swept by the next RESERVE.
	s.Reserve(redisstate.BackgroundForConsumer, org, "call_short", 100_000, 30*time.Millisecond, billing.ReserveNew)
	time.Sleep(80 * time.Millisecond)
	res, _ := s.Reserve(redisstate.BackgroundForConsumer, org, "call_next", 1, time.Minute, billing.ReserveNew)
	if res.Account.HeldMicros != 1 || rdb.HExists(redisstate.BackgroundForConsumer, k.Holds, "call_short").Val() {
		t.Fatalf("the expired hold was not swept: %+v", res.Account)
	}
}

// Every 402 managed_credits_exhausted is one the ledger agrees with.
// Requests run through the credits plugin until it refuses; at each refusal
// the ledger's balance (F minus the outbox spend the drain settles) is at or
// below zero for exhausted and below the request's hold for insufficient,
// and the refusal's available_usd is exactly that balance.
func TestWallet402ExhaustedMatchesTheLedger(t *testing.T) {
	s, _, _ := redisstate.ManagedTestStoreForConsumer(t)
	const org = "org-402"
	const granted = 250_000 // $0.25
	redisstate.MustGrantForConsumer(t, s, org, granted, 1, 0)
	plugin := creditsplugin.New(true, nil)
	plugin.SetOrgLedger(creditsplugin.NewOrgLedger(s), true)
	plugin.SetSettings(creditsplugin.DefaultSettings())
	plugin.SetPrices(redisstate.FixedFeePricesForConsumer) // every request holds 50000

	var refused *models.APIError
	for i := 0; i < 20 && refused == nil; i++ {
		rc := redisstate.ManagedRequestForConsumer(org, "req-402-"+strconv.Itoa(i))
		res := plugin.ProcessRequest(redisstate.BackgroundForConsumer, rc)
		if res.Error != nil {
			refused = res.Error
			ledgerBalance := granted - redisstate.OutboxBilledForConsumer(t, s, org)
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
		plugin.ProcessResponse(redisstate.BackgroundForConsumer, rc)
		rc.Release()
	}
	if refused == nil || refused.Code != "managed_credits_exhausted" {
		t.Fatalf("refusal = %+v, want managed_credits_exhausted once the $0.25 is spent", refused)
	}
	if spent := redisstate.OutboxBilledForConsumer(t, s, org); spent != granted {
		t.Fatalf("ledger spend %d, want the whole grant %d", spent, granted)
	}
}

// A refusal short of a hold is insufficient, and its available_usd is the
// ledger's remaining balance, not zero.
func TestWallet402InsufficientNamesTheLedgerBalance(t *testing.T) {
	s, _, _ := redisstate.ManagedTestStoreForConsumer(t)
	const org = "org-402i"
	redisstate.MustGrantForConsumer(t, s, org, 70_000, 1, 0)
	s.Reserve(redisstate.BackgroundForConsumer, org, "earlier", 40_000, time.Minute, billing.ReserveNew)
	s.Settle(redisstate.BackgroundForConsumer, org, "earlier", redisstate.SpendRecordForConsumer(org, "earlier", 40_000))
	plugin := creditsplugin.New(true, nil)
	plugin.SetOrgLedger(creditsplugin.NewOrgLedger(s), true)
	plugin.SetSettings(creditsplugin.DefaultSettings())
	plugin.SetPrices(redisstate.FixedFeePricesForConsumer) // the request needs a 50000 hold

	rc := redisstate.ManagedRequestForConsumer(org, "req-short")
	defer rc.Release()
	res := plugin.ProcessRequest(redisstate.BackgroundForConsumer, rc)
	if res.Error == nil || res.Error.Code != "managed_credits_insufficient" {
		t.Fatalf("result %+v, want managed_credits_insufficient", res.Error)
	}
	meta := res.Error.Metadata.(models.PaymentMeta)
	ledgerBalance := int64(70_000) - redisstate.OutboxBilledForConsumer(t, s, org)
	if *meta.AvailableUSD != billing.MicrosToUSD(ledgerBalance) || ledgerBalance != 30_000 {
		t.Fatalf("available_usd %v, ledger balance %d micros", *meta.AvailableUSD, ledgerBalance)
	}
}
