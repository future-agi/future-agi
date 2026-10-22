package main

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/server"
)

// TestWiringOrder checks the wiring_<concern>.go inits: the documented order,
// each name bound to its own init, each init run exactly once in that order,
// the afterServer work run after them in the same order, and main running
// the inits before the pipeline is built and the afterServer work after the
// server is.
func TestWiringOrder(t *testing.T) {
	want := []struct {
		name string
		init func(*wiringDeps) error
	}{
		{"catalog", initCatalog},
		{"modelhealth", initModelHealth},
		{"pricing", initPricing},
		{"credits", initCredits},
		{"outcomes", initOutcomes},
		{"agentbudget", initAgentBudget},
		{"spendguard", initSpendGuard},
		{"idempotency", initIdempotency},
		{"generation", initGeneration},
		{"telemetry", initTelemetry},
		{"promptcache", initPromptCache},
		{"selector", initSelector},
		{"runtimeauth", initRuntimeAuth},
		{"toolcallguard", initToolCallGuard},
		{"routing_options", initRoutingOptions},
		{"privacy", initPrivacy},
		{"capability", initCapability},
		{"mcp", initMCP},
	}
	if len(wiringOrder) != len(want) {
		t.Fatalf("wiringOrder has %d steps, want %d", len(wiringOrder), len(want))
	}
	for i, w := range want {
		got := wiringOrder[i]
		if got.name != w.name || reflect.ValueOf(got.init).Pointer() != reflect.ValueOf(w.init).Pointer() {
			t.Errorf("wiringOrder[%d] = %s, want %s bound to its own init", i, got.name, w.name)
		}
	}

	t.Run("each init once, in order, then afterServer in order", func(t *testing.T) {
		var calls []string
		steps := make([]wiringStep, len(wiringOrder))
		for i, s := range wiringOrder {
			steps[i] = wiringStep{s.name, func(d *wiringDeps) error {
				calls = append(calls, "init:"+s.name)
				d.afterServer(func() error {
					calls = append(calls, "after:"+s.name)
					return nil
				})
				return nil
			}}
		}
		d := &wiringDeps{}
		if err := runWiring(d, steps); err != nil {
			t.Fatalf("runWiring: %v", err)
		}
		var wantCalls []string
		for _, w := range want {
			wantCalls = append(wantCalls, "init:"+w.name)
		}
		if strings.Join(calls, ",") != strings.Join(wantCalls, ",") {
			t.Fatalf("inits ran %v, want %v", calls, wantCalls)
		}
		calls = nil
		if err := d.startServerWiring(); err != nil {
			t.Fatalf("startServerWiring: %v", err)
		}
		for i, w := range want {
			wantCalls[i] = "after:" + w.name
		}
		if strings.Join(calls, ",") != strings.Join(wantCalls, ",") {
			t.Fatalf("afterServer work ran %v, want %v", calls, wantCalls)
		}
	})

	t.Run("an init error stops the wiring and names the step", func(t *testing.T) {
		boom := errors.New("boom")
		var ran []string
		steps := []wiringStep{
			{"a", func(*wiringDeps) error { ran = append(ran, "a"); return nil }},
			{"b", func(*wiringDeps) error { ran = append(ran, "b"); return boom }},
			{"c", func(*wiringDeps) error { ran = append(ran, "c"); return nil }},
		}
		err := runWiring(&wiringDeps{}, steps)
		if !errors.Is(err, boom) || err.Error() != "wiring b: boom" || strings.Join(ran, ",") != "a,b" {
			t.Fatalf("err = %v, ran %v; want \"wiring b: boom\" after a,b", err, ran)
		}
	})

	t.Run("an afterServer error names the step that registered it", func(t *testing.T) {
		boom := errors.New("boom")
		d := &wiringDeps{}
		steps := []wiringStep{{"credits", func(d *wiringDeps) error {
			d.afterServer(func() error { return boom })
			return nil
		}}}
		if err := runWiring(d, steps); err != nil {
			t.Fatalf("runWiring: %v", err)
		}
		if err := d.startServerWiring(); !errors.Is(err, boom) || err.Error() != "wiring credits: boom" {
			t.Fatalf("err = %v, want \"wiring credits: boom\"", err)
		}
	})

	t.Run("main runs the inits before the pipeline and the rest after the server", func(t *testing.T) {
		src, err := os.ReadFile("main.go")
		if err != nil {
			t.Fatalf("read main.go: %v", err)
		}
		s := string(src)
		order := []string{
			"wiring.seedInjected(modelDBGetter)",
			"runWiring(wiring, wiringOrder)",
			"wiring.events = logFlusher.EnqueueEvent",
			"pipeline.NewEngine(plugins...)",
			"server.New(",
			"wiring.startServerWiring()",
			"SetInjectedDeps(wiring.injected())",
		}
		last := -1
		for _, needle := range order {
			if n := strings.Count(s, needle); n != 1 {
				t.Fatalf("main.go has %d calls of %s, want 1", n, needle)
			}
			at := strings.Index(s, needle)
			if at < last {
				t.Fatalf("main.go calls %s out of order; want %v", needle, order)
			}
			last = at
		}
	})
}

// TestCatalogAndCreditsWiring runs the real inits the way main does and
// checks what they wire: the credits plugin in the pipeline, the org ledger
// and catalog behind the managed admin routes, and the catalog refresh loop.
func TestCatalogAndCreditsWiring(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":[{"id":"vendor/model-a","created":1790000000,"context_length":1000,` +
			`"pricing":{"prompt":"0.000001","completion":"0.000002"}}]}`))
	}))
	defer upstream.Close()

	noDiscover := false
	cfg := config.DefaultConfig()
	cfg.Admin.Token = "wiring-admin"
	cfg.Server.Host = "127.0.0.1"
	cfg.Server.Port = freePort(t)
	cfg.Managed.MarkupPercent = 5
	cfg.Managed.RequireCredits = true
	// No Redis here: with require_redis the ledger fails closed and the
	// credits read is 503. The memory ledger is for tests.
	cfg.Managed.RequireRedis = false
	cfg.Managed.CatalogRefresh = time.Hour
	cfg.Providers["managed-or"] = config.ProviderConfig{
		BaseURL: upstream.URL, APIKey: "dummy", APIFormat: "openai",
		Type: "openrouter", Managed: true, AutoDiscover: &noDiscover,
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	catalogStore := catalog.NewStore()
	sources := server.ManagedCatalogSources(registry)
	if len(sources) != 1 {
		t.Fatalf("managed catalog sources = %v, want one", sources)
	}
	catalog.Seed(catalogStore, sources)
	if catalogStore.Load().Has("managed-or", "vendor/model-a") {
		t.Fatal("the seeded catalog already lists the upstream's model; the refresh check below would prove nothing")
	}
	keyStore := auth.NewKeyStore(cfg.Auth)

	d := &wiringDeps{cfg: cfg, registry: registry, keyStore: keyStore, catalogStore: catalogStore, catalogSources: sources}
	if err := runWiring(d, wiringOrder); err != nil {
		t.Fatalf("runWiring: %v", err)
	}
	if d.orgLedger == nil {
		t.Fatal("credits wiring left no org ledger")
	}
	var names []string
	for _, p := range d.plugins {
		names = append(names, p.Name())
	}
	// agentbudget joins credits and outcomes. It is wired without Redis too,
	// so a run budget without Redis is refused.
	if strings.Join(names, ",") != "credits,outcomes,agentbudget" {
		t.Fatalf("wired plugins = %v, want [credits outcomes agentbudget]", names)
	}

	engine := pipeline.NewEngine(d.plugins...)
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(server.BuildModelDB(nil, catalogStore))
	srv := server.New(cfg, "", registry, engine, keyStore, nil, nil, nil, &mdb, nil, nil)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	d.engine, d.server, d.handlers, d.ctx = engine, srv, srv.Handlers(), ctx
	if err := d.startServerWiring(); err != nil {
		t.Fatalf("startServerWiring: %v", err)
	}

	deadline := time.Now().Add(5 * time.Second)
	for !catalogStore.Load().Has("managed-or", "vendor/model-a") {
		if time.Now().After(deadline) {
			t.Fatal("the catalog refresh loop never loaded the upstream's catalog")
		}
		time.Sleep(10 * time.Millisecond)
	}

	served := make(chan error, 1)
	go func() { served <- srv.Start() }()
	defer func() {
		shutdownCtx, stop := context.WithTimeout(context.Background(), 5*time.Second)
		defer stop()
		_ = srv.Shutdown(shutdownCtx)
		<-served
	}()
	base := "http://" + cfg.Addr()
	for _, path := range []string{"/-/catalog", "/-/orgs/org-1/credits"} {
		code, body := getWithRetry(t, base+path, "Bearer wiring-admin")
		if code != http.StatusOK || !strings.Contains(body, `"markup_percent":5`) {
			t.Errorf("GET %s = %d %s; want 200 with the managed markup", path, code, body)
		}
	}
}

// getWithRetry GETs url, retrying while the server is still starting.
func getWithRetry(t *testing.T, url, authorization string) (int, string) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for {
		req, _ := http.NewRequest(http.MethodGet, url, nil)
		req.Header.Set("Authorization", authorization)
		resp, err := http.DefaultClient.Do(req)
		if err == nil {
			defer resp.Body.Close()
			body, _ := io.ReadAll(resp.Body)
			return resp.StatusCode, string(body)
		}
		if time.Now().After(deadline) {
			t.Fatalf("GET %s: %v", url, err)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

// TestInjectedDepsReachHandlers runs the wiring the way main does: the safe
// defaults first, a phase-1 init and a phase-2 callback each overwriting one
// injected dependency, then the hand-over to the handlers. The handlers,
// built before phase 2 ran, see the overwritten values from then on and the
// defaults for the rest.
func TestInjectedDepsReachHandlers(t *testing.T) {
	cfg := config.DefaultConfig()
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(modeldb.New(modeldb.BundledModels, nil))

	d := &wiringDeps{cfg: cfg, registry: registry}
	d.seedInjected(mdb.Load)
	if d.prices == nil || d.priced == nil || d.modelHealth != routing.NoModelHealth || d.events == nil {
		t.Fatalf("seedInjected left a dependency unset: %+v", d.injected())
	}
	wiredPrices := func(providerID, model, endpointID string) (models.PriceCard, bool) {
		return models.PriceCard{Prompt: 7, Source: models.PriceSourcePriceCard}, true
	}
	steps := []wiringStep{
		{"pricing", func(d *wiringDeps) error {
			d.priced = func(endpoint, model string) bool { return model == "wired" }
			return nil
		}},
		{"catalog", func(d *wiringDeps) error {
			d.afterServer(func() error { d.prices = wiredPrices; return nil })
			return nil
		}},
	}
	if err := runWiring(d, steps); err != nil {
		t.Fatalf("runWiring: %v", err)
	}

	engine := pipeline.NewEngine()
	srv := server.New(cfg, "", registry, engine, nil, nil, nil, nil, &mdb, nil, nil)
	h := srv.Handlers()
	if card, _ := h.Deps().Prices("openai", "gpt-4o", ""); card.Source != models.PriceSourceModelDB {
		t.Fatalf("before the hand-over the handlers price from %q, want the modeldb default", card.Source)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	d.engine, d.server, d.handlers, d.ctx = engine, srv, h, ctx
	if err := d.startServerWiring(); err != nil {
		t.Fatalf("startServerWiring: %v", err)
	}
	h.SetInjectedDeps(d.injected())

	deps := h.Deps()
	if card, ok := deps.Prices("openai", "gpt-4o", ""); !ok || card.Source != models.PriceSourcePriceCard || card.Prompt != 7 {
		t.Errorf("after the hand-over prices = %+v, want the phase-2 value", card)
	}
	if !deps.Priced("chat", "wired") || deps.Priced("chat", "gpt-4o") {
		t.Error("the phase-1 priced value did not reach the handlers")
	}
	if deps.ModelHealth != routing.NoModelHealth || deps.Events == nil {
		t.Errorf("unset dependencies lost their defaults: %+v", deps)
	}
}
