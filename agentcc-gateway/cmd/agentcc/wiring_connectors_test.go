package main

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/server"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The connectors step loads the connector catalog: the embedded bundle by
// default, connectors.catalog_dir in its place, and a directory that does not
// load stops the gateway. The org config handlers get it whether or not MCP
// is on, so a catalog_ref server is refused at the PUT exactly when the MCP
// pool would refuse it; the tenancy step gets the
// same catalog (wiring_mcp_tenancy.go).
func TestConnectorsWiring(t *testing.T) {
	put := func(srv *server.Server, body string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("PUT", "/-/orgs/o1/config?org_id=o1", bytes.NewReader([]byte(body)))
		req.Header.Set("Authorization", "Bearer adm")
		w := httptest.NewRecorder()
		srv.OrgConfigHandlers().SetOrgConfig(w, req)
		return w
	}
	closeServer := func(ref string) string {
		return `{"mcp":{"enabled":true,"servers":{"close":{"url":"https://mcp.close.com/mcp","catalog_ref":"` + ref + `",` +
			`"credential":{"mode":"shared","secret":{"type":"api_key","in":"header","name":"Close-API-Key","value":"test-close-key-not-real"}}}}}}`
	}
	wired := func(t *testing.T, catalogDir string) (*wiringDeps, *server.Server, error) {
		t.Helper()
		cfg := config.DefaultConfig()
		cfg.Admin.Token = "adm"
		cfg.Connectors.CatalogDir = catalogDir
		registry, err := providers.NewRegistry(cfg)
		if err != nil {
			t.Fatal(err)
		}
		var mdb atomic.Pointer[modeldb.ModelDB]
		mdb.Store(modeldb.New(modeldb.BundledModels, nil))
		srv := server.New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, &mdb, tenant.NewStore(), nil)
		t.Cleanup(func() {
			if m := srv.MCP(); m != nil {
				m.Close()
			}
		})
		d := &wiringDeps{cfg: cfg, server: srv}
		if err := runWiring(d, []wiringStep{{"connectors", initConnectors}}); err != nil {
			return d, srv, err
		}
		return d, srv, d.startServerWiring()
	}

	t.Run("the embedded catalog", func(t *testing.T) {
		d, srv, err := wired(t, "")
		if err != nil {
			t.Fatal(err)
		}
		if _, parts, ok := d.connectorCatalog.CredentialParts("close", "api-key"); !ok || len(parts) != 2 {
			t.Fatalf("close/api-key: ok %t, %d parts", ok, len(parts))
		}
		if w := put(srv, closeServer("close/api-key@1.0.0")); w.Code != http.StatusOK {
			t.Fatalf("close/api-key@1.0.0: PUT = %d %s", w.Code, w.Body.String())
		}
		if w := put(srv, closeServer("close/api-key@2.0.0")); w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "mcp.servers.close.catalog_ref") {
			t.Fatalf("another major version: PUT = %d %s", w.Code, w.Body.String())
		}
	})

	t.Run("connectors.catalog_dir replaces it", func(t *testing.T) {
		d, srv, err := wired(t, "../../internal/connectorcatalog/manifest/testdata/valid")
		if err != nil {
			t.Fatal(err)
		}
		if _, _, ok := d.connectorCatalog.CredentialParts("close", "api-key"); ok {
			t.Fatal("the embedded catalog is still loaded")
		}
		if _, _, ok := d.connectorCatalog.CredentialParts("close", "close-mcp"); !ok {
			t.Fatal("the directory's close/close-mcp is not loaded")
		}
		if w := put(srv, closeServer("close/api-key@1.0.0")); w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "does not have") {
			t.Fatalf("a provider only the embedded catalog has: PUT = %d %s", w.Code, w.Body.String())
		}
	})

	t.Run("a directory that does not load stops the gateway", func(t *testing.T) {
		if _, _, err := wired(t, t.TempDir()); err == nil || !strings.Contains(err.Error(), "wiring connectors") || !strings.Contains(err.Error(), "holds no app") {
			t.Fatalf("an empty catalog_dir: %v", err)
		}
	})
}
