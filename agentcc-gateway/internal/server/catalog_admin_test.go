package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const catalogTestModel = "anthropic/claude-sonnet-5.5"

// catalogUpstream is an OpenRouter-shaped catalog whose prices a test can
// change between refreshes.
type catalogUpstream struct {
	*httptest.Server
	prompt atomic.Value // the claude model's prompt price, a decimal string
}

func newCatalogUpstream(t *testing.T) *catalogUpstream {
	u := &catalogUpstream{}
	u.prompt.Store("0.000003")
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/models":
			fmt.Fprintf(w, `{"data":[{"id":%q,"name":"Claude Sonnet 5.5","created":1790000000,"context_length":1000000,`+
				`"architecture":{"input_modalities":["text","image"],"output_modalities":["text"],"tokenizer":"Claude"},`+
				`"pricing":{"prompt":%q,"completion":"0.000015","input_cache_write":"0.00000375"},`+
				`"top_provider":{"max_completion_tokens":64000},"supported_parameters":["tools"]},`+
				`{"id":"vendor/other","created":1790000000,"pricing":{"prompt":"0.000001","completion":"0.000002"}}]}`,
				catalogTestModel, u.prompt.Load())
		case "/api/v1/endpoints/zdr":
			fmt.Fprintf(w, `{"data":[{"model_id":%q,"tag":"anthropic"}]}`, catalogTestModel)
		case "/api/v1/models/" + catalogTestModel + "/endpoints":
			fmt.Fprintf(w, `{"data":{"id":%q,"name":"Claude Sonnet 5.5","endpoints":[`+
				`{"name":"Anthropic | %s","provider_name":"Anthropic","tag":"anthropic","quantization":"unknown","context_length":1000000,`+
				`"pricing":{"prompt":%q,"completion":"0.000015","input_cache_write":"0.00000375","discount":0},`+
				`"max_completion_tokens":64000,"supported_parameters":["tools"],"status":0,"uptime_last_30m":99.5,`+
				`"latency_last_30m":{"p50":640,"p90":1510},"throughput_last_30m":{"p50":70}},`+
				`{"name":"Google | %s","provider_name":"Google","tag":"google-vertex/europe","quantization":null,"context_length":1000000,`+
				`"pricing":{"prompt":"0.0000033","completion":"0.0000165"},"max_completion_tokens":null,"supported_parameters":[],"status":-2}]}}`,
				catalogTestModel, catalogTestModel, u.prompt.Load(), catalogTestModel)
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(u.Close)
	return u
}

// catalogClock is a clock the test moves by hand.
type catalogClock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *catalogClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *catalogClock) Advance(d time.Duration) {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
}

type catalogAdminGateway struct {
	srv      *Server
	store    *catalog.Store
	upstream *catalogUpstream
	clock    *catalogClock
}

// newCatalogAdminGateway is a gateway whose managed OpenRouter provider's
// catalog is served by a test upstream, with a running refresher, the
// store configured with dir as its snapshot directory.
func newCatalogAdminGateway(t *testing.T, dir string) *catalogAdminGateway {
	t.Helper()
	up := newCatalogUpstream(t)
	off := false
	cfg := config.DefaultConfig()
	cfg.Admin.Token = adminToken
	cfg.Catalog.SnapshotDir = dir
	cfg.Providers["openrouter"] = config.ProviderConfig{
		BaseURL: up.URL + "/api", APIKey: "fagi-openrouter-key", APIFormat: "openai",
		Type: "openrouter", Managed: true, AutoDiscover: &off,
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	clock := &catalogClock{t: time.Date(2026, 10, 15, 12, 0, 0, 0, time.UTC)}
	sources := ManagedCatalogSources(registry)
	opts := catalog.OptionsFromConfig(cfg, sources)
	opts.Now = clock.Now
	store := catalog.NewStore()
	if err := store.Configure(opts); err != nil {
		t.Fatal(err)
	}
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(BuildModelDB(nil, store))
	keyStore := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(), keyStore, nil, nil, nil, &mdb, tenant.NewStore(), nil)
	srv.EnableManaged(ManagedSettings{Catalog: store, Ledger: creditsplugin.NewOrgLedger(nil), MarkupPercent: 5})
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	catalog.NewRefresher(ctx, store, sources, srv.RefreshModelDB)
	srv.ready.Store(true)
	return &catalogAdminGateway{srv: srv, store: store, upstream: up, clock: clock}
}

func (g *catalogAdminGateway) admin(method, path, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, bytes.NewBufferString(body))
	req.Header.Set("Authorization", "Bearer "+adminToken)
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// refreshAndWait asks for a refresh and waits for the history to reach n.
func (g *catalogAdminGateway) refreshAndWait(t *testing.T, n int) map[string]any {
	t.Helper()
	w := g.admin("POST", "/-/catalog/refresh", "")
	if w.Code != http.StatusAccepted {
		t.Fatalf("refresh = %d %s", w.Code, w.Body)
	}
	deadline := time.Now().Add(5 * time.Second)
	for len(g.store.History(0)) < n {
		if time.Now().After(deadline) {
			t.Fatalf("history never reached %d entries", n)
		}
		time.Sleep(5 * time.Millisecond)
	}
	return decode(t, w)
}

func decode(t *testing.T, w *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var v map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &v); err != nil {
		t.Fatalf("body %q: %v", w.Body, err)
	}
	return v
}

func TestCatalogAdminRoutes(t *testing.T) {
	g := newCatalogAdminGateway(t, t.TempDir())
	seed := g.store.Load().ID()

	routes := [][2]string{{"GET", "/-/catalog"}, {"GET", "/-/catalog/endpoints?model=x"}, {"GET", "/-/catalog/stats"},
		{"GET", "/-/catalog/history"}, {"GET", "/-/catalog/snapshots/" + seed}, {"POST", "/-/catalog/refresh"},
		{"PUT", "/-/catalog/blocks"}}
	for _, r := range routes {
		req := httptest.NewRequest(r[0], r[1], nil)
		w := httptest.NewRecorder()
		g.srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != http.StatusUnauthorized {
			t.Errorf("%s %s without the admin token = %d", r[0], r[1], w.Code)
		}
	}

	// Refresh: 202 with the version it starts from; the history gains the
	// live version.
	accepted := g.refreshAndWait(t, 1)
	if accepted["snapshot_id"] != seed || fmt.Sprint(accepted["providers"]) != "[openrouter]" {
		t.Fatalf("refresh accepted = %v", accepted)
	}
	live := g.store.Load().ID()

	// The model list carries the version, freshness, states and counts.
	cat := decode(t, g.admin("GET", "/-/catalog", ""))
	if cat["snapshot_id"] != live || cat["stale"] != false || cat["markup_percent"] != 5.0 {
		t.Fatalf("catalog head = id %v stale %v markup %v", cat["snapshot_id"], cat["stale"], cat["markup_percent"])
	}
	modelsList := cat["models"].([]any)
	first := modelsList[0].(map[string]any)
	if first["id"] != catalogTestModel || first["state"] != "active" || first["endpoints_count"] != 0.0 {
		t.Fatalf("model view = %v", first)
	}
	if _, ok := first["endpoints"]; ok {
		t.Fatal("the model list must not inline endpoints; they have their own route")
	}
	if p := first["prices"].(map[string]any); p["cache_write"] != 3.75e-6 {
		t.Fatalf("model prices = %v, want the full card", p)
	}

	// Endpoints: the catalog-endpoints-v1 shape, read from the upstream.
	w := g.admin("GET", "/-/catalog/endpoints?model="+catalogTestModel, "")
	if w.Code != http.StatusOK {
		t.Fatalf("endpoints = %d %s", w.Code, w.Body)
	}
	validateAgainstSchema(t, w.Body.Bytes(), "catalog-endpoints-v1.schema.json")
	doc := decode(t, w)["data"].(map[string]any)
	eps := doc["endpoints"].([]any)
	direct := eps[0].(map[string]any)
	if len(eps) != 2 || direct["upstream_fee_bps"] != 550.0 || direct["source"] != "openrouter" || direct["zdr"] != true ||
		direct["data_collection"] != "deny" || direct["pricing"].(map[string]any)["input_cache_write"] != "0.00000375" {
		t.Fatalf("endpoints = %v", eps)
	}
	if r := eps[1].(map[string]any)["region"]; r != "europe" {
		t.Fatalf("vertex region = %v", r)
	}
	if w := g.admin("GET", "/-/catalog/endpoints", ""); w.Code != http.StatusBadRequest {
		t.Fatalf("endpoints without a model = %d", w.Code)
	}
	if w := g.admin("GET", "/-/catalog/endpoints?model=no/such", ""); w.Code != http.StatusNotFound {
		t.Fatalf("endpoints of an unlisted model = %d", w.Code)
	}

	// Stats count the endpoints the read added.
	stats := decode(t, g.admin("GET", "/-/catalog/stats", ""))
	perSource := stats["per_source"].(map[string]any)["openrouter"].(map[string]any)
	if stats["endpoints"] != 2.0 || stats["priced_endpoints"] != 2.0 || stats["canonical_models"] != 2.0 ||
		perSource["models"] != 2.0 || stats["snapshot_id"] != g.store.Load().ID() {
		t.Fatalf("stats = %v", stats)
	}

	// History: newest first, limited, each with a diff.
	hist := decode(t, g.admin("GET", "/-/catalog/history?limit=1", ""))["data"].([]any)
	if len(hist) != 1 || hist[0].(map[string]any)["source"] != "endpoints" {
		t.Fatalf("history?limit=1 = %v", hist)
	}
	all := decode(t, g.admin("GET", "/-/catalog/history", ""))["data"].([]any)
	if len(all) != 2 || all[1].(map[string]any)["id"] != live {
		t.Fatalf("history = %v", all)
	}
	if added := all[1].(map[string]any)["diff"].(map[string]any)["added"]; fmt.Sprint(added) != "["+catalogTestModel+" vendor/other]" {
		t.Fatalf("first live diff added = %v", added)
	}
	for _, bad := range []string{"0", "-2", "x"} {
		if w := g.admin("GET", "/-/catalog/history?limit="+bad, ""); w.Code != http.StatusBadRequest {
			t.Errorf("history?limit=%s = %d", bad, w.Code)
		}
	}

	// Snapshots: a kept version's full content; anything else is 404.
	snap := decode(t, g.admin("GET", "/-/catalog/snapshots/"+live, ""))
	if snap["id"] != live || len(snap["providers"].([]any)) != 1 {
		t.Fatalf("snapshot = %v", snap)
	}
	for _, id := range []string{"000000000000", "..%2F..%2Fetc", "abc"} {
		if w := g.admin("GET", "/-/catalog/snapshots/"+id, ""); w.Code != http.StatusNotFound {
			t.Errorf("snapshot %s = %d", id, w.Code)
		}
	}
}

func TestCatalogAdminRoutesNeedManagedModels(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Admin.Token = adminToken
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	for _, r := range [][2]string{{"GET", "/-/catalog/stats"}, {"GET", "/-/catalog/endpoints?model=x"},
		{"GET", "/-/catalog/history"}, {"POST", "/-/catalog/refresh"}, {"PUT", "/-/catalog/blocks"}} {
		req := httptest.NewRequest(r[0], r[1], nil)
		req.Header.Set("Authorization", "Bearer "+adminToken)
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != http.StatusNotFound || !strings.Contains(w.Body.String(), "managed_models_disabled") {
			t.Errorf("%s %s without managed models = %d %s", r[0], r[1], w.Code, w.Body)
		}
	}
}

func TestCatalogRefreshRateLimited(t *testing.T) {
	g := newCatalogAdminGateway(t, "")
	g.refreshAndWait(t, 1)

	g.clock.Advance(15 * time.Second)
	w := g.admin("POST", "/-/catalog/refresh", "")
	if w.Code != http.StatusTooManyRequests || w.Header().Get("Retry-After") != "45" ||
		!strings.Contains(w.Body.String(), `"code":"catalog_refresh_rate_limited"`) {
		t.Fatalf("second refresh in the minute = %d %q %s", w.Code, w.Header().Get("Retry-After"), w.Body)
	}
	if w := g.admin("POST", "/-/catalog/refresh?provider=openrouter", ""); w.Code != http.StatusTooManyRequests {
		t.Fatalf("naming the provider must not bypass the limit: %d", w.Code)
	}

	// A minute after the first, a refresh with one price changed runs, and
	// its history entry names the model.
	g.clock.Advance(46 * time.Second)
	g.upstream.prompt.Store("0.0000031")
	g.refreshAndWait(t, 2)
	newest := g.store.History(1)[0]
	if fmt.Sprint(newest.Diff.PriceChanged) != "["+catalogTestModel+"]" {
		t.Fatalf("diff = %+v, want the repriced model", newest.Diff)
	}
	if card, _ := g.store.PriceCard("openrouter", catalogTestModel, ""); card.Prompt != 3.1e-6 {
		t.Fatalf("price after refresh = %v", card.Prompt)
	}
	// The model database is rebuilt after the history entry is written, so
	// a slow (-race, loaded) run polls for it.
	for deadline := time.Now().Add(5 * time.Second); ; time.Sleep(5 * time.Millisecond) {
		info, ok := g.srv.Handlers().ModelDB().Get("openrouter/" + catalogTestModel)
		if ok && info.Pricing.InputPerToken == 3.1e-6 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("a refresh that changes prices must rebuild the model database")
		}
	}

	if w := g.admin("POST", "/-/catalog/refresh?provider=nope", ""); w.Code != http.StatusNotFound {
		t.Fatalf("refresh of an unknown provider = %d", w.Code)
	}
}

func TestCatalogBlocksPersist(t *testing.T) {
	dir := t.TempDir()
	g := newCatalogAdminGateway(t, dir)
	g.refreshAndWait(t, 1)
	id := g.store.Load().ID()

	w := g.admin("PUT", "/-/catalog/blocks", `{"models":["`+catalogTestModel+`"],"reason":"resale terms pending"}`)
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), `"source":"runtime"`) {
		t.Fatalf("PUT blocks = %d %s", w.Code, w.Body)
	}
	state := func(g *catalogAdminGateway) (string, string) {
		for _, m := range decode(t, g.admin("GET", "/-/catalog", ""))["models"].([]any) {
			if mm := m.(map[string]any); mm["id"] == catalogTestModel {
				reason, _ := mm["state_reason"].(string)
				return mm["state"].(string), reason
			}
		}
		t.Fatal("model missing from the catalog")
		return "", ""
	}
	if s, r := state(g); s != "blocked" || r != "resale terms pending" {
		t.Fatalf("after the block: %s (%s)", s, r)
	}
	if g.store.Load().ID() != id || len(g.store.History(0)) != 1 {
		t.Fatal("a block is a state, not a new catalog version")
	}
	if _, err := os.Stat(filepath.Join(dir, "blocks.json")); err != nil {
		t.Fatalf("blocks not persisted: %v", err)
	}

	// A restarted gateway on the same snapshot directory still blocks it.
	restarted := newCatalogAdminGateway(t, dir)
	if s, _ := state(restarted); s != "blocked" {
		t.Fatalf("after a restart the model is %s", s)
	}

	for _, bad := range []string{``, `{"reason":"x"}`, `{"models":"x"}`, `not json`} {
		if w := g.admin("PUT", "/-/catalog/blocks", bad); w.Code != http.StatusBadRequest {
			t.Errorf("PUT blocks %q = %d", bad, w.Code)
		}
	}
	if w := g.admin("PUT", "/-/catalog/blocks", `{"models":[]}`); w.Code != http.StatusOK {
		t.Fatalf("clearing blocks = %d", w.Code)
	}
	if s, _ := state(g); s != "active" {
		t.Fatalf("after clearing: %s", s)
	}
}

// --- a subset of JSON Schema: enough for the contract goldens --------------

// validateAgainstSchema checks body against api_contracts/gateway/examples/
// <name>: type (with null), object properties, required and
// additionalProperties false, array items, local $ref, pattern, enum and
// minimum. An unsupported keyword fails the test rather than pass silently.
func validateAgainstSchema(t *testing.T, body []byte, name string) {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "api_contracts", "gateway", "examples", name))
	if err != nil {
		t.Fatal(err)
	}
	var schema map[string]any
	if err := json.Unmarshal(raw, &schema); err != nil {
		t.Fatal(err)
	}
	var v any
	if err := json.Unmarshal(body, &v); err != nil {
		t.Fatal(err)
	}
	for _, problem := range checkSchema(schema, schema, v, "$") {
		t.Errorf("%s: %s", name, problem)
	}
}

var schemaKeywords = map[string]bool{"$schema": true, "$id": true, "title": true, "description": true, "$defs": true,
	"type": true, "properties": true, "required": true, "additionalProperties": true, "items": true, "$ref": true,
	"pattern": true, "enum": true, "minimum": true, "minLength": true}

func checkSchema(root, s map[string]any, v any, at string) []string {
	for k := range s {
		if !schemaKeywords[k] {
			return []string{at + ": schema keyword " + k + " is not supported by this validator"}
		}
	}
	if ref, ok := s["$ref"].(string); ok {
		def := root["$defs"].(map[string]any)[strings.TrimPrefix(ref, "#/$defs/")].(map[string]any)
		return checkSchema(root, def, v, at)
	}
	var problems []string
	if typ, ok := s["type"]; ok && !typeMatches(typ, v) {
		return []string{fmt.Sprintf("%s: %v is not of type %v", at, v, typ)}
	}
	switch x := v.(type) {
	case map[string]any:
		props, _ := s["properties"].(map[string]any)
		for _, r := range asList(s["required"]) {
			if _, ok := x[r.(string)]; !ok {
				problems = append(problems, at+": missing required "+r.(string))
			}
		}
		for k, val := range x {
			ps, ok := props[k].(map[string]any)
			if !ok {
				if s["additionalProperties"] == false {
					problems = append(problems, at+": property "+k+" is not allowed")
				}
				continue
			}
			problems = append(problems, checkSchema(root, ps, val, at+"."+k)...)
		}
	case []any:
		if items, ok := s["items"].(map[string]any); ok {
			for i, item := range x {
				problems = append(problems, checkSchema(root, items, item, fmt.Sprintf("%s[%d]", at, i))...)
			}
		}
	case string:
		if p, ok := s["pattern"].(string); ok && !regexp.MustCompile(p).MatchString(x) {
			problems = append(problems, fmt.Sprintf("%s: %q does not match %s", at, x, p))
		}
		if min, ok := s["minLength"].(float64); ok && float64(len(x)) < min {
			problems = append(problems, at+": too short")
		}
	case float64:
		if min, ok := s["minimum"].(float64); ok && x < min {
			problems = append(problems, fmt.Sprintf("%s: %v is below %v", at, x, min))
		}
	}
	if enum, ok := s["enum"].([]any); ok {
		found := false
		for _, e := range enum {
			found = found || e == v
		}
		if !found {
			problems = append(problems, fmt.Sprintf("%s: %v is not one of %v", at, v, enum))
		}
	}
	return problems
}

func asList(v any) []any {
	l, _ := v.([]any)
	return l
}

func typeMatches(typ, v any) bool {
	for _, t := range append(asList(typ), typ) {
		switch t {
		case "object":
			if _, ok := v.(map[string]any); ok {
				return true
			}
		case "array":
			if _, ok := v.([]any); ok {
				return true
			}
		case "string":
			if _, ok := v.(string); ok {
				return true
			}
		case "integer":
			if f, ok := v.(float64); ok && f == float64(int64(f)) {
				return true
			}
		case "number":
			if _, ok := v.(float64); ok {
				return true
			}
		case "boolean":
			if _, ok := v.(bool); ok {
				return true
			}
		case "null":
			if v == nil {
				return true
			}
		}
	}
	return false
}
