package vendors

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/catalog/connectors"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

var servedSetApps = []struct{ app, adapter string }{
	{"google-docs", "google"}, {"google-drive", "google"}, {"google-meet", "google"}, {"google-sheets", "google"}, {"google-slides", "google"},
	{"microsoft-excel", "microsoft"}, {"microsoft-onedrive", "microsoft"}, {"microsoft-sharepoint", "microsoft"},
	{"gmail", "google"}, {"google-calendar", "google"}, {"microsoft-outlook", "microsoft"}, {"microsoft-teams", "microsoft"},
}

func servedSetCatalog(t *testing.T, sources []string) *manifest.Catalog {
	t.Helper()
	var bundle struct {
		Providers []map[string]any `json:"providers"`
	}
	if err := json.Unmarshal(connectors.BundleJSON, &bundle); err != nil {
		t.Fatal(err)
	}
	cat := &manifest.Catalog{}
	for _, item := range servedSetApps {
		found := false
		for _, record := range bundle.Providers {
			if record["app"] != item.app || record["id"] != "futureagi-rest" {
				continue
			}
			delete(record, "clientSources")
			record["auth"].(map[string]any)["oauth2"].(map[string]any)["clientSources"] = sources
			// Synthetic enablement for unit policy checks; real gmail=false is covered
			// by the full-wiring REGEN-012 fixture, never overridden in production.
			record["x-gateway"] = map[string]any{"connectionEnablement": map[string]any{"schemaVersion": 1, "enabled": true, "operations": []string{"connect", "list_tools", "invoke"}}}
			raw, err := json.Marshal(record)
			if err != nil {
				t.Fatal(err)
			}
			var p manifest.Provider
			if err := json.Unmarshal(raw, &p); err != nil {
				t.Fatal(err)
			}
			cat.Apps = append(cat.Apps, &manifest.App{ID: item.app, Providers: []*manifest.Provider{&p}})
			found = true
			break
		}
		if !found {
			t.Fatalf("missing provider %s", item.app)
		}
	}
	return cat
}
func TestK2ServedSetSourceMatrix(t *testing.T) {
	for _, tc := range []struct {
		name    string
		sources []string
		admit   bool
	}{
		{"org", []string{"org"}, true}, {"both", []string{"org", "platform"}, true},
		{"platform", []string{"platform"}, false}, {"missing", nil, false}, {"empty", []string{}, false},
		{"unknown", []string{"future"}, false}, {"mixed-unknown", []string{"org", "future"}, false}, {"wrong-case", []string{"ORG"}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cat := servedSetCatalog(t, tc.sources)
			gate, err := NewClientAdmission(cat, "https://runtime.example.test")
			if err != nil {
				t.Fatal(err)
			}
			for _, item := range servedSetApps {
				p := cat.App(item.app).Provider("futureagi-rest")
				result := gate.AdmitClient(mcp.ClientAdmissionRequest{OrgID: "org-a", CatalogRef: item.app + "/futureagi-rest@" + p.Version, URL: "https://runtime.example.test/" + item.app + "/mcp", CredentialMode: "per_user"})
				if (result.Denial == nil) != tc.admit {
					t.Errorf("%s admit=%v want %v", item.app, result.Denial == nil, tc.admit)
				}
			}
		})
	}
}
func TestK2ServedSetKeepsNativeAndDelegatedGuards(t *testing.T) {
	for _, change := range []struct {
		name string
		edit func(*manifest.Provider)
	}{
		{"publisher", func(p *manifest.Provider) { p.Publisher = "community" }},
		{"kind", func(p *manifest.Provider) { p.Kind = manifest.KindVendorMCP }},
		{"pkce", func(p *manifest.Provider) { p.Auth.OAuth2.PKCE = false }},
		{"user", func(p *manifest.Provider) { p.AccountModes = []string{"org"} }},
		{"tools", func(p *manifest.Provider) { p.Tools.Items = nil; p.GeneratedTools = nil }},
		{"client-group", func(p *manifest.Provider) { p.Auth.OAuth2.ClientGroup = "" }},
	} {
		t.Run(change.name, func(t *testing.T) {
			for _, app := range servedSetCatalog(t, []string{"org"}).Apps {
				p := app.Providers[0]
				change.edit(p)
				if googleProvider(p) || microsoftProvider(p) {
					t.Fatalf("%s bypassed native checks", p.App)
				}
			}
		})
	}
	for _, app := range servedSetCatalog(t, []string{"org"}).Apps {
		p := app.Providers[0]
		if p.Auth.OAuth2.ClientGroup != "microsoft" {
			continue
		}
		p.Auth.OAuth2.Scopes = []string{"https://graph.microsoft.com/.default"}
		if microsoftProvider(p) {
			t.Fatalf("%s admitted app-only scopes", p.App)
		}
	}
}

// The gate reads the catalog record as it is at each evaluation: nothing it
// concluded earlier authorises a later request, and no app name is listed.
func TestK2ServedSetReadsCurrentRecordAndNoNameAllowlist(t *testing.T) {
	cat := servedSetCatalog(t, []string{"org"})
	app := cat.App("google-docs")
	app.ID = "catalog-next-app"
	p := app.Providers[0]
	p.App = app.ID
	ref := app.ID + "/futureagi-rest@" + p.Version
	gate, err := NewClientAdmission(cat, "https://runtime.example.test")
	if err != nil {
		t.Fatal(err)
	}
	request := mcp.ClientAdmissionRequest{OrgID: "org-a", CatalogRef: ref, URL: "https://runtime.example.test/catalog-next-app/mcp", CredentialMode: "per_user"}
	reason := func() string {
		denial := gate.AdmitClient(request).Denial
		if denial == nil {
			return ""
		}
		return denial.Metadata.(map[string]any)["reason"].(string)
	}
	if got := reason(); got != "" {
		t.Fatalf("declared record refused: %s", got)
	}
	// Each change is made to the live record and undone: a refusal is never sticky.
	for _, step := range []struct {
		name, want string
		change     func()
		undo       func()
	}{
		{"enablement-off", "k2_connection_disabled", func() { p.XGateway.ConnectionEnablement.Enabled = false }, func() { p.XGateway.ConnectionEnablement.Enabled = true }},
		{"source-platform-only", "k2_client_source_unavailable", func() { p.Auth.OAuth2.ClientSources = []string{"platform"} }, func() { p.Auth.OAuth2.ClientSources = []string{"org"} }},
	} {
		step.change()
		if got := reason(); got != step.want {
			t.Fatalf("%s: refusal %q, want %q: the gate used a recorded verdict or a copy of the record", step.name, got, step.want)
		}
		step.undo()
		if got := reason(); got != "" {
			t.Fatalf("%s undone: still refused with %q", step.name, got)
		}
	}
	apps := cat.Apps
	cat.Apps = nil
	if got := reason(); got != "k2_client_source_unavailable" {
		t.Fatalf("record removed: refusal %q, want k2_client_source_unavailable", got)
	}
	cat.Apps = apps
	if got := reason(); got != "" {
		t.Fatalf("record restored: still refused with %q", got)
	}
}
