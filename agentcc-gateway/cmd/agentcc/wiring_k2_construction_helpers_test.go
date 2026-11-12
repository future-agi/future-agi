package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/catalog/connectors"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credentials"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/server"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// JSON fixtures deliberately compile on r2: nested fields must be consumed by
// production, not by a test-only adapter. The returned catalog is this test's
// private copy: it is the live catalog the wiring reads, so a test may change
// one of its records between requests (never while one is in flight).
func k2ConstructionCatalog(t *testing.T, edit func(string, map[string]any)) *manifest.Catalog {
	t.Helper()
	loaded, err := connectors.Embedded()
	if err != nil {
		t.Fatal(err)
	}
	cat := *loaded.Records
	cat.Apps = nil
	for _, original := range loaded.Records.Apps {
		app := *original
		app.Providers = nil
		for _, originalProvider := range original.Providers {
			raw, err := json.Marshal(originalProvider)
			if err != nil {
				t.Fatal(err)
			}
			var record map[string]any
			if err := json.Unmarshal(raw, &record); err != nil {
				t.Fatal(err)
			}
			if originalProvider.ID == "futureagi-rest" {
				delete(record, "clientSources")
				oauth := record["auth"].(map[string]any)["oauth2"].(map[string]any)
				sources := []string{"org"}
				if app.ID == "google-calendar" {
					sources = []string{"org", "platform"}
				}
				oauth["clientSources"] = sources
				// gwc d2fd7fd6: all twelve futureagi-rest records, gmail included, are enabled.
				record["x-gateway"] = map[string]any{"connectionEnablement": map[string]any{
					"schemaVersion": 1, "enabled": true, "operations": []string{"connect", "list_tools", "invoke"},
				}}
				record["tools"] = map[string]any{"source": "operations", "items": []any{map[string]any{"name": "echo", "inputSchema": map[string]any{"type": "object"}}}}
				if edit != nil {
					edit(app.ID, record)
				}
			}
			raw, err = json.Marshal(record)
			if err != nil {
				t.Fatal(err)
			}
			var provider manifest.Provider
			if err := json.Unmarshal(raw, &provider); err != nil {
				t.Fatal(err)
			}
			if originalProvider.ID != "futureagi-rest" {
				provider.GeneratedTools = originalProvider.GeneratedTools
			}
			app.Providers = append(app.Providers, &provider)
		}
		cat.Apps = append(cat.Apps, &app)
	}
	original := connectors.Embedded
	connectors.Embedded = func() (*connectors.Catalog, error) {
		return &connectors.Catalog{Records: &cat, Source: "synthetic REGEN-012 declarations"}, nil
	}
	t.Cleanup(func() { connectors.Embedded = original })
	return &cat
}

type k2ConstructionFixture struct {
	gateway                                                *server.Server
	srv                                                    *mcp.Server
	fetches, sharedLookups, factories, discovery, dispatch atomic.Int64
}

type k2SharedLookupSpy struct {
	mcp.SharedCredentials
	calls *atomic.Int64
}

func (s k2SharedLookupSpy) SharedAuth(org, server string) mcp.HeaderSource {
	s.calls.Add(1)
	return s.SharedCredentials.SharedAuth(org, server)
}

// All production wiring callbacks and the real tenancy.Manager remain installed.
// Only the caller, loopback token channel and upstream transport are synthetic.
func newK2ConstructionFixture(t *testing.T, runtimeSet bool, app, ref, mode, suffix string) *k2ConstructionFixture {
	t.Helper()
	f := &k2ConstructionFixture{}
	tokens := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.fetches.Add(1)
		if r.URL.Path != credentials.UpstreamTokenPath {
			http.Error(w, "bad synthetic token path", 400)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"access_token": k2BoundaryToken, "token_type": "Bearer", "expires_at": time.Now().Add(time.Hour).UTC().Format(time.RFC3339), "credential_version": 1})
	}))
	t.Cleanup(tokens.Close)
	const runtime = "http://127.0.0.1:9"
	credential := map[string]any{"mode": mode}
	if mode == "shared" {
		credential["kind"], credential["credential_id"], credential["state"] = "oauth", "synthetic-shared", "active"
	}
	raw, _ := json.Marshal(map[string]any{"meta": map[string]any{"version": 1}, "mcp": map[string]any{"enabled": true, "access": map[string]any{"default": "allow"}, "servers": map[string]any{"service": map[string]any{"url": runtime + "/" + app + "/mcp" + suffix, "catalog_ref": ref, "credential": credential}}}})
	var org tenant.OrgConfig
	if err := json.Unmarshal(raw, &org); err != nil {
		t.Fatal(err)
	}
	store := tenant.NewStore()
	store.Set("org-a", &org)
	cfg := config.DefaultConfig()
	cfg.MCP.Enabled, cfg.MCP.K2AppsEnabled, cfg.MCP.UpstreamOAuthEnabled = true, true, true
	cfg.MCP.StateKeys = []string{mcpTestStateKey}
	cfg.MCP.Django.BaseURL, cfg.MCP.Django.InternalTokenSecret = tokens.URL, k2ChannelSecret
	cfg.Admin.Token = "k2-construction-synthetic-admin"
	if runtimeSet {
		cfg.MCP.Vendors.K2RuntimeURL = runtime
	}
	reg, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(modeldb.New(modeldb.BundledModels, nil))
	f.gateway = server.New(cfg, "", reg, pipeline.NewEngine(), nil, nil, nil, nil, &mdb, store, nil)
	f.srv = f.gateway.MCP()
	t.Cleanup(f.srv.Close)
	d := &wiringDeps{cfg: cfg, server: f.gateway}
	if err := runMCPWiring(d, mcpWiringOrder); err != nil {
		t.Fatal(err)
	}
	if err := d.startServerWiring(); err != nil {
		t.Fatal(err)
	}
	manager, ok := f.srv.Tenancy().(*tenancy.Manager)
	if !ok {
		t.Fatal("production tenancy manager was replaced")
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		_ = manager.Close(ctx)
	})
	deps := *f.srv.Deps()
	deps.BearerVerifier = k2BoundaryCaller{}
	deps.SharedCredentials = k2SharedLookupSpy{deps.SharedCredentials, &f.sharedLookups}
	deps.ClientFactory = func(mcp.ClientConfig) (mcp.UpstreamClient, error) {
		f.factories.Add(1)
		return &k2ConstructionClient{fixture: f}, nil
	}
	f.srv.SetDeps(deps)
	return f
}

type k2ConstructionClient struct {
	fakeBuiltin
	fixture *k2ConstructionFixture
}

func (c *k2ConstructionClient) ListTools(context.Context) ([]json.RawMessage, error) {
	c.fixture.discovery.Add(1)
	return []json.RawMessage{json.RawMessage(`{"name":"echo","inputSchema":{"type":"object"}}`)}, nil
}
func (c *k2ConstructionClient) ListResources(context.Context) ([]json.RawMessage, error) {
	c.fixture.discovery.Add(1)
	return nil, nil
}
func (c *k2ConstructionClient) ListPrompts(context.Context) ([]json.RawMessage, error) {
	c.fixture.discovery.Add(1)
	return nil, nil
}
func (c *k2ConstructionClient) CallTool(context.Context, string, json.RawMessage, mcp.CallOptions) (json.RawMessage, error) {
	c.fixture.dispatch.Add(1)
	return json.RawMessage(`{"content":[{"type":"text","text":"synthetic success"}]}`), nil
}

func (f *k2ConstructionFixture) request(t *testing.T, session, method string, params any) *httptest.ResponseRecorder {
	t.Helper()
	raw, err := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
	if err != nil {
		t.Fatal(err)
	}
	r := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewReader(raw))
	r.Header.Set("Authorization", "Bearer k2-boundary-synthetic-caller")
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("Accept", "application/json, text/event-stream")
	if session != "" {
		r.Header.Set("MCP-Session-Id", session)
	}
	w := httptest.NewRecorder()
	f.srv.HandlePost(w, r)
	return w
}
func (f *k2ConstructionFixture) exercise(t *testing.T) {
	t.Helper()
	w := f.request(t, "", "initialize", map[string]any{"protocolVersion": mcp.ProtocolVersion, "capabilities": map[string]any{}, "clientInfo": map[string]any{"name": "k2-construction", "version": "1"}})
	session := w.Header().Get("MCP-Session-Id")
	if session == "" {
		t.Fatalf("initialize: HTTP %d", w.Code)
	}
	for _, method := range []string{"tools/list", "tools/call"} {
		params := map[string]any{}
		if method == "tools/call" {
			params["name"], params["arguments"] = "service__echo", map[string]any{}
		}
		f.request(t, session, method, params)
	}
}
