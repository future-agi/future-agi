package capability

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const testOrg = "org-test"

// repoPath is a path from the repository root.
func repoPath(parts ...string) string {
	_, here, _, _ := runtime.Caller(0)
	return filepath.Join(append([]string{filepath.Dir(here), "..", "..", ".."}, parts...)...)
}

// goldenCards are the tool-price-card-v1 golden cards: exa (managed
// partner, cacheable per org, metadata retention), parallel (unconfirmed,
// no_divert, never cached), tavily (byok_only, no_perf_disclosure,
// eval_export forbidden) and futureagi guardrails (native, owner price
// pending).
func goldenCards(t *testing.T) []*Card {
	t.Helper()
	raw, err := os.ReadFile(repoPath("api_contracts", "gateway", "examples", "tool-price-card-v1.json"))
	if err != nil {
		t.Fatal(err)
	}
	cards, err := CardsFromJSON(raw)
	if err != nil {
		t.Fatal(err)
	}
	return cards
}

// goldenCard is the golden card of vendor, copied so a test may change it.
func goldenCard(t *testing.T, vendor string) *Card {
	t.Helper()
	for _, c := range goldenCards(t) {
		if c.Vendor == vendor {
			cp := *c
			cp.SKUs = append([]SKU(nil), c.SKUs...)
			cp.Regions = append([]string(nil), c.Regions...)
			return &cp
		}
	}
	t.Fatalf("no golden card for %s", vendor)
	return nil
}

// echoFixtureCards are the scenario's echo-a and echo-b cards
// (scripts/scenarios/fixtures/capability-echo/tools/echo), $0.001 per call, managed_partner.
func echoFixtureCards(t *testing.T) []*Card {
	t.Helper()
	var out []*Card
	for _, v := range []string{"echo-a", "echo-b"} {
		raw, err := os.ReadFile(repoPath("agentcc-gateway", "scripts", "scenarios", "fixtures", "capability-echo", "tools", "echo", v+".json"))
		if err != nil {
			t.Fatal(err)
		}
		cards, err := CardsFromJSON(raw)
		if err != nil {
			t.Fatal(err)
		}
		out = append(out, cards...)
	}
	return out
}

// card is a test card for vendor on capability, priced usd per request, with
// permissive terms; mutate adjusts it.
func card(capability, vendor string, usd float64, mutate ...func(*Card)) *Card {
	c := &Card{Vendor: vendor, Capability: capability,
		SKUs:         []SKU{{SKU: capability + "." + vendor, Unit: "request", USDPerUnit: usd, FreeOnFailure: true}},
		ResaleStatus: ResaleManagedPartner, ResaleTermsRef: "test-ref", TermsURL: "https://example.invalid/terms",
		TermsCheckedAt: "2026-10-16", ZDRCapable: true, TrainsOnInput: TrainsNo, Regions: []string{"us", "eu"},
		ResultsRetention: "full", Cacheable: CacheNone, EvalExport: "allowed"}
	for _, m := range mutate {
		m(c)
	}
	return c
}

// vendorFake is an httptest echo vendor: each vendor's path answers with the
// request body, or with the status armed for it.
type vendorFake struct {
	*httptest.Server
	mu     sync.Mutex
	status map[string]int
	calls  map[string]int
}

func startVendors(t *testing.T) *vendorFake {
	t.Helper()
	f := &vendorFake{status: map[string]int{}, calls: map[string]int{}}
	f.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		vendor := filepath.Base(filepath.Dir(r.URL.Path)) // /<vendor>/echo
		body, _ := io.ReadAll(r.Body)
		f.mu.Lock()
		f.calls[vendor]++
		status := f.status[vendor]
		f.mu.Unlock()
		if status != 0 {
			w.WriteHeader(status)
			fmt.Fprintf(w, `{"error":"armed %d"}`, status)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write(body)
	}))
	t.Cleanup(f.Close)
	return f
}

func (f *vendorFake) fail(vendor string, status int) {
	f.mu.Lock()
	f.status[vendor] = status
	f.mu.Unlock()
}

func (f *vendorFake) count(vendor string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.calls[vendor]
}

// operator is operator config with a managed credential for each vendor, at
// the fake's /<vendor> base URL.
func (f *vendorFake) operator(cards []*Card, managed ...string) map[string]config.ProviderConfig {
	out := map[string]config.ProviderConfig{}
	for _, c := range cards {
		out[c.Vendor] = config.ProviderConfig{BaseURL: f.URL + "/" + c.Vendor}
	}
	for _, v := range managed {
		pc := out[v]
		pc.Managed, pc.APIKey = true, "fagi-test-"+v
		out[v] = pc
	}
	return out
}

// testLedger is the managed-credit store: Redis (TEST_REDIS_ADDR, DB
// TEST_REDIS_DB, else DB 9, under test:capability:<test>:<nonce>:, keys
// deleted after) when it is set, else the in-memory ledger.
func testLedger(t *testing.T) billing.Ledger {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		return billing.NewMemoryLedger()
	}
	db := 9
	if v := os.Getenv("TEST_REDIS_DB"); v != "" {
		db, _ = strconv.Atoi(v)
	}
	client, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: db, Timeout: time.Second})
	if err != nil {
		t.Fatalf("TEST_REDIS_ADDR=%s is set but Redis is not reachable: %v", addr, err)
	}
	prefix := fmt.Sprintf("test:capability:%s:%d:", t.Name(), time.Now().UnixNano())
	rdb := client.Redis()
	t.Cleanup(func() {
		ctx := context.Background()
		iter := rdb.Scan(ctx, 0, prefix+"*", 1000).Iterator()
		var keys []string
		for iter.Next(ctx) {
			keys = append(keys, iter.Val())
		}
		if len(keys) > 0 {
			rdb.Del(ctx, keys...)
		}
		client.Close()
	})
	store := redisstate.NewManagedCreditStore(client, prefix)
	t.Cleanup(func() { outboxReaders.Delete(store) })
	outboxReaders.Store(store, func(org string) []map[string]string {
		msgs, err := rdb.XRange(context.Background(), prefix+"mc:{"+org+"}:outbox", "-", "+").Result()
		if err != nil {
			t.Fatalf("read outbox: %v", err)
		}
		var out []map[string]string
		for _, m := range msgs {
			rec := map[string]string{}
			for k, v := range m.Values {
				rec[k] = fmt.Sprint(v)
			}
			out = append(out, rec)
		}
		return out
	})
	return store
}

var outboxReaders sync.Map // billing.Ledger -> func(org) []map[string]string

// outbox is the org's settlement records on l.
func outbox(t *testing.T, l billing.Ledger, org string) []map[string]string {
	t.Helper()
	if mem, ok := l.(*billing.MemoryLedger); ok {
		return mem.Outbox(org)
	}
	read, ok := outboxReaders.Load(l)
	if !ok {
		t.Fatal("no outbox reader for this ledger")
	}
	return read.(func(string) []map[string]string)(org)
}

func grant(t *testing.T, l billing.Ledger, org string, micros int64) {
	t.Helper()
	acct, err := l.Account(context.Background(), org)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := l.Grant(context.Background(), org, billing.GrantPush{GrantedTotalMicros: micros, Version: acct.GrantedVersion + 1}); err != nil {
		t.Fatal(err)
	}
}

func account(t *testing.T, l billing.Ledger, org string) billing.Account {
	t.Helper()
	acct, err := l.Account(context.Background(), org)
	if err != nil {
		t.Fatal(err)
	}
	return acct
}

// newRC is a request of testOrg's key key-1, released when the test ends.
func newRC(t *testing.T, id string) *models.RequestContext {
	t.Helper()
	rc := models.AcquireRequestContext()
	rc.RequestID = id
	rc.Metadata["org_id"] = testOrg
	rc.Metadata["auth_key_id"] = "key-1"
	t.Cleanup(rc.Release)
	return rc
}

// orgConfig is an org that enabled capability with vendors in order, and,
// when managed is set, opted in to managed tools for capability/*.
func orgConfig(capability string, vendors []string, managed bool) *tenant.OrgConfig {
	on := true
	cfg := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{},
		Tools: &tenant.ToolsConfig{Capabilities: map[string]*tenant.ToolsCapabilityConfig{
			capability: {Enabled: &on, Providers: vendors}}}}
	if managed {
		cfg.MCP = &tenant.MCPOrgConfig{ManagedTools: &tenant.MCPManagedToolsConfig{Enabled: &on, Allow: []string{capability + "/*"}}}
	}
	return cfg
}

// withBYOK gives the org its own key for vendor.
func withBYOK(cfg *tenant.OrgConfig, vendors ...string) *tenant.OrgConfig {
	for _, v := range vendors {
		cfg.Providers[v] = &tenant.ProviderConfig{APIKey: "org-key-" + v, Enabled: true}
	}
	return cfg
}

func setFallbackToManaged(cfg *tenant.OrgConfig) *tenant.OrgConfig {
	on := true
	cfg.MCP.ManagedTools.FallbackToManaged = &on
	return cfg
}

// testCore is a core whose capability (of class) has an echo adapter for
// each card's vendor, at the fake vendors, with the operator's managed
// credentials for managed.
type testCore struct {
	*Core
	vendors *vendorFake
	ledger  billing.Ledger
}

type coreSetup struct {
	capability string
	class      Class
	cards      []*Card
	managed    []string
	ledger     billing.Ledger
	markupBps  int
	cacheTTL   time.Duration
	now        func() time.Time
	adapters   map[string]Adapter
}

func newTestCore(t *testing.T, s coreSetup) *testCore {
	t.Helper()
	if s.class == "" {
		s.class = ClassStatelessCrossVendor
	}
	if s.ledger == nil {
		s.ledger = billing.NewMemoryLedger()
	}
	vendors := startVendors(t)
	reg := NewRegistry()
	if err := reg.RegisterCapability(s.capability, Spec{Class: s.class, EndpointType: "search"}); err != nil {
		t.Fatal(err)
	}
	for _, c := range s.cards {
		a := s.adapters[c.Vendor]
		if a == nil {
			a = EchoAdapter{Client: vendors.Client()}
		}
		if err := reg.Register(s.capability, c.Vendor, a); err != nil {
			t.Fatal(err)
		}
	}
	core := NewCore(Options{Registry: reg, Cards: NewStaticCards(s.cards...),
		Credentials: ConfigCredentials{Operator: vendors.operator(s.cards, s.managed...)}, Ledger: s.ledger,
		MarkupBps: s.markupBps, ProcessingFeeBps: 320, HoldTTL: time.Minute, ReserveDefaultUSD: 0.10,
		CacheTTL: s.cacheTTL, Now: s.now})
	return &testCore{Core: core, vendors: vendors, ledger: s.ledger}
}

// call runs capability on a fresh request with id, body {"q": id}.
func (tc *testCore) call(t *testing.T, id, capability string, req Request) (*models.RequestContext, Result, error) {
	t.Helper()
	rc := newRC(t, id)
	if req.Body == nil {
		req.Body = json.RawMessage(`{"q":"` + id + `"}`)
	}
	res, err := tc.Do(context.Background(), rc, capability, req)
	return rc, res, err
}

// apiErr is err as an APIError with the wanted status and code.
func wantAPIError(t *testing.T, err error, status int, code string) *models.APIError {
	t.Helper()
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("error %v (%T), want an APIError %d %s", err, err, status, code)
	}
	if apiErr.Status != status || apiErr.Code != code {
		t.Fatalf("error %d %s (%s), want %d %s", apiErr.Status, apiErr.Code, apiErr.Message, status, code)
	}
	return apiErr
}

func reasonOf(apiErr *models.APIError) string {
	m, _ := apiErr.Metadata.(map[string]any)
	r, _ := m["reason"].(string)
	return r
}

// steppingClock advances by step on every read.
func steppingClock(step time.Duration) func() time.Time {
	var n atomic.Int64
	start := time.Date(2026, 10, 17, 0, 0, 0, 0, time.UTC)
	return func() time.Time { return start.Add(time.Duration(n.Add(1)) * step) }
}
