package main

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestK2ServedSetDisabledGuard(t *testing.T) {
	for _, app := range []string{
		"google-docs", "google-drive", "google-meet", "google-sheets", "google-slides",
		"microsoft-excel", "microsoft-onedrive", "microsoft-sharepoint",
		"gmail", "google-calendar", "microsoft-outlook", "microsoft-teams", "catalog-next-app",
	} {
		t.Run(app, func(t *testing.T) {
			raw, err := json.Marshal(map[string]any{"mcp": map[string]any{"servers": map[string]any{
				"service": map[string]any{"catalog_ref": app + "/futureagi-rest@1.0.0"},
			}}})
			if err != nil {
				t.Fatal(err)
			}
			var cfg tenant.OrgConfig
			if err := json.Unmarshal(raw, &cfg); err != nil {
				t.Fatal(err)
			}
			cc := &mcp.CallContext{OrgConfig: &cfg}
			cc.Upstream.ServerID = "service"
			if !k2CallHidden(cc) {
				t.Fatal("K2-disabled path would bypass admission via the plain resolver")
			}
			// A nil base resolver proves refusal happens before token lookup.
			a, d := (k2DisabledResolver{}).Resolve(cc)
			if a != nil || d == nil || d.HTTPStatus != 404 {
				t.Fatalf("disabled K2 was not refused: auth=%v denial=%+v", a, d)
			}
		})
	}
	if k2CallHidden(&mcp.CallContext{}) {
		t.Fatal("an ordinary server was treated as K2")
	}
}

func TestK2ServedSetWiringUsesEmbeddedRecords(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.MCP.Enabled = true
	cfg.MCP.K2AppsEnabled = true
	cfg.MCP.UpstreamOAuthEnabled = true
	cfg.MCP.Vendors.K2RuntimeURL = "https://runtime.example.test"
	// The K2 served set belongs to the serving build, not a directory that
	// could describe providers absent from its embedded runtime catalog.
	cfg.Connectors.CatalogDir = t.TempDir() + "/not-an-admission-source"
	if err := initMCPVendors(&wiringDeps{cfg: cfg}, &mcp.Deps{}); err != nil {
		t.Fatalf("K2 admission did not use the embedded catalog: %v", err)
	}
}
