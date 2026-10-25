package server

import (
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// newCardTestGateway is newManagedTestGateway plus a managed direct provider
// ("direct") whose catalog is a price card: it sells card-model, and lists
// stale-model (its card too old to bill by) and unpriced-model (no price).
// Its upstream reports no cost, as a direct vendor's does not.
func newCardTestGateway(t *testing.T, orgCfg *tenant.OrgConfig) (*managedTestGateway, *recordingUpstream) {
	t.Helper()
	managed, operator, direct := startRecordingUpstream(t), startRecordingUpstream(t), startRecordingUpstream(t)
	off := false
	cfg := config.DefaultConfig()
	cfg.Admin.Token = adminToken
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name: "org-user", Key: managedOrgKey, Owner: "user", KeyType: "byok",
		Metadata: map[string]string{"org_id": "org-m"},
	}}
	cfg.Providers["openrouter"] = config.ProviderConfig{BaseURL: managed.URL, APIKey: "fagi-openrouter-key",
		APIFormat: "openai", Type: "openrouter", Managed: true, AutoDiscover: &off}
	cfg.Providers["operator"] = config.ProviderConfig{BaseURL: operator.URL, APIKey: "fagi-operator-key",
		APIFormat: "openai", Models: []string{"gpt-4o"}, AutoDiscover: &off}
	cfg.Providers["direct"] = config.ProviderConfig{BaseURL: direct.URL, APIKey: "fagi-direct-key", APIFormat: "openai",
		Type: "openai", Managed: true, CatalogSource: catalog.SourcePriceCard, AutoDiscover: &off}
	cfg.Routing.ProviderLock = config.ProviderLockConfig{Enabled: true}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("registry: %v", err)
	}
	store := catalog.NewStore()
	store.Put(&catalog.Snapshot{ProviderID: "openrouter", Source: catalog.OriginBundled, CatalogSource: catalog.SourceOpenRouter,
		FetchedAt: time.Now(), Models: testCatalogModels})
	store.Put(&catalog.Snapshot{ProviderID: "direct", Source: catalog.OriginPriceCard, CatalogSource: catalog.SourcePriceCard,
		FetchedAt: time.Now(), Models: map[string]*catalog.Model{
			// The card's endpoint collects no data, as a card for an account
			// whose terms collect none says (managed traffic denies data
			// collection by default, so an "unknown" endpoint is refused).
			"card-model": {ID: "card-model", InputPerToken: 3e-6, OutputPerToken: 15e-6,
				Endpoints: []catalog.Endpoint{directEndpoint("card-model", 3e-6)}},
			"stale-model":    {ID: "stale-model", InputPerToken: 3e-6, OutputPerToken: 15e-6, StalePrice: true},
			"unpriced-model": {ID: "unpriced-model", Unpriced: true},
		}})
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(BuildModelDB(nil, store))
	tenants := tenant.NewStore()
	tenants.Set("org-m", orgCfg)
	keyStore := auth.NewKeyStore(cfg.Auth)
	ledger := creditsplugin.NewOrgLedger(nil)
	cost := costplugin.New(true, mdb.Load, tenants, nil)
	cost.SetManagedMarkup(5)
	credits := creditsplugin.New(true, keyStore)
	credits.SetOrgLedger(ledger, true)
	srv := New(cfg, "", registry, pipeline.NewEngine(cost, credits), keyStore, nil, nil, nil, &mdb, tenants, nil)
	srv.EnableManaged(ManagedSettings{Catalog: store, Ledger: ledger, MarkupPercent: 5, RequireCredits: true})
	srv.ready.Store(true)
	ledger.SetMicros("org-m", auth.USDToMicros(1))
	return &managedTestGateway{srv: srv, ledger: ledger, tenants: tenants, managed: managed, operator: operator}, direct
}

// cardOptIn opts the org in and allows data collection, so that the
// managed data policy (deny by default) refuses nothing here: every refusal
// in these tests must come from the card itself (ServesManaged). With the
// default policy an unlisted model is refused as data_policy_collection,
// which hides a missing ServesManaged check.
func cardOptIn() *tenant.OrgConfig {
	cfg := managedOptIn()
	cfg.ManagedModels.AllowDataCollection = true
	return cfg
}

// TestPriceCardProviderServesOnlyWhatItsCardSells: a managed
// provider whose upstream reports no cost is never sent, on FutureAGI's
// credentials, a model its card does not sell, whichever path names it: the
// "<managed id>/<model>" prefix, a provider lock, or a fallback: a managed
// call is served only when it can be billed. OpenRouter's prefix path serves uncatalogued models
// (TestManagedProviderPrefixIsStrippedForUncataloguedModels).
func TestPriceCardProviderServesOnlyWhatItsCardSells(t *testing.T) {
	g, direct := newCardTestGateway(t, cardOptIn())

	if w := g.chat(t, chatBody("direct/card-model"), nil); w.Code != http.StatusOK || direct.lastModel() != "card-model" ||
		w.Header().Get("x-agentcc-billing-mode") != "managed" {
		t.Fatalf("a card model: status %d, upstream model %q, billing %q; body %s", w.Code, direct.lastModel(),
			w.Header().Get("x-agentcc-billing-mode"), w.Body)
	}
	served := direct.calls()
	for _, model := range []string{"direct/totally-unlisted-model", "direct/stale-model", "direct/unpriced-model"} {
		if w := g.chat(t, chatBody(model), nil); w.Code != http.StatusForbidden {
			t.Errorf("%s: status %d, want 403; body %s", model, w.Code, w.Body)
		}
	}
	lock := map[string]string{"x-agentcc-provider-lock": "direct"}
	if w := g.chat(t, chatBody("totally-unlisted-model"), lock); w.Code != http.StatusForbidden {
		t.Errorf("locked onto direct with an unlisted model: status %d, want 403; body %s", w.Code, w.Body)
	}
	if direct.calls() != served {
		t.Fatalf("the direct upstream was sent %d models its card does not sell", direct.calls()-served)
	}
	if w := g.chat(t, chatBody("card-model"), lock); w.Code != http.StatusOK || direct.calls() != served+1 {
		t.Fatalf("locked onto direct with a card model: status %d; body %s", w.Code, w.Body)
	}
}

func TestPriceCardProviderFallbackServesOnlyWhatItsCardSells(t *testing.T) {
	for _, tc := range []struct {
		fallback string
		served   bool
	}{{"direct/totally-unlisted-model", false}, {"direct/stale-model", false}, {"direct/card-model", true}} {
		cfg := cardOptIn()
		cfg.Routing = &tenant.RoutingConfig{
			FallbackEnabled: true,
			ModelFallbacks:  map[string][]string{"anthropic/claude-sonnet-5.5": {tc.fallback}},
		}
		g, direct := newCardTestGateway(t, cfg)
		g.managed.status.Store(http.StatusServiceUnavailable)

		w := g.chat(t, chatBody("anthropic/claude-sonnet-5.5"), nil)

		if got := direct.calls() > 0; got != tc.served {
			t.Errorf("fallback %s: direct upstream called = %v, want %v (status %d)", tc.fallback, got, tc.served, w.Code)
		}
	}
}

// An org's auto candidates never put a model a card does not sell in the
// pool: "auto" over only such a candidate finds none (400
// no_auto_candidate), rather than choosing it and being refused later.
func TestPriceCardProviderAutoCandidates(t *testing.T) {
	for _, tc := range []struct {
		candidate string
		served    bool
	}{{"direct/totally-unlisted-model", false}, {"direct/stale-model", false}, {"direct/unpriced-model", false}, {"direct/card-model", true}} {
		cfg := cardOptIn()
		cfg.Routing = &tenant.RoutingConfig{Auto: &tenant.AutoRoutingConfig{Candidates: []string{tc.candidate}}}
		g, direct := newCardTestGateway(t, cfg)

		w := g.chat(t, chatBody("auto"), nil)

		if got := direct.calls() > 0; got != tc.served || (w.Code == http.StatusOK) != tc.served {
			t.Errorf("auto over %s: direct upstream called = %v, status %d; want served = %v; body %s",
				tc.candidate, got, w.Code, tc.served, w.Body)
		}
		if !tc.served && (w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "no_auto_candidate")) {
			t.Errorf("auto over %s: status %d body %s, want 400 no_auto_candidate", tc.candidate, w.Code, w.Body)
		}
	}
}
