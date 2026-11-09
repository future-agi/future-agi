package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const optBNonactKey = "nonactivation-fixture-internal-key"

// Reuses optBMCPAdmissionServer's validated-hosted construction pattern, not
// its symbol: deployed parity deliberately carries only these new test files.
// Redis and the LLM are injected seams, not a claim to run cmd/agentcc/main.
const optBNonactBase = `auth:
  enabled: true
guardrails: {enabled: true}
server: {trusted_proxies: [192.0.2.10/32], max_request_timeout: 300s}
redis: {enabled: true, timeout: 1s}
tools: {enabled: false}
`

type optBNonactProvider struct {
	providers.Provider
	calls atomic.Int64
}

func (*optBNonactProvider) ID() string   { return "nonactivation" }
func (*optBNonactProvider) Close() error { return nil }
func (p *optBNonactProvider) ChatCompletion(_ context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	if len(req.Tools) == 0 {
		return nil, fmt.Errorf("tool-capable request missing")
	}
	p.calls.Add(1)
	var reply models.ChatCompletionResponse
	err := json.Unmarshal([]byte(`{"id":"fixture","object":"chat.completion","model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"done"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`), &reply)
	return &reply, err
}

type optBNonactFixture struct {
	s                     *Server
	up                    *httptest.Server
	connections, requests atomic.Int64
	provider              *optBNonactProvider
	base                  string
}

func optBNonactNew(t *testing.T, hosted, dormant bool) *optBNonactFixture {
	t.Helper()
	f := &optBNonactFixture{provider: &optBNonactProvider{}, base: optBNonactBase}
	upstream := mcp.NewServer(mcp.ServerConfig{})
	f.up = httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.requests.Add(1)
		switch r.Method {
		case "POST":
			upstream.HandlePost(w, r)
		case "GET":
			upstream.HandleSSE(w, r)
		case "DELETE":
			upstream.HandleDelete(w, r)
		default:
			http.NotFound(w, r)
		}
	}))
	f.up.Config.ConnState = func(_ net.Conn, state http.ConnState) {
		if state == http.StateNew {
			f.connections.Add(1)
		}
	}
	f.up.Start()
	t.Cleanup(func() { upstream.Close(); f.up.Close() })
	// Synced org keys are BYOK: give the org its own loopback LLM instead of
	// depending on an internal key's access to the global provider registry.
	llm := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req models.ChatCompletionRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "invalid fixture chat", http.StatusBadRequest)
			return
		}
		reply, err := f.provider.ChatCompletion(r.Context(), &req)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(reply)
	}))
	t.Cleanup(llm.Close)
	profile := "self-hosted"
	if hosted {
		profile = "hosted"
	}
	t.Setenv(config.EnvDeploymentProfile, profile)
	t.Setenv(config.EnvHostedOneKeyActivation, "false")
	for _, key := range []string{"AGENTCC_ADMIN_TOKEN", "AGENTCC_INTERNAL_API_KEY", "AGENTCC_WEBHOOK_SECRET"} {
		t.Setenv(key, strings.Repeat("test-admin-", 5))
	}
	t.Setenv("AGENTCC_CONTROL_PLANE_URL", "https://backend.invalid")
	t.Setenv("AGENTCC_SYNC_ON_STARTUP", "true")
	t.Setenv("AGENTCC_SYNC_INTERVAL", "60s")
	t.Setenv("AGENTCC_REDIS_ADDRESS", "redis:6379")
	t.Setenv("AGENTCC_MCP_STATE_KEYS", strings.Repeat("s", 32))
	mcpConfig := "mcp: {enabled: false}\n"
	if dormant {
		mcpConfig = fmt.Sprintf("mcp: {enabled: false, servers: {fixture: {url: %q, transport: http}}}\n", f.up.URL)
	}
	if !hosted {
		mcpConfig = "mcp: {enabled: true}\n"
	}
	f.base += mcpConfig
	path := filepath.Join(t.TempDir(), "gateway.yaml")
	optBNonactWrite(t, path, f.base)
	cfg, err := config.Load(path)
	if err != nil {
		t.Fatal("validated construction fixture refused")
	}
	if cfg.IsHosted() != hosted {
		t.Fatal("construction profile mismatch")
	}
	reg, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal("provider registry construction failed")
	}
	t.Cleanup(func() { reg.Close() })
	reg.RegisterProvider("nonactivation", f.provider, []string{"gpt-4o"})
	keys := auth.NewKeyStore(cfg.Auth, cfg.ControlPlane.SyncsKeys())
	// With control-plane sync enabled, org-bound keys must arrive through
	// sync; config keys are reserved for unscoped deployment identities.
	keys.SyncFromHashes([]auth.SyncedKey{{
		ID:       "key_nonactivation_fixture",
		Name:     "nonactivation",
		KeyHash:  auth.HashKey(optBNonactKey),
		Metadata: map[string]string{"org_id": "fixture"},
	}})
	f.s = New(cfg, path, reg, pipeline.NewEngine(authplugin.New(keys, true)), keys, nil, nil, nil, testModelDBPtr(), tenant.NewStore(), nil)
	// Inject only the LLM seam, leaving the validated hosted configuration
	// and its private-URL policy untouched. Admission still evicts this cache.
	orgProviders := providers.NewOrgProviderCache(map[string]config.ProviderConfig{
		"nonactivation": {BaseURL: llm.URL, APIFormat: "openai"},
	}, false)
	f.s.OrgProviderCache = orgProviders
	f.s.handlers.orgProviderCache = orgProviders
	f.s.orgHandlers.orgProviderCache = orgProviders
	t.Cleanup(orgProviders.EvictAll)
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		_ = f.s.Shutdown(ctx)
	})
	if dormant && len(cfg.MCP.Servers) != 1 {
		t.Fatal("dormant fixture was not loaded")
	}
	key := keys.Authenticate(optBNonactKey)
	if key == nil || keys.Identity(key).OrgID != "fixture" {
		t.Fatal("chat key is not bound to the fixture org")
	}
	return f
}

func optBNonactWrite(t *testing.T, path, body string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(body), 0600); err != nil {
		t.Fatal("fixture write failed")
	}
}

func (f *optBNonactFixture) orgBody() string {
	// managed_servers is global-only; org PUT/bulk reject unknown fields.
	return fmt.Sprintf(`{"providers":{"nonactivation":{"enabled":true,"api_key":"fixture-provider-key","models":["gpt-4o"]}},"mcp":{"enabled":true,"servers":{"fixture":{"enabled":true,"url":%q,"transport":"http"}},"virtual_servers":{"fixture":{"members":["fixture"]}}}}`, f.up.URL)
}

func (f *optBNonactFixture) admit(t *testing.T, bulk bool) {
	t.Helper()
	method, path, body := "PUT", "/-/orgs/fixture/config", f.orgBody()
	if bulk {
		method, path, body = "POST", "/-/orgs/configs/bulk", `{"fixture":`+body+`}`
	}
	w := serve(f.s, method, path, body, "Bearer "+f.s.cfg.Admin.Token)
	if w.Code != http.StatusOK {
		t.Fatal("org admission did not succeed")
	}
	f.admitted(t)
}

func (f *optBNonactFixture) admitted(t *testing.T) {
	t.Helper()
	org := f.s.TenantStore.Get("fixture")
	if org == nil || org.MCP == nil || !org.MCP.Enabled || len(org.MCP.Servers) != 1 || len(org.MCP.VirtualServers) != 1 {
		t.Fatal("MCP and virtual-server config was not retained")
	}
}

func (f *optBNonactFixture) sync(t *testing.T) {
	t.Helper()
	var fetched atomic.Int64
	cp := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" || r.URL.Path != "/agentcc/org-configs/bulk/" {
			http.NotFound(w, r)
			return
		}
		fetched.Add(1)
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"status":true,"result":{"fixture":`+f.orgBody()+`}}`)
	}))
	defer cp.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	// The same fetch/parse/MergeFetched entry used by startup and periodic ticks.
	if tenant.SyncFromControlPlane(ctx, cp.URL, f.s.cfg.Admin.Token, f.s.TenantStore) != nil {
		t.Fatal("sync tick failed")
	}
	if fetched.Load() != 1 {
		t.Fatal("sync tick did not fetch")
	}
	f.admitted(t)
}

// Enumerate the actual enabled route builder, including every subordinate
// MCP/VS route, rather than allow a newly registered path to escape a hand list.
func optBNonactRoutes(t *testing.T) []Route {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.MCP.Enabled = true
	s := &Server{cfg: cfg}
	r := NewRouter()
	s.registerMCPRoutes(r)
	t.Cleanup(func() { s.MCP().Close() })
	routes := r.Routes()
	if len(routes) < 11 {
		t.Fatal("enabled MCP route census unexpectedly empty")
	}
	return routes
}

func (f *optBNonactFixture) off(t *testing.T) {
	t.Helper()
	if !f.s.hosted || !f.s.cfg.IsHosted() {
		t.Fatal("strict-hosted profile lost")
	}
	if f.s.cfg.MCP.Enabled || len(f.s.cfg.MCP.ManagedServers) != 0 {
		t.Error("ACTIVATION_FOUND: running MCP configuration enabled")
	}
	// The static census also proves tenancy.New/SetTenancy are dominated by
	// the real main wiring's MCP()==nil return; main is not reimplemented here.
	if f.s.MCP() != nil {
		t.Error("ACTIVATION_FOUND: MCP server constructed")
	}
	for _, route := range f.s.router.Routes() {
		if strings.HasPrefix(route.Pattern, "/mcp") || strings.HasPrefix(route.Pattern, "/-/mcp") {
			t.Error("ACTIVATION_FOUND: MCP or virtual-server route registered")
		}
	}
	for _, route := range optBNonactRoutes(t) {
		if route.Pattern == "/-/reload" {
			continue
		}
		for _, actual := range f.s.router.Routes() {
			if route.Method == actual.Method && route.Pattern == actual.Pattern {
				t.Error("ACTIVATION_FOUND: enabled MCP route registered")
			}
		}
		parts := strings.Split(route.Pattern, "/")
		for i, part := range parts {
			if strings.HasPrefix(part, "{") {
				parts[i] = "fixture"
			}
		}
		w := serve(f.s, route.Method, strings.Join(parts, "/")+"?org_id=fixture", `{"org_id":"fixture","name":"fixture"}`, "Bearer "+f.s.cfg.Admin.Token)
		if w.Code != http.StatusNotFound {
			t.Error("ACTIVATION_FOUND: MCP route is not absent")
		}
	}
	before := f.provider.calls.Load()
	w := serve(f.s, "POST", "/v1/chat/completions", `{"model":"gpt-4o","messages":[{"role":"user","content":"hello"}],"tools":[{"type":"function","function":{"name":"fixture","parameters":{"type":"object"}}}]}`, "Bearer "+optBNonactKey)
	if w.Code != http.StatusOK || f.provider.calls.Load() != before+1 {
		t.Error("org tool-capable chat did not complete")
	}
	if f.connections.Load() != 0 || f.requests.Load() != 0 {
		t.Error("ACTIVATION_FOUND: MCP upstream contacted")
	}
}

func optBNonactAdmission(t *testing.T, mode string) {
	f := optBNonactNew(t, true, mode == "dormant")
	switch mode {
	case "put":
		f.admit(t, false)
	case "bulk":
		f.admit(t, true)
	case "sync":
		f.sync(t)
	case "dormant":
		f.admit(t, false)
	}
	f.off(t)
	w := serve(f.s, "POST", "/-/reload", "", "Bearer "+f.s.cfg.Admin.Token)
	if w.Code != http.StatusOK {
		t.Fatal("unchanged hosted HTTP reload did not run")
	}
	f.off(t)
	// Two ticks even for PUT/bulk/dormant; sync also receives a subsequent tick.
	for range 2 {
		f.sync(t)
		f.off(t)
	}
}

func TestOptBHostedMCPNonActivationAdminPUT(t *testing.T)         { optBNonactAdmission(t, "put") }
func TestOptBHostedMCPNonActivationAdminBulk(t *testing.T)        { optBNonactAdmission(t, "bulk") }
func TestOptBHostedMCPNonActivationControlPlaneSync(t *testing.T) { optBNonactAdmission(t, "sync") }
func TestOptBHostedMCPNonActivationDormantFile(t *testing.T)      { optBNonactAdmission(t, "dormant") }

func TestOptBHostedMCPNonActivationHTTPReload(t *testing.T) {
	for _, mode := range []string{"enabled", "managed", "both"} {
		t.Run(mode, func(t *testing.T) {
			f := optBNonactNew(t, true, false)
			f.admit(t, false)
			enabled, managed := "false", "{}"
			if mode != "managed" {
				enabled = "true"
			}
			if mode != "enabled" {
				managed = fmt.Sprintf("{fixture: {url: %q, kind: remote}}", f.up.URL)
			}
			candidate := optBNonactBase + fmt.Sprintf("mcp: {enabled: %s, managed_servers: %s}\n", enabled, managed)
			optBNonactWrite(t, f.s.configPath, candidate)
			before := f.s.cfg
			w := serve(f.s, "POST", "/-/reload", "", "Bearer "+f.s.cfg.Admin.Token)
			if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "MCP disabled") {
				t.Error("hosted MCP reload not explicitly refused")
			}
			if f.s.cfg != before {
				t.Error("ACTIVATION_FOUND: refused MCP reload replaced running configuration")
			}
			f.off(t)
			for range 2 {
				f.sync(t)
				f.off(t)
			}
		})
	}
}

func TestOptBSelfHostedMCPActivationControl(t *testing.T) {
	f := optBNonactNew(t, false, false)
	f.admit(t, false) // Identical org MCP+VS payload to the strict-hosted arm.
	m := f.s.MCP()
	if m == nil {
		t.Fatal("self-hosted MCP control missing")
	}
	mgr := tenancy.New(tenancy.Options{MCP: f.s.cfg.MCP, Store: f.s.TenantStore, Deps: m.Deps, Adopt: m.StaticClients, Publish: m.SetStaticClient})
	m.SetTenancy(mgr)
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		_ = mgr.Close(ctx)
	})
	w := serve(f.s, "GET", "/-/mcp/status?org_id=fixture", "", "Bearer "+f.s.cfg.Admin.Token)
	if w.Code != http.StatusOK {
		t.Fatal("self-hosted MCP route did not answer")
	}
	init := `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"fixture","version":"1"}}}`
	protocol := serve(f.s, "POST", "/mcp", init, "Bearer "+optBNonactKey)
	if protocol.Code != http.StatusOK || protocol.Header().Get("MCP-Session-Id") == "" {
		t.Fatal("self-hosted MCP protocol control did not answer")
	}
	if f.connections.Load() < 1 || f.requests.Load() < 1 {
		t.Fatal("activation control failed to contact upstream")
	}
	_, pool, err := mgr.Resolve("fixture")
	if err != nil || pool == nil || len(pool.Servers()) != 1 {
		t.Fatal("activation control did not resolve the org pool")
	}
}
