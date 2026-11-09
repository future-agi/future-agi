package main

// Integrator tests added at the W3b merge (X6-W3b); each names the
// integrator request it proves.

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
	"github.com/futureagi/agentcc-gateway/internal/capabilities"
	"github.com/futureagi/agentcc-gateway/internal/capability"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/server"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// w3bMockUpstream is an agentcc-mcpmock on loopback that counts the requests
// it received.
func w3bMockUpstream(t *testing.T) (*httptest.Server, *atomic.Int32) {
	t.Helper()
	fix, err := mcpmock.LoadFixtures("../../scripts/scenarios/fixtures/mcp/common")
	if err != nil {
		t.Fatal(err)
	}
	era, err := mcpmock.ParseEra("dual")
	if err != nil {
		t.Fatal(err)
	}
	m, err := mcpmock.New(mcpmock.Config{Era: era, Auth: "none", Path: "/mcp", ServerName: "w3b-egress"}, fix)
	if err != nil {
		t.Fatal(err)
	}
	var hits atomic.Int32
	h := m.Handler()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, "/__") {
			hits.Add(1)
		}
		h.ServeHTTP(w, r)
	}))
	t.Cleanup(srv.Close)
	m.SetPublicURL(srv.URL)
	return srv, &hits
}

// w3bWiredGateway is a gateway with the whole MCP wiring (mcpWiringOrder)
// over a tenant store the test writes, its egress allowlist set.
func w3bWiredGateway(t *testing.T, privateAllow []string) (*server.Server, *tenant.Store) {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.MCP.Enabled = true
	cfg.MCP.Egress.PrivateAllow = privateAllow
	cfg.MCP.StateKeys = []string{mcpTestStateKey} // config load reads it from AGENTCC_MCP_STATE_KEYS
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(modeldb.New(modeldb.BundledModels, nil))
	store := tenant.NewStore()
	srv := server.New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, &mdb, store, nil)
	t.Cleanup(func() { srv.MCP().Close() })
	d := &wiringDeps{cfg: cfg, server: srv}
	if err := runMCPWiring(d, mcpWiringOrder); err != nil {
		t.Fatal(err)
	}
	if err := d.startServerWiring(); err != nil {
		t.Fatal(err)
	}
	return srv, store
}

// TestMCPOrgPoolDialsThroughTheEgressGuard (M9 IR-13, with M13 and M17): on
// the whole MCP wiring, an org's pushed server is built by M13's factory and
// dials through M17's egress guard (decision 39). A server on a loopback port
// that mcp.egress.private_allow does not list is refused at dial time: the
// upstream sees no request and the pool reports the refusal. The same port
// listed connects, and its tools are the pool's. Negative control (recorded at
// the merge): with the upstream step's SetDialerSource removed, the first case
// connects to the loopback upstream.
func TestMCPOrgPoolDialsThroughTheEgressGuard(t *testing.T) {
	up, hits := w3bMockUpstream(t)
	u, err := url.Parse(up.URL)
	if err != nil {
		t.Fatal(err)
	}
	push := func(store *tenant.Store) {
		var oc tenant.OrgConfig
		raw := `{"mcp":{"enabled":true,"servers":{"github":{"url":"` + up.URL + `/mcp"}}}}`
		if err := json.Unmarshal([]byte(raw), &oc); err != nil {
			t.Fatal(err)
		}
		store.Set("org-a", &oc)
	}

	srv, store := w3bWiredGateway(t, nil)
	push(store)
	_, pool, err := srv.MCP().Tenancy().Resolve("org-a")
	if err != nil {
		t.Fatal(err)
	}
	ps, ok := pool.Server("github")
	if !ok || ps.Healthy || !strings.Contains(ps.LastError, "egress") || len(ps.Tools) != 0 {
		t.Fatalf("an unlisted loopback upstream: healthy %v, last_error %q, %d tools; want it refused by the egress guard",
			ps.Healthy, ps.LastError, len(ps.Tools))
	}
	if n := hits.Load(); n != 0 {
		t.Fatalf("the refused upstream received %d requests", n)
	}

	srv2, store2 := w3bWiredGateway(t, []string{u.Host})
	push(store2)
	_, pool2, err := srv2.MCP().Tenancy().Resolve("org-a")
	if err != nil {
		t.Fatal(err)
	}
	if ps, ok := pool2.Server("github"); !ok || !ps.Healthy || len(ps.Tools) == 0 {
		t.Fatalf("the listed loopback upstream: healthy %v, last_error %q, %d tools; want it connected with its tools",
			ps.Healthy, ps.LastError, len(ps.Tools))
	}
	if hits.Load() == 0 {
		t.Fatal("the listed upstream received no request")
	}
}

// TestIC10WiredGatewayRefusesManagedByokOnlyGlob (IC-10, with M9's check): on
// a gateway wired with tools.enabled, a PUT /-/orgs/{id}/config whose
// mcp.managed_tools allows a byok_only vendor (brave on the seed cards) is 400
// capability_managed_not_allowed and stores nothing; with tools off no catalog
// is set and the same config is accepted. Red without IC-10's wiring line:
// the tools-enabled PUT is 200.
func TestIC10WiredGatewayRefusesManagedByokOnlyGlob(t *testing.T) {
	const token = "ic10-admin-token-0123456789abcdef0123"
	defer capabilities.SetToolSource(nil)()
	prev := capability.Installed()
	t.Cleanup(func() { capability.Install(prev) })
	for _, tools := range []bool{true, false} {
		cfg := config.DefaultConfig()
		cfg.Tools.Enabled = tools
		cfg.Admin.Token = token
		registry, err := providers.NewRegistry(cfg)
		if err != nil {
			t.Fatal(err)
		}
		var mdb atomic.Pointer[modeldb.ModelDB]
		mdb.Store(modeldb.New(modeldb.BundledModels, nil))
		store := tenant.NewStore()
		srv := server.New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, &mdb, store, nil)
		d := &wiringDeps{cfg: cfg, server: srv}
		if err := initCapability(d); err != nil {
			t.Fatalf("tools %v: initCapability: %v", tools, err)
		}
		if err := d.startServerWiring(); err != nil {
			t.Fatal(err)
		}
		body := `{"mcp":{"enabled":true,"managed_tools":{"enabled":true,"allow":["search/brave"]}}}`
		req := httptest.NewRequest(http.MethodPut, "/-/orgs/org-a/config?org_id=org-a", strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		srv.OrgConfigHandlers().SetOrgConfig(w, req)
		stored := store.Get("org-a") != nil
		switch {
		case tools && (w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "capability_managed_not_allowed") || stored):
			t.Fatalf("tools on: PUT = %d %s (stored %v), want 400 capability_managed_not_allowed and nothing stored", w.Code, w.Body.String(), stored)
		case !tools && (w.Code != http.StatusOK || !stored):
			t.Fatalf("tools off: PUT = %d %s (stored %v), want 200 (no catalog, no check)", w.Code, w.Body.String(), stored)
		}
	}
}
