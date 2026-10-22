package server

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/capability"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	capcoreKey        = "capcore-test-key"
	capcorePrepaidKey = "capcore-test-prepaid-key"
	capcoreOrg        = "org-capcore"
)

// capcoreGateway is a gateway serving the test capability echo through the
// capability core, on two fake echo vendors (echo-a, echo-b) priced by the
// scenario's fixture cards ($0.001 a call), with the operator's managed
// credentials for both, an in-memory managed-credit ledger, and a pipeline
// of auth, cost, credits, the capability publisher and the request log.
type capcoreGateway struct {
	keys    *auth.KeyStore
	srv     *Server
	core    *capability.Core
	ledger  *billing.MemoryLedger
	vendors *capcoreVendors
	logs    *capcoreLogSink
	flusher *logging.LogFlusher
	router  *Router
}

type capcoreVendors struct {
	*httptest.Server
	mu     sync.Mutex
	status map[string]int
	calls  map[string]int
}

func newCapcoreGateway(t *testing.T, orgCfg *tenant.OrgConfig, markupBps int) *capcoreGateway {
	t.Helper()
	vendors := &capcoreVendors{status: map[string]int{}, calls: map[string]int{}}
	vendors.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		vendor := filepath.Base(filepath.Dir(r.URL.Path))
		body, _ := io.ReadAll(r.Body)
		vendors.mu.Lock()
		vendors.calls[vendor]++
		status := vendors.status[vendor]
		vendors.mu.Unlock()
		if status != 0 {
			w.WriteHeader(status)
			io.WriteString(w, `{"error":"armed"}`)
			return
		}
		w.Write(body)
	}))
	t.Cleanup(vendors.Close)
	logs := startCapcoreLogSink(t)

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "capcore-app", Key: capcoreKey, Owner: "capcore", KeyType: "byok", Metadata: map[string]string{"org_id": capcoreOrg}},
		{Name: "capcore-prepaid", Key: capcorePrepaidKey, Owner: "capcore", KeyType: "managed", CreditBalance: 1,
			Metadata: map[string]string{"org_id": capcoreOrg}},
	}
	cfg.Tools.Enabled = true
	cfg.Capabilities.PerCapability = map[string]config.CapabilityConfig{"echo": {}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	tenants.Set(capcoreOrg, orgCfg)
	keyStore := auth.NewKeyStore(cfg.Auth)

	reg := capability.NewRegistry()
	if err := capability.RegisterConfigured(reg, cfg); err != nil {
		t.Fatal(err)
	}
	ledger := billing.NewMemoryLedger()
	operator := map[string]config.ProviderConfig{
		"echo-a": {BaseURL: vendors.URL + "/echo-a", Managed: true, APIKey: "fagi-test-echo-a"},
		"echo-b": {BaseURL: vendors.URL + "/echo-b", Managed: true, APIKey: "fagi-test-echo-b"},
	}
	core := capability.NewCore(capability.Options{Registry: reg, Cards: capability.NewStaticCards(capcoreCards(t)...),
		Credentials: capability.ConfigCredentials{Operator: operator}, Ledger: ledger, MarkupBps: markupBps, ProcessingFeeBps: 320})

	flusher := logging.NewLogFlusher(logs.URL, "", time.Hour, 100)
	logPlugin := logging.New(config.RequestLoggingConfig{Enabled: true}, tenants)
	logPlugin.SetFlusher(flusher)
	engine := pipeline.NewEngine(authplugin.New(keyStore, true), costplugin.New(true, testModelDBPtr().Load, tenants, nil),
		creditsplugin.New(true, keyStore), capability.NewPlugin(), logPlugin)
	srv := New(cfg, "", registry, engine, keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.SetCapabilityCore(core)
	srv.ready.Store(true)
	router := NewRouter()
	srv.registerCapabilityEchoRoutes(router)
	return &capcoreGateway{keys: keyStore, srv: srv, core: core, ledger: ledger, vendors: vendors, logs: logs, flusher: flusher, router: router}
}

// capcoreCards are the scenario's echo fixture cards.
func capcoreCards(t *testing.T) []*capability.Card {
	t.Helper()
	var out []*capability.Card
	for _, v := range []string{"echo-a", "echo-b"} {
		raw, err := os.ReadFile(filepath.Join("..", "..", "scripts", "scenarios", "fixtures", "capability-echo", "tools", "echo", v+".json"))
		if err != nil {
			t.Fatal(err)
		}
		cards, err := capability.CardsFromJSON(raw)
		if err != nil {
			t.Fatal(err)
		}
		out = append(out, cards...)
	}
	return out
}

// capcoreOrgConfig is an org that enabled echo with vendors in order and,
// when managed is set, opted in to managed tools for echo/*.
func capcoreOrgConfig(vendors []string, managed bool) *tenant.OrgConfig {
	on := true
	cfg := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{},
		Tools: &tenant.ToolsConfig{Capabilities: map[string]*tenant.ToolsCapabilityConfig{"echo": {Enabled: &on, Providers: vendors}}}}
	if managed {
		cfg.MCP = &tenant.MCPOrgConfig{ManagedTools: &tenant.MCPManagedToolsConfig{Enabled: &on, Allow: []string{"echo/*"}}}
	}
	return cfg
}

// post sends POST /v1/capabilities/echo with request id id and the org's key.
func (g *capcoreGateway) post(t *testing.T, id, body string, headers ...string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest("POST", "/v1/capabilities/echo", bytes.NewBufferString(body))
	req = req.WithContext(models.WithRequestID(req.Context(), id))
	req.Header.Set("Authorization", "Bearer "+capcoreKey)
	req.Header.Set("Content-Type", "application/json")
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	w := httptest.NewRecorder()
	g.router.ServeHTTP(w, req)
	return w
}

func (g *capcoreGateway) grant(t *testing.T, micros int64) {
	t.Helper()
	if _, err := g.ledger.Grant(context.Background(), capcoreOrg, billing.GrantPush{GrantedTotalMicros: micros, Version: 1}); err != nil {
		t.Fatal(err)
	}
}

// capcoreLogSink is the control plane's webhook: it keeps every log entry.
type capcoreLogSink struct {
	*httptest.Server
	mu      sync.Mutex
	entries []map[string]any
}

func startCapcoreLogSink(t *testing.T) *capcoreLogSink {
	t.Helper()
	s := &capcoreLogSink{}
	s.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var batch struct {
			Logs []map[string]any `json:"logs"`
		}
		_ = json.NewDecoder(r.Body).Decode(&batch)
		s.mu.Lock()
		s.entries = append(s.entries, batch.Logs...)
		s.mu.Unlock()
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(s.Close)
	return s
}

// flushed closes the flusher, delivering every buffered entry, and returns
// the entries the sink received.
func (g *capcoreGateway) flushed(t *testing.T) []map[string]any {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	g.flusher.Close(ctx)
	g.logs.mu.Lock()
	defer g.logs.mu.Unlock()
	return append([]map[string]any(nil), g.logs.entries...)
}

// Every capability call writes its attempt and a log entry attributed to the
// org, the key, the agent, the run and the step; a failed call is logged
// with its attempt too.
func TestCapabilityAttemptAttributed(t *testing.T) {
	g := newCapcoreGateway(t, capcoreOrgConfig([]string{"echo-a", "echo-b"}, true), 500)
	g.grant(t, 100_000)
	agent := []string{"x-agentcc-agent-id", "researcher", "x-agentcc-run-id", "run-42", "x-agentcc-step", "tool"}

	w := g.post(t, "req-attr", `{"input":{"q":"hello"}}`, agent...)
	if w.Code != http.StatusOK {
		t.Fatalf("status %d: %s", w.Code, w.Body)
	}
	if w.Header().Get(capability.HeaderProvider) != "echo-a" || w.Header().Get(capability.HeaderBillingMode) != "managed" ||
		w.Header().Get("x-agentcc-latency-ms") != "" {
		t.Fatalf("headers = %v", w.Header())
	}
	var body struct {
		Data  json.RawMessage  `json:"data"`
		Usage capability.Usage `json:"usage"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if string(body.Data) != `{"q":"hello"}` || body.Usage.ChargedUSD != 0.00105 || body.Usage.Provider != "echo-a" {
		t.Fatalf("body = %s", w.Body)
	}
	raw, _ := json.Marshal(map[string]any{"usage": body.Usage, "headers": map[string]string{
		capability.HeaderProvider: w.Header().Get(capability.HeaderProvider), capability.HeaderBillingMode: w.Header().Get(capability.HeaderBillingMode)}})
	var usage any
	_ = json.Unmarshal(raw, &usage)
	if errs := schematest.Validate(t, "capability-usage-v1.schema.json", usage); len(errs) > 0 {
		t.Fatalf("usage block fails capability-usage-v1: %v", errs)
	}

	g.vendors.mu.Lock()
	g.vendors.status["echo-a"] = http.StatusBadRequest
	g.vendors.mu.Unlock()
	// No step header: a capability call is a tool step.
	if w := g.post(t, "req-attr-fail", `{"input":{"q":"bad"}}`, agent[:4]...); w.Code != http.StatusBadGateway {
		t.Fatalf("failed call status %d: %s", w.Code, w.Body)
	}

	entries := g.flushed(t)
	if len(entries) != 2 {
		t.Fatalf("%d log entries, want 2: %v", len(entries), entries)
	}
	for i, e := range entries {
		if errs := schematest.Validate(t, "log-entry-v2.schema.json", e); len(errs) > 0 {
			t.Errorf("entry %d fails log-entry-v2: %v", i, errs)
		}
		meta, _ := e["metadata"].(map[string]any)
		agentCtx, _ := e["agent"].(map[string]any)
		attempts, _ := e["attempts"].([]any)
		if meta["org_id"] != capcoreOrg || e["auth_key_id"] == nil || e["auth_key_id"] == "" ||
			agentCtx["agent_id"] != "researcher" || agentCtx["run_id"] != "run-42" || agentCtx["step"] != "tool" {
			t.Errorf("entry %d not attributed: org %v, key %v, agent %v", i, meta["org_id"], e["auth_key_id"], agentCtx)
		}
		if len(attempts) != 1 {
			t.Fatalf("entry %d has %d attempts, want 1: %v", i, len(attempts), e)
		}
		a := attempts[0].(map[string]any)
		if a["provider"] != "echo-a" || a["model"] != "echo/echo.call" || a["credential_type"] != "managed" {
			t.Errorf("entry %d attempt = %v", i, a)
		}
	}
	if entries[0]["cost"] != 0.00105 || entries[0]["cost_upstream"] != 0.001 {
		t.Errorf("served entry cost %v, upstream %v; want the billed $0.00105 and $0.001", entries[0]["cost"], entries[0]["cost_upstream"])
	}
	ok := entries[0]["attempts"].([]any)[0].(map[string]any)
	if ok["outcome"] != "success" || ok["billed_cost_micros"] != float64(1050) || entries[0]["billing_mode"] != "managed" ||
		entries[0]["cost_source"] != "price_card" || entries[0]["cost_upstream"] != 0.001 {
		t.Errorf("served entry = %v", entries[0])
	}
	if failed := entries[1]["attempts"].([]any)[0].(map[string]any); failed["outcome"] != "error" || failed["status_code"] != float64(400) {
		t.Errorf("failed attempt = %v", failed)
	}
	if source := entries[1]["agent"].(map[string]any)["step_source"]; source != "inferred" {
		t.Errorf("step_source = %v, want inferred for a call that named no step", source)
	}
}

// A managed capability call the org's credit cannot cover gets the credits
// 402, the error-402-v1 golden byte for byte: a $0.04 hold on an empty
// account.
func TestCapabilityCredits402Golden(t *testing.T) {
	g := newCapcoreGateway(t, capcoreOrgConfig([]string{"echo-a"}, true), 0)
	g.grant(t, 0)

	w := g.post(t, "req-402", `{"input":{"q":"x"},"units":40}`)

	if w.Code != http.StatusPaymentRequired || w.Header().Get("Retry-After") != "" {
		t.Fatalf("status %d, Retry-After %q: %s", w.Code, w.Header().Get("Retry-After"), w.Body)
	}
	golden, err := os.ReadFile(filepath.Join("..", "..", "..", "api_contracts", "gateway", "examples", "error-402-v1.json"))
	if err != nil {
		t.Fatal(err)
	}
	var got, want any
	if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(golden, &want); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("402 body:\n got %s\nwant %s", w.Body.Bytes(), golden)
	}
	g.vendors.mu.Lock()
	defer g.vendors.mu.Unlock()
	if g.vendors.calls["echo-a"] != 0 {
		t.Fatal("a refused call reached the vendor")
	}
}

// A call only a managed vendor could serve, with managed tools off, is 503
// managed_capability_unavailable with its reason.
func TestManagedCapabilityUnavailable503(t *testing.T) {
	org := capcoreOrgConfig([]string{"echo-a"}, true)
	off := false
	org.MCP.ManagedTools.Enabled = &off
	g := newCapcoreGateway(t, org, 500)
	g.grant(t, 100_000)

	w := g.post(t, "req-503", `{"input":{"q":"x"}}`)
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("status %d: %s", w.Code, w.Body)
	}
	var body struct {
		Error struct {
			Code     string         `json:"code"`
			Metadata map[string]any `json:"metadata"`
		} `json:"error"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body.Error.Code != "managed_capability_unavailable" || body.Error.Metadata["reason"] != "managed_tools_disabled" {
		t.Fatalf("body = %s", w.Body)
	}
	if out := g.ledger.Outbox(capcoreOrg); len(out) != 0 {
		t.Fatalf("outbox = %v", out)
	}
}

// POST /v1/capabilities/echo is 404 unless the operator configured
// capabilities.echo with tools.enabled.
func TestEchoRouteOnlyWhenConfigured(t *testing.T) {
	g := newCapcoreGateway(t, capcoreOrgConfig([]string{"echo-a"}, false), 500)
	byok := capcoreOrgConfig([]string{"echo-a"}, false)
	byok.Providers["echo-a"] = &tenant.ProviderConfig{APIKey: "org-key", Enabled: true}
	g.srv.handlers.tenantStore.Set(capcoreOrg, byok)
	if w := g.post(t, "req-on", `{"input":{"q":"x"}}`); w.Code != http.StatusOK {
		t.Fatalf("configured echo: status %d: %s", w.Code, w.Body)
	}

	for name, cfg := range map[string]func(*config.Config){
		"no capabilities.echo": func(c *config.Config) { c.Capabilities.PerCapability = nil },
		"tools off":            func(c *config.Config) { c.Tools.Enabled = false },
	} {
		t.Run(name, func(t *testing.T) {
			c := config.DefaultConfig()
			c.Tools.Enabled = true
			c.Capabilities.PerCapability = map[string]config.CapabilityConfig{"echo": {}}
			cfg(c)
			reg := capability.NewRegistry()
			if err := capability.RegisterConfigured(reg, c); err != nil {
				t.Fatal(err)
			}
			g.srv.SetCapabilityCore(capability.NewCore(capability.Options{Registry: reg}))
			if w := g.post(t, "req-off", `{"input":{"q":"x"}}`); w.Code != http.StatusNotFound {
				t.Fatalf("status %d, want 404: %s", w.Code, w.Body)
			}
		})
	}
	g.srv.SetCapabilityCore(nil)
	if w := g.post(t, "req-no-core", `{"input":{"q":"x"}}`); w.Code != http.StatusNotFound {
		t.Fatalf("no core: status %d, want 404", w.Code)
	}
}

// A capability call bills only the org's ledger: a legacy prepaid key
// (key_type managed) is never debited for it as well, while an org key's
// call carries its cost for budgets and the request log.
func TestCapabilityNeverChargesAPrepaidKey(t *testing.T) {
	g := newCapcoreGateway(t, capcoreOrgConfig([]string{"echo-a"}, true), 500)
	g.grant(t, 100_000)
	var prepaid *auth.APIKey
	for _, k := range g.keys.List() {
		if k.KeyType == "managed" {
			prepaid = k
		}
	}
	before := prepaid.BalanceMicros()

	req := httptest.NewRequest("POST", "/v1/capabilities/echo", bytes.NewBufferString(`{"input":{"q":"x"}}`))
	req = req.WithContext(models.WithRequestID(req.Context(), "req-prepaid"))
	req.Header.Set("Authorization", "Bearer "+capcorePrepaidKey)
	w := httptest.NewRecorder()
	g.router.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("status %d: %s", w.Code, w.Body)
	}
	if after := prepaid.BalanceMicros(); after != before {
		t.Fatalf("prepaid key balance %d -> %d: a capability call billed on the org ledger was charged to the key too", before, after)
	}
	acct, err := g.ledger.Account(context.Background(), capcoreOrg)
	if err != nil || acct.SpentMicros != 1050 {
		t.Fatalf("org ledger spent %d (%v), want 1050", acct.SpentMicros, err)
	}
}

// A key whose model allowlist names only chat models still calls a
// capability: the handler leaves rc.Model empty through the pre phase, so the
// auth plugin's model ACL never sees the capability's name.
func TestCapabilityNotUnderTheKeyModelAllowlist(t *testing.T) {
	g := newCapcoreGateway(t, capcoreOrgConfig([]string{"echo-a"}, true), 500)
	g.grant(t, 100_000)
	if _, err := g.keys.Update(g.keyID(t, "capcore-app"), nil, nil, []string{"gpt-4o"}, nil, nil); err != nil {
		t.Fatal(err)
	}
	if w := g.post(t, "req-acl", `{"input":{"q":"x"}}`); w.Code != http.StatusOK {
		t.Fatalf("status %d: %s, want 200 for a key whose allowlist names only chat models", w.Code, w.Body)
	}
}

// keyID is the id of the configured key named name.
func (g *capcoreGateway) keyID(t *testing.T, name string) string {
	t.Helper()
	for _, k := range g.keys.List() {
		if k.Name == name {
			return k.ID
		}
	}
	t.Fatalf("no key %s", name)
	return ""
}

// The echo route takes whole units only: a fractional count is refused
// before any vendor is called.
func TestEchoUnitsMustBeWhole(t *testing.T) {
	g := newCapcoreGateway(t, capcoreOrgConfig([]string{"echo-a"}, true), 500)
	g.grant(t, 100_000)
	for _, body := range []string{`{"input":{},"units":0.000001}`, `{"input":{},"units":-1}`} {
		if w := g.post(t, "req-units", body); w.Code != http.StatusBadRequest || !bytes.Contains(w.Body.Bytes(), []byte("invalid_units")) {
			t.Fatalf("%s: status %d: %s, want 400 invalid_units", body, w.Code, w.Body)
		}
	}
	if w := g.post(t, "req-units-ok", `{"input":{},"units":2}`); w.Code != http.StatusOK {
		t.Fatalf("whole units: status %d: %s", w.Code, w.Body)
	}
	g.vendors.mu.Lock()
	defer g.vendors.mu.Unlock()
	if n := g.vendors.calls["echo-a"]; n != 1 {
		t.Fatalf("vendor called %d times, want 1", n)
	}
}
