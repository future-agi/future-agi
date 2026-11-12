package vendors

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/catalog/connectors"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle/canonjson"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

func TestK2ClientAdmissionEnablement(t *testing.T) {
	for _, tc := range []struct {
		name        string
		declaration *manifest.ConnectionEnablementDeclaration
	}{
		{"missing", nil},
		{"disabled", &manifest.ConnectionEnablementDeclaration{SchemaVersion: 1, Enabled: false}},
		{"unknown-schema", &manifest.ConnectionEnablementDeclaration{SchemaVersion: 2, Enabled: true, Operations: []string{"connect", "list_tools", "invoke"}}},
		{"connect-only", &manifest.ConnectionEnablementDeclaration{SchemaVersion: 1, Enabled: true, Operations: []string{"connect"}}},
		{"unknown-operation", &manifest.ConnectionEnablementDeclaration{SchemaVersion: 1, Enabled: true, Operations: []string{"connect", "list_tools", "future"}}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cat := servedSetCatalog(t, []string{"org"})
			p := cat.App("google-docs").Providers[0]
			p.XGateway.ConnectionEnablement = tc.declaration
			gate, err := NewClientAdmission(cat, "https://runtime.example.test")
			if err != nil {
				t.Fatal(err)
			}
			got := gate.AdmitClient(mcp.ClientAdmissionRequest{OrgID: "org-a", CatalogRef: p.App + "/" + p.ID + "@" + p.Version, URL: "https://runtime.example.test/google-docs/mcp", CredentialMode: "per_user"})
			if got.Denial == nil || got.Denial.Metadata.(map[string]any)["reason"] != "k2_connection_disabled" {
				t.Fatalf("enablement was inferred: %+v", got.Denial)
			}
		})
	}
}

// The declaration is read at every evaluation, not when the gate was built.
func TestK2ClientAdmissionEnablementFlipsAfterConstruction(t *testing.T) {
	for _, tc := range []struct {
		name        string
		declaration *manifest.ConnectionEnablementDeclaration
	}{
		{"missing", nil},
		{"disabled", &manifest.ConnectionEnablementDeclaration{SchemaVersion: 1, Enabled: false}},
		{"unknown-schema", &manifest.ConnectionEnablementDeclaration{SchemaVersion: 2, Enabled: true, Operations: []string{"connect", "list_tools", "invoke"}}},
		{"connect-only", &manifest.ConnectionEnablementDeclaration{SchemaVersion: 1, Enabled: true, Operations: []string{"connect"}}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cat := servedSetCatalog(t, []string{"org"})
			p := cat.App("google-docs").Providers[0]
			gate, err := NewClientAdmission(cat, "https://runtime.example.test")
			if err != nil {
				t.Fatal(err)
			}
			request := mcp.ClientAdmissionRequest{OrgID: "org-a", CatalogRef: p.App + "/" + p.ID + "@" + p.Version, URL: "https://runtime.example.test/google-docs/mcp", CredentialMode: "per_user"}
			if got := gate.AdmitClient(request); got.Denial != nil {
				t.Fatalf("enabled control refused: %+v", got.Denial)
			}
			p.XGateway.ConnectionEnablement = tc.declaration
			got := gate.AdmitClient(request)
			if got.Denial == nil || got.Denial.Metadata.(map[string]any)["reason"] != "k2_connection_disabled" {
				t.Fatalf("a recorded verdict outlived the record: %+v", got.Denial)
			}
		})
	}
}

func TestK2ServedSetNestedBundleRoundTrip(t *testing.T) {
	b, err := bundle.Decode(connectors.BundleJSON)
	if err != nil {
		t.Fatal(err)
	}
	evaluated := 0
	for i, raw := range b.Providers {
		record := raw.(canonjson.Object)
		id, _ := record.Get("id")
		if id != "futureagi-rest" {
			continue
		}
		authRaw, _ := record.Get("auth")
		auth := authRaw.(canonjson.Object)
		oauthRaw, _ := auth.Get("oauth2")
		oauth := oauthRaw.(canonjson.Object)
		oauth.Set("clientSources", []any{"org", "platform"})
		auth.Set("oauth2", oauth)
		record.Set("auth", auth)
		b.Providers[i] = record
		evaluated++
	}
	if evaluated != len(servedSetApps) {
		t.Fatalf("evaluated %d records, want %d", evaluated, len(servedSetApps))
	}
	b.SHA256, err = bundle.Digest(b.Apps, b.Providers, b.Platforms)
	if err != nil {
		t.Fatal(err)
	}
	data, err := b.Encode()
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := bundle.Decode(data)
	if err != nil {
		t.Fatal(err)
	}
	cat, err := decoded.Catalog()
	if err != nil {
		t.Fatal(err)
	}
	checked := 0
	for _, app := range cat.Apps {
		p := app.Provider("futureagi-rest")
		if p == nil {
			continue
		}
		checked++
		if !providerClientSource(p, "org") || !providerClientSource(p, "platform") {
			t.Fatalf("nested declaration lost for %s", app.ID)
		}
	}
	if checked != evaluated {
		t.Fatalf("decoded %d/%d providers", checked, evaluated)
	}
}
