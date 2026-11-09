package main

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/server"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// tenancyClient is an upstream client that records its Close.
type tenancyClient struct{ closed atomic.Bool }

func (c *tenancyClient) ListTools(context.Context) ([]json.RawMessage, error) {
	return []json.RawMessage{json.RawMessage(`{"name":"search","inputSchema":{"type":"object"}}`)}, nil
}
func (c *tenancyClient) CallTool(context.Context, string, json.RawMessage, mcp.CallOptions) (json.RawMessage, error) {
	return nil, errors.New("unused")
}
func (c *tenancyClient) ListResources(context.Context) ([]json.RawMessage, error) { return nil, nil }
func (c *tenancyClient) ReadResource(context.Context, string, mcp.CallOptions) (json.RawMessage, error) {
	return nil, errors.New("unused")
}
func (c *tenancyClient) ListPrompts(context.Context) ([]json.RawMessage, error) { return nil, nil }
func (c *tenancyClient) GetPrompt(context.Context, string, json.RawMessage, mcp.CallOptions) (json.RawMessage, error) {
	return nil, errors.New("unused")
}
func (c *tenancyClient) Healthy() bool { return !c.closed.Load() }
func (c *tenancyClient) Close() error  { c.closed.Store(true); return nil }

// The tenancy step installs a manager over the server's tenant store that
// builds through the factory SetDeps hands over after it, and closes the
// pools once shutdown begins.
func TestMCPTenancyWiring(t *testing.T) {
	t.Setenv("AGENTCC_MCP_STATE_KEYS", mcpTestStateKey)
	cfg := config.DefaultConfig()
	cfg.MCP.Enabled = true
	cfg.Server.ShutdownTimeout = 5 * time.Second
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(modeldb.New(modeldb.BundledModels, nil))
	store := tenant.NewStore()
	srv := server.New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, &mdb, store, nil)
	t.Cleanup(func() { srv.MCP().Close() })

	client := &tenancyClient{}
	builds := 0
	built := map[string]mcp.ClientConfig{}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	d := &wiringDeps{cfg: cfg, server: srv, ctx: ctx}
	if err := initConnectors(d); err != nil { // the step before mcp: the connector catalog
		t.Fatal(err)
	}
	steps := []mcpWiringStep{
		{"tenancy", initMCPTenancy},
		{"upstream", func(_ *wiringDeps, deps *mcp.Deps) error { // a later step's factory
			deps.ClientFactory = func(c mcp.ClientConfig) (mcp.UpstreamClient, error) {
				builds++
				built[c.URL] = c
				return client, nil
			}
			return nil
		}},
	}
	if err := runMCPWiring(d, steps); err != nil {
		t.Fatal(err)
	}
	if err := d.startServerWiring(); err != nil {
		t.Fatal(err)
	}
	ten := srv.MCP().Tenancy()
	if ten == nil {
		t.Fatal("no tenancy installed")
	}
	var oc tenant.OrgConfig
	if err := json.Unmarshal([]byte(`{"mcp":{"enabled":true,"servers":{"github":{"url":"https://a.example"}}}}`), &oc); err != nil {
		t.Fatal(err)
	}
	store.Set("org-a", &oc)
	_, pool, err := ten.Resolve("org-a")
	if err != nil {
		t.Fatal(err)
	}
	if s, ok := pool.Server("github"); !ok || !s.Healthy || builds != 1 {
		t.Fatalf("github %+v, builds %d: the pool did not build through the later step's factory", s, builds)
	}

	// The pool places a catalog_ref server by the connector catalog the
	// connectors step loaded (IR-CN7-3): Close's key with the constant
	// Close-Scope from the embedded catalog.
	var cc tenant.OrgConfig
	if err := json.Unmarshal([]byte(`{"mcp":{"enabled":true,"servers":{"close":{"url":"https://close.example/mcp",`+
		`"catalog_ref":"close/api-key@1.0.0","credential":{"mode":"shared","secret":{"type":"api_key","in":"header",`+
		`"name":"Close-API-Key","value":"test-close-key-not-real"}}}}}}`), &cc); err != nil {
		t.Fatal(err)
	}
	store.Set("org-close", &cc)
	if _, _, err := ten.Resolve("org-close"); err != nil {
		t.Fatalf("a catalog_ref server with the connectors step's catalog: %v", err)
	}
	deadline := time.Now().Add(3 * time.Second)
	for built["https://close.example/mcp"].Auth.Template == nil {
		if time.Now().After(deadline) {
			t.Fatal("the close server was not built with a credential template")
		}
		time.Sleep(5 * time.Millisecond)
	}
	if got := strings.Join(built["https://close.example/mcp"].Auth.Template.HeaderNames(), ","); got != "Close-Api-Key,Close-Scope" {
		t.Fatalf("close template headers %q, want the key and the catalog's Close-Scope", got)
	}

	cancel() // shutdown begins
	deadline = time.Now().Add(3 * time.Second)
	for !client.closed.Load() {
		if time.Now().After(deadline) {
			t.Fatal("the org pool's client was not closed at shutdown")
		}
		time.Sleep(5 * time.Millisecond)
	}

	// MCP disabled: nothing installed.
	off := newWiringServer(t, false)
	d2 := &wiringDeps{server: off}
	if err := initMCPTenancy(d2, &mcp.Deps{}); err != nil || d2.startServerWiring() != nil {
		t.Fatal("MCP disabled: the tenancy step failed")
	}
}
