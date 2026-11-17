package server

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Who pays for a Console run's model call. Each row sends a Console run's
// model call (a delegated envelope on a console-run grant, POST
// /-/delegated/v1/chat/completions) and checks the status, the
// x-agentcc-billing-mode header, and which upstream took the call with which
// credentials. The gateway's own (operator, platform) instance of a provider
// is never called: every config.yaml provider here holds a node key of its
// own, and every recorded call must carry the org's key, or, on the managed
// rows, the managed provider's key and nothing else. The managed rows also
// check the money: the org ledger's hold, settle or void, and its balance.

// consoleRunPayerIDs are the config.yaml providers of the BYOK rows' gateway,
// each with its own recording upstream. An org provider of the same id
// states no base_url, so it inherits that upstream (OrgProviderCache) but
// calls it with the org's key: one recorder sees both instances, and the
// Authorization header tells them apart. "platform" is never an org
// provider: it is the gateway's own only.
var consoleRunPayerIDs = []string{"aaa-decoy", "openai", "platform", "prov-a", "prov-alias", "prov-b"}

// consoleRunPayerNodeModels is what every config.yaml provider lists, so the
// gateway's own instances serve every model a row asks for: a Console run
// that fell back to one of them would be seen.
var consoleRunPayerNodeModels = []string{"gpt-4o-mini", "gpt-4o", "console-run-alias", "OpenAI/gpt-4o-mini", "gpt-4o-mini:batch", "anthropic/claude-sonnet-5.5", "m-case"}

const consoleRunPayerManagedKey = "test-only-managed-provider-key"

// consoleRunPayerGrantMicros is the org's Future AGI credit on the managed
// rows: one dollar.
const consoleRunPayerGrantMicros = 1_000_000

// consoleRunPayerBilledMicros is what the managed row's one call is billed,
// in micros. The managed upstream is startRecordingUpstream (managed_test.go):
// it answers usage of 1000 prompt and 500 completion tokens and reports its
// own charge, cost 0.007 (upstreamCost). The model's catalogue entry
// (testCatalogModels "anthropic/claude-sonnet-5.5") prices input at 2e-6 and
// output at 10e-6 USD per token, which comes to the same charge:
//
//	1000 × 0.000002 + 500 × 0.000010 = 0.002 + 0.005 = 0.007 USD
//
// The cost plugin (plugins/cost price_attempt.go PriceAttempt) takes the
// reported charge when there is one, else the card's token prices, and
// rounds it to micros once, half up (micros.go usdToMicros):
//
//	upstream = usdToMicros(0.007) = 7000 micros
//
// then bills a managed attempt upstream × (10,000 + markup bps) / 10,000,
// rounded half up (micros.go billedAndLanded, scaleBps), at the rig's 5%
// markup (SetManagedMarkup(5): markupBpsOf(5) = 500 bps):
//
//	billed = scaleBps(7000, 10500) = (7000 × 10500 + 5000) / 10000 (integer division)
//	       = 73,505,000 / 10,000 = 7350
//
// 7000 × 10500 = 73,500,000 is exactly 7350 × 10,000, so the half-up
// rounding changes nothing.
const consoleRunPayerBilledMicros = 7350

// consoleRunPayerBilledCost is consoleRunPayerBilledMicros as the cost plugin
// writes the request's cost (micros.go formatMicros: six decimals, exactly),
// which setAgentccHeaders (response_evidence.go) sends as x-agentcc-cost.
const consoleRunPayerBilledCost = "0.007350"

func consoleRunPayerNodeKey(id string) string { return "test-only-node-" + id + "-key" }
func consoleRunPayerOrgKey(id string) string  { return "test-only-org-" + id + "-key" }

// consoleRunPayerOrgProvider is an enabled org provider with the org's key.
func consoleRunPayerOrgProvider(id string, listed ...string) *tenant.ProviderConfig {
	return &tenant.ProviderConfig{Enabled: true, APIKey: consoleRunPayerOrgKey(id), APIFormat: "openai", Models: listed}
}

// consoleRunPayerRig is a gateway a row runs on, and the calls each of its
// config.yaml providers' upstreams took. On the managed rig, ledger records
// every hold, settle and void the credits plugin made on the org ledger,
// orgLedger is that ledger as the gateway holds it, and managed is the
// managed provider's upstream; all three are nil on the BYOK rig.
type consoleRunPayerRig struct {
	srv       *Server
	ids       []string
	calls     func(id string) []r7Call
	ledger    *consoleRunPayerLedger
	orgLedger *creditsplugin.OrgLedger
	managed   *recordingUpstream
}

// consoleRunPayerBYOKRig is newDelegated733Server with one recording upstream
// per consoleRunPayerIDs entry, and per extra id (a config.yaml provider only
// this row's gateway has), and org as the delegated org's config in place of
// the one newDelegated733Server sets.
func consoleRunPayerBYOKRig(t *testing.T, org *tenant.OrgConfig, extra ...string) consoleRunPayerRig {
	t.Helper()
	off := false
	ids := append(append([]string(nil), consoleRunPayerIDs...), extra...)
	ups := make(map[string]*r7Upstream, len(ids))
	for _, id := range ids {
		ups[id] = r7StartUpstream(t, nil)
	}
	srv := newDelegated733Server(t, func(cfg *config.Config) {
		for _, id := range ids {
			cfg.Providers[id] = config.ProviderConfig{BaseURL: ups[id].URL + "/v1", APIKey: consoleRunPayerNodeKey(id), APIFormat: "openai", Models: consoleRunPayerNodeModels, AutoDiscover: &off}
		}
	})
	if err := srv.handlers.tenantStore.Set(delegated733Org, org); err != nil {
		t.Fatal(err)
	}
	srv.delegated = auth.NewEnvelopeVerifier(delegated733Secret, time.Now().Add(-time.Minute))
	return consoleRunPayerRig{srv: srv, ids: ids, calls: func(id string) []r7Call { return ups[id].snapshot() }}
}

// consoleRunPayerManagedRig is newManagedTestGatewayWith's managed wiring
// (managed_test.go) for the delegated org: a managed OpenRouter-style
// provider "openrouter" on Future AGI's key, an operator provider that is not
// managed, a config.yaml "openai" whose org instance the org holds for
// another model, the bundled test catalogue, and the org's managed credit on
// an in-memory ledger that records what the credits plugin does on it
// (consoleRunPayerLedger). The auth plugin is in the engine, as in
// newDelegated733Server, so the envelope's principal authenticates.
func consoleRunPayerManagedRig(t *testing.T, org *tenant.OrgConfig) consoleRunPayerRig {
	t.Helper()
	managed := startRecordingUpstream(t)
	operator := startRecordingUpstream(t)
	orgUp := r7StartUpstream(t, nil)

	off := false
	cfg := config.DefaultConfig()
	cfg.Admin.Token = delegated733Secret
	cfg.Auth.Enabled = true
	cfg.Providers["openrouter"] = config.ProviderConfig{
		BaseURL: managed.URL, APIKey: consoleRunPayerManagedKey, APIFormat: "openai",
		Type: "openrouter", Managed: true, AutoDiscover: &off,
	}
	cfg.Providers["operator"] = config.ProviderConfig{
		BaseURL: operator.URL, APIKey: consoleRunPayerNodeKey("operator"), APIFormat: "openai",
		Models: []string{"gpt-4o"}, AutoDiscover: &off,
	}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL: orgUp.URL + "/v1", APIKey: consoleRunPayerNodeKey("openai"), APIFormat: "openai",
		Models: []string{"gpt-4o-mini"}, AutoDiscover: &off,
	}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("registry: %v", err)
	}
	store := catalog.NewStore()
	store.Put(&catalog.Snapshot{ProviderID: "openrouter", Source: "bundled", CatalogSource: catalog.SourceOpenRouter,
		FetchedAt: time.Now(), Models: testCatalogModels})

	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(BuildModelDB(nil, store))
	tenants := tenant.NewStore()
	if err := tenants.Set(delegated733Org, org); err != nil {
		t.Fatal(err)
	}

	keyStore := auth.NewKeyStore(cfg.Auth)
	recorder := &consoleRunPayerLedger{MemoryLedger: billing.NewMemoryLedger(), upstreamCalls: managed.calls}
	ledger := creditsplugin.NewOrgLedger(recorder)
	cost := costplugin.New(true, mdb.Load, tenants, nil)
	cost.SetManagedMarkup(5)
	credits := creditsplugin.New(true, keyStore)
	credits.SetOrgLedger(ledger, true)

	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true), cost, credits), keyStore, nil, nil, nil, &mdb, tenants, nil)
	srv.EnableManaged(ManagedSettings{Catalog: store, Ledger: ledger, MarkupPercent: 5, RequireCredits: true})
	h := srv.handlers
	deps := *h.Deps()
	deps.Prices = store.PriceLookup(deps.Prices)
	h.SetInjectedDeps(deps)
	cost.SetPricer(costplugin.NewPricer(h.ModelDB, func() models.PriceLookup { return h.Deps().Prices }))
	credits.SetPrices(func(providerID, model, endpointID string) (models.PriceCard, bool) {
		return h.Deps().Prices(providerID, model, endpointID)
	})
	credits.SetOrgMaxTokens(h.OrgDefaultMaxTokensEstimate)
	h.SetBilling(credits)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	t.Cleanup(srv.batchProcessor.Stop)
	srv.delegated = auth.NewEnvelopeVerifier(delegated733Secret, time.Now().Add(-time.Minute))

	// Future AGI credits granted to the org: one dollar.
	ledger.SetMicros(delegated733Org, auth.USDToMicros(1))

	return consoleRunPayerRig{srv: srv, ids: []string{"openai", "openrouter", "operator"}, calls: func(id string) []r7Call {
		switch id {
		case "openrouter":
			return consoleRunPayerRecorded(managed)
		case "operator":
			return consoleRunPayerRecorded(operator)
		}
		return orgUp.snapshot()
	}, ledger: recorder, orgLedger: ledger, managed: managed}
}

// consoleRunPayerLedgerEvent is one call the credits plugin made on the org
// ledger: op is "reserve" (any RESERVE: plain, inclusive or owned), "settle"
// or "void". upstream is how many calls the managed upstream had taken when
// it was made.
type consoleRunPayerLedgerEvent struct {
	op       string
	org      string
	holdID   string
	micros   int64               // reserve: the micros held
	mode     billing.ReserveMode // reserve: new or add
	rec      billing.Settlement  // settle, void: the record passed
	upstream int
}

// consoleRunPayerLedger is a billing.MemoryLedger that records the credits
// plugin's RESERVE, SETTLE and VOID calls, as routerOffLedger
// (routers_off_746g_test.go) counts its reserves. Every other method,
// including the owned and inclusive interfaces the credits plugin asserts
// the ledger to, is the MemoryLedger's own.
type consoleRunPayerLedger struct {
	*billing.MemoryLedger
	upstreamCalls func() int
	mu            sync.Mutex
	events        []consoleRunPayerLedgerEvent
}

func (l *consoleRunPayerLedger) record(e consoleRunPayerLedgerEvent) {
	if l.upstreamCalls != nil {
		e.upstream = l.upstreamCalls()
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	l.events = append(l.events, e)
}

// snapshot is a copy of the recorded calls, oldest first.
func (l *consoleRunPayerLedger) snapshot() []consoleRunPayerLedgerEvent {
	l.mu.Lock()
	defer l.mu.Unlock()
	return append([]consoleRunPayerLedgerEvent(nil), l.events...)
}

func (l *consoleRunPayerLedger) Reserve(ctx context.Context, org, hold string, micros int64, ttl time.Duration, mode billing.ReserveMode) (billing.ReserveResult, error) {
	l.record(consoleRunPayerLedgerEvent{op: "reserve", org: org, holdID: hold, micros: micros, mode: mode})
	return l.MemoryLedger.Reserve(ctx, org, hold, micros, ttl, mode)
}

func (l *consoleRunPayerLedger) ReserveInclusive(ctx context.Context, org, req, attempt, model string, micros int64, ttl time.Duration, mode billing.ReserveMode) (billing.ReserveResult, error) {
	l.record(consoleRunPayerLedgerEvent{op: "reserve", org: org, holdID: req, micros: micros, mode: mode})
	return l.MemoryLedger.ReserveInclusive(ctx, org, req, attempt, model, micros, ttl, mode)
}

func (l *consoleRunPayerLedger) ReserveOwned(ctx context.Context, org, req, attempt, model string, micros int64, ttl time.Duration, mode billing.ReserveMode, token bool, rec billing.Settlement) (billing.ReserveResult, error) {
	l.record(consoleRunPayerLedgerEvent{op: "reserve", org: org, holdID: req, micros: micros, mode: mode, rec: rec})
	return l.MemoryLedger.ReserveOwned(ctx, org, req, attempt, model, micros, ttl, mode, token, rec)
}

func (l *consoleRunPayerLedger) Settle(ctx context.Context, org, holdID string, rec billing.Settlement) (billing.SettleResult, error) {
	l.record(consoleRunPayerLedgerEvent{op: "settle", org: org, holdID: holdID, rec: rec})
	return l.MemoryLedger.Settle(ctx, org, holdID, rec)
}

func (l *consoleRunPayerLedger) Void(ctx context.Context, org, holdID string, rec billing.Settlement) (billing.SettleResult, error) {
	l.record(consoleRunPayerLedgerEvent{op: "void", org: org, holdID: holdID, rec: rec})
	return l.MemoryLedger.Void(ctx, org, holdID, rec)
}

// consoleRunPayerOps splits the recorded calls by op.
func consoleRunPayerOps(events []consoleRunPayerLedgerEvent) (reserves, settles, voids []consoleRunPayerLedgerEvent) {
	for _, e := range events {
		switch e.op {
		case "reserve":
			reserves = append(reserves, e)
		case "settle":
			settles = append(settles, e)
		case "void":
			voids = append(voids, e)
		}
	}
	return reserves, settles, voids
}

// consoleRunPayerCheckSettled checks the managed row's money: one hold,
// keyed by the request id, taken before the upstream was called; one SETTLE
// of that hold at consoleRunPayerBilledMicros, the request's billed micros
// (the record the credits plugin settles with is settlementOf(rc), whose
// BilledMicros is rc.Billing.BilledMicros); no void; the org's dollar less
// exactly that, nothing held; and x-agentcc-cost naming the same amount.
func consoleRunPayerCheckSettled(t *testing.T, rig consoleRunPayerRig, w *httptest.ResponseRecorder, billed int64, billedCost string) {
	t.Helper()
	events := rig.ledger.snapshot()
	reserves, settles, voids := consoleRunPayerOps(events)
	if len(reserves) != 1 || len(settles) != 1 || len(voids) != 0 {
		t.Fatalf("ledger calls %+v; want exactly one hold, one settle and no void", events)
	}
	hold, settle := reserves[0], settles[0]
	if hold.org != delegated733Org || settle.org != delegated733Org {
		t.Errorf("hold on org %q, settle on org %q; want both on %q", hold.org, settle.org, delegated733Org)
	}
	if hold.holdID == "" || hold.holdID != settle.rec.RequestID || settle.holdID != hold.holdID {
		t.Errorf("hold id %q, settled hold id %q, request id %q; want one non-empty id, the request's", hold.holdID, settle.holdID, settle.rec.RequestID)
	}
	if hold.mode != billing.ReserveNew || hold.micros <= 0 {
		t.Errorf("hold mode %q of %d micros; want a new hold of a positive amount", hold.mode, hold.micros)
	}
	if hold.upstream != 0 || settle.upstream != 1 {
		t.Errorf("the managed upstream had taken %d calls at the hold and %d at the settle; want 0 then 1 (held before the call, settled after)", hold.upstream, settle.upstream)
	}
	if settle.rec.BilledMicros != billed {
		t.Errorf("settled the request's billed micros %d; want %d", settle.rec.BilledMicros, billed)
	}
	if settle.rec.HoldMicros != hold.micros {
		t.Errorf("the settlement names a hold of %d micros; the hold was %d", settle.rec.HoldMicros, hold.micros)
	}
	out := rig.ledger.Outbox(delegated733Org)
	if len(out) != 1 || out[0]["kind"] != billing.KindSpend || out[0]["billed_micros"] != strconv.FormatInt(billed, 10) || out[0]["request_id"] != hold.holdID {
		t.Errorf("outbox %v; want one spend of %d micros for request %q", out, billed, hold.holdID)
	}
	acct, err := rig.ledger.Account(context.Background(), delegated733Org)
	if err != nil {
		t.Fatal(err)
	}
	if acct.GrantedMicros != consoleRunPayerGrantMicros || acct.SpentMicros != billed || acct.HeldMicros != 0 {
		t.Errorf("account %+v; want granted %d, spent %d, nothing held", acct, consoleRunPayerGrantMicros, billed)
	}
	if got := rig.orgLedger.BalanceMicros(delegated733Org); got != consoleRunPayerGrantMicros-billed {
		t.Errorf("balance %d micros; want %d (the $1 grant less the settled %d)", got, consoleRunPayerGrantMicros-billed, billed)
	}
	if got := w.Header().Get("x-agentcc-cost"); got != billedCost {
		t.Errorf("x-agentcc-cost = %q; want %q, the settled amount", got, billedCost)
	}
}

// consoleRunPayerCheckVoided checks the failed managed call's money: its
// hold (one id, the request's) was taken before the upstream was called and
// VOIDED once after it, nothing was settled, and the org holds its whole
// dollar again with nothing held.
func consoleRunPayerCheckVoided(t *testing.T, rig consoleRunPayerRig) {
	t.Helper()
	events := rig.ledger.snapshot()
	reserves, settles, voids := consoleRunPayerOps(events)
	if len(reserves) == 0 || len(settles) != 0 || len(voids) != 1 {
		t.Fatalf("ledger calls %+v; want a hold, exactly one void and no settle", events)
	}
	void := voids[0]
	if void.holdID == "" || void.holdID != void.rec.RequestID {
		t.Errorf("voided hold id %q for request %q; want the request's id", void.holdID, void.rec.RequestID)
	}
	var held int64
	for _, r := range reserves {
		if r.holdID != void.holdID || r.org != delegated733Org {
			t.Errorf("hold %q on org %q; want every hold on the voided id %q of org %q", r.holdID, r.org, void.holdID, delegated733Org)
		}
		held += r.micros
	}
	if reserves[0].mode != billing.ReserveNew || reserves[0].upstream != 0 || void.upstream == 0 {
		t.Errorf("first hold mode %q at %d upstream calls, void at %d; want a new hold before the call and the void after it", reserves[0].mode, reserves[0].upstream, void.upstream)
	}
	if void.rec.HoldMicros != held {
		t.Errorf("the void names a hold of %d micros; %d were held", void.rec.HoldMicros, held)
	}
	out := rig.ledger.Outbox(delegated733Org)
	if len(out) != 1 || out[0]["kind"] != billing.KindVoid || out[0]["billed_micros"] != "0" || out[0]["request_id"] != void.holdID {
		t.Errorf("outbox %v; want one void of 0 micros for request %q", out, void.holdID)
	}
	acct, err := rig.ledger.Account(context.Background(), delegated733Org)
	if err != nil {
		t.Fatal(err)
	}
	if acct.GrantedMicros != consoleRunPayerGrantMicros || acct.SpentMicros != 0 || acct.HeldMicros != 0 {
		t.Errorf("account %+v; want granted %d, nothing spent or held", acct, consoleRunPayerGrantMicros)
	}
	if got := rig.orgLedger.BalanceMicros(delegated733Org); got != consoleRunPayerGrantMicros {
		t.Errorf("balance %d micros; want the whole $1 grant, %d", got, consoleRunPayerGrantMicros)
	}
}

// consoleRunPayerRecorded is what a recordingUpstream was sent, as r7Calls.
func consoleRunPayerRecorded(u *recordingUpstream) []r7Call {
	u.mu.Lock()
	defer u.mu.Unlock()
	out := make([]r7Call, len(u.models))
	for i := range u.models {
		out[i] = r7Call{Model: u.models[i], Auth: u.auths[i]}
	}
	return out
}

// consoleRunPayerChat sends one Console run model call for model, on a fresh
// console-run envelope whose fe1c grant names model alone.
func consoleRunPayerChat(srv *Server, model string) *httptest.ResponseRecorder {
	body := chatBody(model)
	env, grant, _ := signedConsoleRun733(delegated733Route, body, []string{model}, time.Now().Unix())
	return request733(srv, delegated733Route, body, env, grant)
}

type consoleRunPayerRow struct {
	name    string
	managed bool // run on consoleRunPayerManagedRig; else consoleRunPayerBYOKRig
	org     *tenant.OrgConfig
	model   string // the model the Console run asks for
	sends   int    // calls sent, each on a fresh envelope; 0 is 1
	want    string // x-agentcc-billing-mode on a 200; "" for a refusal or a failure
	served  string // config.yaml provider id whose upstream takes every call
	sent    string // the model id that upstream must be sent
	payKey  string // the key that upstream must be called with
	// extra are config.yaml providers only this row's BYOK gateway has, each
	// with its own recording upstream, listing consoleRunPayerNodeModels.
	extra []string
	// upstreamFails: the managed upstream answers 500 with an OpenAI error
	// body; every call fails (not 2xx) after reaching served.
	upstreamFails bool
	// noReportedCost: the managed upstream's usage carries no cost, so the
	// catalogue's token prices price the call. reportedCost: the managed
	// upstream reports this charge in USD, which wins over the catalogue.
	noReportedCost bool
	reportedCost   float64
	// billed and billedCost: a settled managed row's micros and its
	// x-agentcc-cost; zero means consoleRunPayerBilledMicros/Cost.
	billed     int64
	billedCost string
	// refusal: a refused row's error code; when set the call must answer
	// 403 with that code in its body.
	refusal string
}

func TestDelegatedConsoleRunPayerTable(t *testing.T) {
	rows := []consoleRunPayerRow{
		{
			// orgRouteFor rule 1: the org's model_map aliases a typed name to
			// its own provider, which lists one other model. A Console run is
			// held to its cap: the model served would not be a cap id, so the
			// call is refused and no upstream is called (fi-security 1a763d9b;
			// the Console menu lists no aliases).
			name: "alias to another model, refused",
			org: &tenant.OrgConfig{
				ModelMap:  map[string]string{"console-run-alias": "prov-alias"},
				Providers: map[string]*tenant.ProviderConfig{"prov-alias": consoleRunPayerOrgProvider("prov-alias", "gpt-4o")},
			},
			model: "console-run-alias",
		},
		{
			// orgRouteFor rule 1 on an id the org lists: prov-a lists
			// m-listed, but the org's model_map sends m-listed to prov-b,
			// which lists exactly one other model (m-other). The call would
			// be served as m-other, not a cap id, so a Console run is refused
			// with no upstream called (STRICT, fi-security 1a763d9b); the
			// Console menu does not list such an id.
			name: "listed id mapped to another model, refused",
			org: &tenant.OrgConfig{
				ModelMap: map[string]string{"m-listed": "prov-b"},
				Providers: map[string]*tenant.ProviderConfig{
					"prov-a": consoleRunPayerOrgProvider("prov-a", "m-listed"),
					"prov-b": consoleRunPayerOrgProvider("prov-b", "m-other"),
				},
			},
			model: "m-listed", refusal: "model_not_permitted",
		},
		{
			// orgRouteFor rule 2: "<provider>/<model>" with the prefix in
			// another case reaches the org provider the prefix names, which
			// lists the bare id, ahead of aaa-decoy, which sorts first and
			// lists the whole id as its own model name (rule 3 would pick it).
			name: "provider-prefixed",
			org: &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
				"openai":    consoleRunPayerOrgProvider("openai", "gpt-4o-mini"),
				"aaa-decoy": consoleRunPayerOrgProvider("aaa-decoy", "OpenAI/gpt-4o-mini"),
			}},
			model: "OpenAI/gpt-4o-mini", want: billingBYOK,
			served: "openai", sent: "gpt-4o-mini", payKey: consoleRunPayerOrgKey("openai"),
		},
		{
			// orgRouteFor rule 3: two enabled org providers list the model;
			// the smallest provider id in byte order serves it
			// (nextOrgProviderListing), on every call, whatever the map's
			// iteration order.
			name: "two providers",
			org: &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
				"prov-b": consoleRunPayerOrgProvider("prov-b", "gpt-4o-mini"),
				"prov-a": consoleRunPayerOrgProvider("prov-a", "gpt-4o-mini"),
			}},
			model: "gpt-4o-mini", sends: 5, want: billingBYOK,
			served: "prov-a", sent: "gpt-4o-mini", payKey: consoleRunPayerOrgKey("prov-a"),
		},
		{
			// rule 3's order is Go's string order, bytes, not a case-folded
			// one: "Zeta" < "alpha" because 'Z' (0x5A) < 'a' (0x61). Both org
			// providers list the model; "Zeta" serves every call and "alpha"
			// takes none. Both are config.yaml providers of this row's
			// gateway only, so the other rows' registry is unchanged.
			name: "two providers, byte order",
			org: &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
				"alpha": consoleRunPayerOrgProvider("alpha", "m-case"),
				"Zeta":  consoleRunPayerOrgProvider("Zeta", "m-case"),
			}},
			extra: []string{"Zeta", "alpha"},
			model: "m-case", sends: 5, want: billingBYOK,
			served: "Zeta", sent: "m-case", payKey: consoleRunPayerOrgKey("Zeta"),
		},
		{
			// No org provider serves the model; the org has Future AGI credits
			// on and a granted balance, and the model is in the managed
			// catalogue at a price: the managed provider serves it on Future
			// AGI's key, billed to the org's credit.
			name:    "managed",
			managed: true,
			org: &tenant.OrgConfig{
				ManagedModels: &tenant.ManagedModelsConfig{Enabled: true},
				Providers:     map[string]*tenant.ProviderConfig{"openai": consoleRunPayerOrgProvider("openai", "gpt-4o-mini")},
			},
			model: "anthropic/claude-sonnet-5.5", want: billingManaged,
			served: "openrouter", sent: "anthropic/claude-sonnet-5.5", payKey: consoleRunPayerManagedKey,
		},
		{
			// The managed row, but the managed upstream reports no cost: the
			// catalogue's token prices price it, to the same literal,
			// 1000 × 0.000002 + 500 × 0.00001 = $0.007 = 7000 micros,
			// × 10500 / 10000 = 7350.
			name:    "managed, catalogue priced",
			managed: true,
			org: &tenant.OrgConfig{
				ManagedModels: &tenant.ManagedModelsConfig{Enabled: true},
				Providers:     map[string]*tenant.ProviderConfig{"openai": consoleRunPayerOrgProvider("openai", "gpt-4o-mini")},
			},
			model: "anthropic/claude-sonnet-5.5", want: billingManaged, noReportedCost: true,
			served: "openrouter", sent: "anthropic/claude-sonnet-5.5", payKey: consoleRunPayerManagedKey,
		},
		{
			// The managed row, but the managed upstream reports $0.006, not
			// the catalogue's $0.007: the provider's figure wins, so a
			// managed call is charged the provider's reported cost plus the
			// markup, else the catalogue's: 6000 micros × 10500 / 10000 = 6300
			// exactly.
			name:    "managed, provider reported cost",
			managed: true,
			org: &tenant.OrgConfig{
				ManagedModels: &tenant.ManagedModelsConfig{Enabled: true},
				Providers:     map[string]*tenant.ProviderConfig{"openai": consoleRunPayerOrgProvider("openai", "gpt-4o-mini")},
			},
			model: "anthropic/claude-sonnet-5.5", want: billingManaged, reportedCost: 0.006,
			billed: 6300, billedCost: "0.006300",
			served: "openrouter", sent: "anthropic/claude-sonnet-5.5", payKey: consoleRunPayerManagedKey,
		},
		{
			// The managed row's org and model, but the managed upstream
			// answers 500: the call fails, its hold is voided, and the org's
			// dollar is whole again; nothing is settled or held.
			name:    "managed, upstream error",
			managed: true,
			org: &tenant.OrgConfig{
				ManagedModels: &tenant.ManagedModelsConfig{Enabled: true},
				Providers:     map[string]*tenant.ProviderConfig{"openai": consoleRunPayerOrgProvider("openai", "gpt-4o-mini")},
			},
			model: "anthropic/claude-sonnet-5.5", upstreamFails: true,
			served: "openrouter", sent: "anthropic/claude-sonnet-5.5", payKey: consoleRunPayerManagedKey,
		},
		{
			// The only org provider for the model holds no credentials
			// (orgProviderUsable): refused, never the gateway's own instance.
			name: "refused, no credentials",
			org: &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
				"prov-a": {Enabled: true, APIFormat: "openai", Models: []string{"gpt-4o-mini"}},
			}},
			model: "gpt-4o-mini",
		},
		{
			// The only org provider for the model is disabled.
			name: "refused, disabled",
			org: &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
				"prov-a": {Enabled: false, APIKey: consoleRunPayerOrgKey("prov-a"), APIFormat: "openai", Models: []string{"gpt-4o-mini"}},
			}},
			model: "gpt-4o-mini",
		},
		{
			// A ":batch" id: the org provider lists the plain id only, and an
			// org listing matches exactly (orgModelMatches), so nothing of the
			// org's serves it; the gateway's own providers list it and are
			// never called.
			name: "refused, batch variant",
			org: &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
				"openai": consoleRunPayerOrgProvider("openai", "gpt-4o-mini"),
			}},
			model: "gpt-4o-mini:batch",
		},
	}
	for _, row := range rows {
		t.Run(row.name, func(t *testing.T) {
			var rig consoleRunPayerRig
			if row.managed {
				rig = consoleRunPayerManagedRig(t, row.org)
				if row.upstreamFails {
					rig.managed.status.Store(http.StatusInternalServerError)
				}
				if row.reportedCost != 0 {
					cost := row.reportedCost
					rig.managed.reportedCost.Store(&cost)
				}
				if row.noReportedCost {
					var answer upstreamStream = func(w http.ResponseWriter, _ *http.Request, model string) {
						w.Header().Set("Content-Type", "application/json")
						fmt.Fprintf(w, `{"id":"chatcmpl-console-run","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1000,"completion_tokens":500,"total_tokens":1500}}`, model)
					}
					rig.managed.responseWith.Store(&answer)
				}
			} else {
				rig = consoleRunPayerBYOKRig(t, row.org, row.extra...)
			}
			sends := row.sends
			if sends == 0 {
				sends = 1
			}
			var last *httptest.ResponseRecorder
			for i := 0; i < sends; i++ {
				w := consoleRunPayerChat(rig.srv, row.model)
				last = w
				if row.upstreamFails {
					if w.Code/100 == 2 {
						t.Fatalf("call %d for %q answered %d with its upstream failing: %s", i, row.model, w.Code, w.Body.String())
					}
					continue
				}
				if row.want == "" {
					if w.Code/100 == 2 {
						t.Fatalf("a refused Console run call for %q answered %d: %s", row.model, w.Code, w.Body.String())
					}
					if w.Code != 403 && w.Code != 404 {
						t.Fatalf("refusal status %d, want 403 or 404: %s", w.Code, w.Body.String())
					}
					if row.refusal != "" && (w.Code != 403 || !strings.Contains(w.Body.String(), row.refusal)) {
						t.Fatalf("refusal %d %s, want 403 %s", w.Code, w.Body.String(), row.refusal)
					}
					continue
				}
				if w.Code != 200 {
					t.Fatalf("call %d for %q: %d %s", i, row.model, w.Code, w.Body.String())
				}
				if got := w.Header().Get("x-agentcc-billing-mode"); got != row.want {
					t.Fatalf("call %d: x-agentcc-billing-mode = %q, want %q", i, got, row.want)
				}
			}
			for _, id := range rig.ids {
				calls := rig.calls(id)
				if (row.want == "" && !row.upstreamFails) || id != row.served {
					if len(calls) != 0 {
						t.Errorf("upstream %q took %d calls, want none: %v", id, len(calls), calls)
					}
					continue
				}
				if row.upstreamFails {
					if len(calls) == 0 {
						t.Errorf("upstream %q took no call; the failing upstream must have been reached", id)
					}
				} else if len(calls) != sends {
					t.Errorf("upstream %q took %d calls, want %d: %v", id, len(calls), sends, calls)
				}
				for _, c := range calls {
					if c.Model != row.sent || c.Auth != "Bearer "+row.payKey {
						t.Errorf("upstream %q was sent model %q with %q; want %q with the paying key %q", id, c.Model, c.Auth, row.sent, row.payKey)
					}
				}
			}
			if row.managed {
				if row.upstreamFails {
					consoleRunPayerCheckVoided(t, rig)
				} else {
					billed, billedCost := row.billed, row.billedCost
					if billed == 0 {
						billed, billedCost = consoleRunPayerBilledMicros, consoleRunPayerBilledCost
					}
					consoleRunPayerCheckSettled(t, rig, last, billed, billedCost)
				}
			}
		})
	}
}
