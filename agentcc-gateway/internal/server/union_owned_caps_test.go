package server

import (
	"context"
	"fmt"
	"net/http"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
)

func TestUnionStrictOwnedSingleFinancialIdentity(t *testing.T) {
	g, _, _ := capComposedGateway(t)
	cfg := config.DefaultConfig()
	cfg.Managed.CatalogueFamilies = []string{"cat2"}
	if err := g.srv.handlers.catalog.Configure(catalog.OptionsFromConfig(cfg, []catalog.Source{{ProviderID: "openrouter", Type: catalog.SourceOpenRouter}})); err != nil {
		t.Fatal(err)
	}
	all, install := installBundledCatalogue(t)
	// Keep the inclusive catalogue profile (and therefore owned accounting),
	// but dispatch an explicit synthetic scalar fixture, not a bundled model
	// with a web-search or inclusive dimension that strict caps must reject.
	const model = "example/union-strict-scalar"
	all[model] = &catalog.Model{
		ID: model, ContextLength: 8192, MaxOutputTokens: 1024,
		InputModalities: []string{"text"}, OutputModalities: []string{"text"},
		Prices: catalog.Prices{Prompt: 5e-6, Completion: 25e-6},
	}
	install(t, g)
	cat := g.srv.handlers.catalog.Load()
	if !cat.OwnsManagedAttempts() {
		t.Fatal("strict scalar fixture must retain owned accounting")
	}
	card, ok := cat.PriceCard("openrouter", model, "")
	want := models.PriceCard{Prompt: 5e-6, Completion: 25e-6, Source: models.PriceSourceCatalog, SnapshotID: cat.ID()}
	if !ok || !reflect.DeepEqual(card, want) {
		t.Fatalf("strict fixture must have only the declared scalar rates: %+v", card)
	}
	owner := g.ledger.Ledger().(*billing.MemoryLedger)
	var requestID string
	var attempts []models.Attempt
	var checkedSecond bool
	g.srv.handlers.hookObserver = func(hook, _ string, rc *models.RequestContext) {
		if hook == hookBeginAttempt && requestID == "" {
			requestID = rc.RequestID
			admission := capbudget.RequestAdmission(rc)
			if admission == nil || !rc.Billing.OwnedRequired || !rc.Billing.OwnedRequest {
				t.Fatal("strict admission and owned reservation must both be active")
			}
			if quote, ready := admission.QualifiedQuote(); !ready || quote < 7350 {
				t.Fatalf("scalar fixture must qualify a hold covering the reported charge: quote=%d ready=%t", quote, ready)
			}
			rc.AddFinalizer(func() { attempts = rc.AttemptsSnapshot() })
		}
		if hook != hookFinishAttempt || checkedSecond {
			return
		}
		checkedSecond = true
		before, err := owner.ReadOwnedJournal(context.Background(), "org-m", rc.RequestID)
		if err != nil {
			t.Error(err)
		}
		money := g.account(t, "org-m")
		_, _, refusal := g.srv.handlers.startAttempt(context.Background(), rc, models.AttemptRetry, "union-repeat", rc.Provider, rc.Request.Model, models.CredentialManaged)
		if refusal != models.ReasonAttemptBudget {
			t.Errorf("strict second attempt escaped: %q", refusal)
		}
		after, err := owner.ReadOwnedJournal(context.Background(), "org-m", rc.RequestID)
		if err != nil || !reflect.DeepEqual(before, after) || money != g.account(t, "org-m") {
			t.Errorf("second attempt changed journal/reservation: error=%v", err)
		}
	}
	w := g.chat(t, fmt.Sprintf(`{"model":%q,"max_tokens":1024,"messages":[{"role":"user","content":"synthetic"}]}`, model), nil)
	if w.Code != http.StatusOK || g.managed.calls() != 1 || !checkedSecond {
		t.Fatalf("strict owned chat: status=%d calls=%d repeatChecked=%t body=%s", w.Code, g.managed.calls(), checkedSecond, w.Body)
	}
	journal, err := owner.ReadOwnedJournal(context.Background(), "org-m", requestID)
	id := models.AttemptID(requestID, 1)
	if err != nil || len(journal.Attempts) != 1 || !journal.Attempts[id].Dispatched || !journal.Attempts[id].TokenPriced || len(journal.Record.ManagedJournal) != 1 || !journal.Record.ManagedJournal[id].Dispatched {
		t.Fatalf("immutable journal identity=%q journal=%+v error=%v", id, journal, err)
	}
	if len(attempts) != 2 || attempts[0].FinancialID != id || attempts[0].Outcome != models.OutcomeSuccess {
		t.Fatalf("public log lost financial identity or refused retry: %+v", attempts)
	}
	// A refused start still gets an audit identity from AddAttempt, but must
	// never acquire a financial reservation or carry usage/charge evidence.
	retry := attempts[1]
	if retry.Kind != models.AttemptRetry || retry.Outcome != models.OutcomeSkipped || retry.Reason != models.ReasonAttemptBudget || retry.FinancialID == "" || retry.FinancialID == id || retry.InFlight || retry.Billable || retry.Pricing != nil || retry.Usage != nil || retry.UpstreamCostMicros != 0 || retry.BilledCostMicros != 0 || retry.LandedCostMicros != 0 {
		t.Fatalf("refused retry lost its zero-work audit evidence: %+v", retry)
	}
	if _, exists := journal.Attempts[retry.FinancialID]; exists {
		t.Fatal("refused retry acquired an owned reservation")
	}
	if _, exists := journal.Record.ManagedJournal[retry.FinancialID]; exists {
		t.Fatal("refused retry acquired a financial journal entry")
	}
	if len(journal.Record.Attempts) != 2 || journal.Record.Attempts[0].AttemptID != id {
		t.Fatalf("settlement must retain one dispatch and one refused audit row: %+v", journal.Record.Attempts)
	}
	dispatched, skipped := journal.Record.Attempts[0], journal.Record.Attempts[1]
	if dispatched.Outcome != string(models.OutcomeSuccess) || dispatched.Model != model || dispatched.CredentialType != string(models.CredentialManaged) || dispatched.BilledMicros != 7350 || dispatched.UpstreamMicros != 7000 {
		t.Fatalf("dispatched financial identity has incorrect settlement: %+v", dispatched)
	}
	if skipped.AttemptID != retry.FinancialID || skipped.Kind != string(models.AttemptRetry) || skipped.Outcome != string(models.OutcomeSkipped) || skipped.Model != model || skipped.BilledMicros != 0 || skipped.UpstreamMicros != 0 {
		t.Fatalf("refused retry has nonzero financial effect or missing audit evidence: %+v", skipped)
	}
	account := g.account(t, "org-m")
	rows := g.outbox("org-m")
	if account.SpentMicros != 7350 || account.HeldMicros != 0 || len(rows) != 1 || rows[0]["billed_micros"] != fmt.Sprint(account.SpentMicros) {
		t.Fatalf("strict owned double/missing debit: account=%+v outbox=%v", account, rows)
	}
}

func TestUnionNoRunOrgCapStillAppliesWithStrictDaily(t *testing.T) {
	g, _, _ := capComposedGateway(t)
	policy := g.tenants.Get("org-m")
	limit := 0.000001
	policy.ManagedModels.MaxCallCostUSD = &limit
	g.tenants.Set("org-m", policy)
	before := g.account(t, "org-m")
	w := g.chat(t, chatBody("openai/gpt-6-sol"), nil)
	if w.Code != http.StatusPaymentRequired || errorCode(t, w) != "call_cap_exceeded" || g.managed.calls() != 0 {
		t.Fatalf("no-run org cap bypassed: %d %s", w.Code, w.Body)
	}
	if after := g.account(t, "org-m"); after != before || len(g.outbox("org-m")) != 0 {
		t.Fatalf("pre-credit cap refusal changed wallet/outbox: %+v", after)
	}
}
