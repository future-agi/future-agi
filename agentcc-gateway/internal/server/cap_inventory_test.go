package server

import (
	"context"
	_ "embed"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

//go:embed testdata/cap-route-inventory.txt
var capRouteInventory string

func capRouteClasses(t *testing.T) map[string]string {
	t.Helper()
	classes := map[string]string{}
	for _, line := range strings.Split(capRouteInventory, "\n") {
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		category, route, _ := strings.Cut(line, " ")
		if classes[route] != "" {
			t.Fatalf("duplicate inventory row: %s", route)
		}
		switch category {
		case "supported", "unsupported", "public", "discovery", "admin", "admin-execution":
		default:
			t.Fatalf("unknown class %s", category)
		}
		classes[route] = category
	}
	return classes
}
func capInventoryServer(t *testing.T) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Admin.Token = canonAdminToken
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "caps", Key: videoKeyA, KeyType: "byok", Metadata: map[string]string{"org_id": videoOrgA}}}
	cfg.A2A.Enabled = true
	cfg.MCP.Enabled = true
	cfg.MCP.Endpoint = "/cap-mcp"
	cfg.Cluster.Enabled = true
	cfg.Edge.Enabled = true
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	store := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(), store, nil, nil, metrics.NewRegistry(), testModelDBPtr(), tenant.NewStore(), nil)
	srv.TenantStore.Set(videoOrgA, capRouteConfig(t, srv, true))
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		srv.Shutdown(ctx)
	})
	return srv
}
func TestCapAdmissionRouteInventory(t *testing.T) {
	srv := capInventoryServer(t)
	classes := capRouteClasses(t)
	registered := map[string]bool{}
	for _, rt := range srv.router.Routes() {
		name := rt.Method + " " + rt.Pattern
		// Routes lists every registration. A later registration of the same
		// method and path replaces the handler (registerMCPAdminRoutes
		// re-registers the five MCP admin routes and POST /-/reload this
		// way), so it is the same route: one row, classified once.
		if registered[name] {
			continue
		}
		registered[name] = true
		class, ok := classes[name]
		if !ok {
			t.Errorf("unclassified route: %s", name)
			continue
		}
		if class == "supported" && name != "POST /v1/chat/completions" {
			t.Errorf("unsupported new execution: %s", name)
		}
		delete(classes, name)
	}
	for name := range classes {
		t.Errorf("stale route classification: %s", name)
	}
}

func TestCappedUnsupportedSurfacesNeverDispatch(t *testing.T) {
	srv := capInventoryServer(t)
	classes := capRouteClasses(t)
	routes := srv.router.Routes()
	// Instrument the exact router boundary AFTER successful auth and admission.
	// Every possible provider call, enqueue, upgrade or session creation occurs
	// after this boundary. Positive controls prove each registered route reaches it.
	var executions atomic.Int64
	for _, rt := range routes {
		if classes[rt.Method+" "+rt.Pattern] != "unsupported" {
			continue
		}
		spy := func(w http.ResponseWriter, r *http.Request) { executions.Add(1); w.WriteHeader(299) }
		srv.router.static[rt.Method+" "+rt.Pattern] = spy
		for key, parts := range srv.router.paramIndex {
			for i := range parts {
				if parts[i].method != rt.Method {
					continue
				}
				pattern := ""
				for _, part := range parts[i].parts {
					if part.isParam {
						pattern += "/{" + part.value + "}"
					} else {
						pattern += "/" + part.value
					}
				}
				if pattern == rt.Pattern {
					srv.router.paramIndex[key][i].handler = spy
				}
			}
		}
	}
	for _, rt := range routes {
		class := classes[rt.Method+" "+rt.Pattern]
		if class != "unsupported" && class != "admin-execution" {
			continue
		}
		t.Run(rt.Method+" "+rt.Pattern, func(t *testing.T) {
			target := concretePath(rt.Pattern)
			headers := map[string]string{"Authorization": "Bearer " + videoKeyA}
			if strings.HasPrefix(target, "/v1beta/") {
				headers = map[string]string{"x-goog-api-key": videoKeyA}
			}
			if strings.HasPrefix(target, "/cartesia/") {
				headers = map[string]string{"X-API-Key": videoKeyA}
			}
			if strings.HasPrefix(target, "/deepgram/") {
				headers = map[string]string{"Authorization": "Token " + videoKeyA}
			}
			body := `{"model":"synthetic","messages":[{"role":"user","content":"hi"}],"prompt":"hi","input":"hi","jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"cap-test","version":"1"}}}`
			before := executions.Load()
			w := serveWithHeaders(t, srv, rt.Method, target, body, headers, 5*time.Second)
			if class == "admin-execution" {
				if w.Code != http.StatusUnauthorized && w.Code != http.StatusForbidden {
					t.Fatalf("customer key reached administrative execution: %d %s", w.Code, w.Body)
				}
				return
			}
			if w.Code != http.StatusPaymentRequired || errorCode(t, w) != "call_cap_unenforceable" || executions.Load() != before || w.Header().Get("MCP-Session-Id") != "" {
				t.Fatalf("unsupported dispatch: %d %s calls=%d", w.Code, w.Body, executions.Load()-before)
			}
			srv.TenantStore.Set(videoOrgA, capRouteConfig(t, srv, false))
			control := serveWithHeaders(t, srv, rt.Method, target, body, headers, 5*time.Second)
			srv.TenantStore.Set(videoOrgA, capRouteConfig(t, srv, true))
			if control.Code != 299 || executions.Load() != before+1 {
				t.Fatalf("default-off route control unreachable: %d %s", control.Code, control.Body)
			}
		})
	}
}
