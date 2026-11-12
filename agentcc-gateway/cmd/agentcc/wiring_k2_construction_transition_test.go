package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestK2ConstructionUnsupportedPostures(t *testing.T) {
	for _, runtimeSet := range []bool{false, true} {
		for _, mode := range []string{"none", "exchange", "id_jag"} {
			t.Run(mode+map[bool]string{true: "/url-set", false: "/url-empty"}[runtimeSet], func(t *testing.T) {
				cat := k2ConstructionCatalog(t, nil)
				p := cat.App("google-docs").Provider("futureagi-rest")
				f := newK2ConstructionFixture(t, runtimeSet, "google-docs", "google-docs/futureagi-rest@"+p.Version, mode, "")
				f.exercise(t)
				reason := "k2_posture_unavailable"
				if !runtimeSet {
					reason = "k2_runtime_unavailable"
				}
				assertK2ConstructionRefusal(t, f, reason)
			})
		}
	}
}

// A stale connected client is not retained as last-known-good when the next
// config names a refused connection. Changing only catalog_ref/URL must suffice.
// Every record is enabled (gmail too), so the refused target is a record whose
// enablement this test switches off.
func TestK2ConstructionRefusedReplacement(t *testing.T) {
	cat := k2ConstructionCatalog(t, func(app string, record map[string]any) {
		if app == "google-sheets" {
			record["x-gateway"].(map[string]any)["connectionEnablement"].(map[string]any)["enabled"] = false
		}
	})
	p := cat.App("google-docs").Provider("futureagi-rest")
	f := newK2ConstructionFixture(t, true, "google-docs", "google-docs/futureagi-rest@"+p.Version, "per_user", "")
	f.exercise(t)
	if f.dispatch.Load() != 1 {
		t.Fatal("initial admitted positive control did not dispatch")
	}
	before := [4]int64{f.fetches.Load(), f.factories.Load(), f.discovery.Load(), f.dispatch.Load()}
	raw, err := json.Marshal(f.gateway.TenantStore.Get("org-a"))
	if err != nil {
		t.Fatal(err)
	}
	var next tenant.OrgConfig
	if err := json.Unmarshal(raw, &next); err != nil {
		t.Fatal(err)
	}
	next.MCP.Servers["service"].CatalogRef = "google-sheets/futureagi-rest@" + cat.App("google-sheets").Provider("futureagi-rest").Version
	next.MCP.Servers["service"].URL = "http://127.0.0.1:9/google-sheets/mcp"
	f.gateway.TenantStore.Set("org-a", &next)
	f.exercise(t)
	_, pool, err := f.srv.Tenancy().Resolve("org-a")
	if err != nil {
		t.Fatal(err)
	}
	client, err := pool.Client("service", "user-a", mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer synthetic-override"}}})
	var denial *mcp.Denial
	if client != nil || !errors.As(err, &denial) || denial.Metadata.(map[string]any)["reason"] != "k2_connection_disabled" {
		t.Fatalf("refused replacement retained a client: %v", err)
	}
	after := [4]int64{f.fetches.Load(), f.factories.Load(), f.discovery.Load(), f.dispatch.Load()}
	if after != before {
		t.Fatalf("replacement side effects delta: before=%v after=%v", before, after)
	}
}
