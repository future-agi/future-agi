package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/catalog/connectors"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/restmcp"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/runtimesupport"
)

const supportTestToken = "unit-only-0123456789abcdef0123456789abcdef"

func runtimeRequest(t *testing.T, h http.Handler, app, provider, revision string, authenticated bool) (int, map[string]any) {
	t.Helper()
	data, _ := json.Marshal(map[string]string{"schema": runtimesupport.RequestSchema, "app_id": app, "provider_id": provider, "revision": revision, "runtime_url": "https://runtime.invalid", "request_id": strings.Repeat("a", 32)})
	r := httptest.NewRequest("POST", runtimesupport.Path, strings.NewReader(string(data)))
	r.Header.Set("Content-Type", "application/json")
	if authenticated {
		r.Header.Set("Authorization", "Bearer "+supportTestToken)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	var body map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	return w.Code, body
}

func TestRuntimeSupportEmbeddedDisabledAndLegacyAbsent(t *testing.T) {
	t.Setenv("AGENTCC_ADMIN_TOKEN", supportTestToken)
	t.Setenv("AGENTCC_CONNECTORS_RUNTIME_URL", "https://runtime.invalid")
	t.Setenv("AGENTCC_MCP_K2_APPS_ENABLED", "true")
	old := runtimeSupportBuildID
	runtimeSupportBuildID = "sha256:" + strings.Repeat("a", 64)
	t.Cleanup(func() { runtimeSupportBuildID = old })
	apps, cat, err := loadRuntimeApps("")
	if err != nil {
		t.Fatal(err)
	}
	srv, err := restmcp.New(apps, restmcp.Options{})
	if err != nil {
		t.Fatal(err)
	}
	h := handler(srv, apps, cat)
	records, digest := cat.RuntimeSnapshot()
	if len(records) == 0 || len(digest) != 64 {
		t.Fatal("no embedded snapshot")
	}
	routes := map[string]bool{}
	for _, a := range apps {
		routes[a.ID+"/"+a.Provider] = true
	}
	declared, absent, checked := 0, 0, 0
	for _, rec := range records {
		if rec.Retired || (rec.Kind != "rest" && rec.Kind != "vendor_mcp") || (rec.Kind == "rest" && !routes[rec.AppID+"/"+rec.ProviderID]) {
			continue
		}
		var raw struct {
			Gateway struct {
				Enablement *struct {
					Enabled bool `json:"enabled"`
				} `json:"connectionEnablement"`
			} `json:"x-gateway"`
		}
		if err := json.Unmarshal(rec.Raw, &raw); err != nil {
			t.Fatal(err)
		}
		if raw.Gateway.Enablement == nil {
			absent++
		} else {
			declared++
			if raw.Gateway.Enablement.Enabled {
				t.Fatal("pinned-base expectation changed: fixture must be updated explicitly")
			}
		}
		rev, err := runtimesupport.DeclarationRevision(rec.Raw)
		if err != nil {
			t.Fatal(err)
		}
		status, body := runtimeRequest(t, h, rec.AppID, rec.ProviderID, rev, true)
		if status != 200 || body["state"] != "unavailable" || body["reason_code"] != "runtime_support_lifecycle_unavailable" || len(body) != 8 {
			t.Fatalf("disabled/absent record: %d %v", status, body)
		}
		status, body = runtimeRequest(t, h, rec.AppID, rec.ProviderID, "1.0.0+sha256:"+strings.Repeat("0", 64), true)
		if status != 200 || body["reason_code"] != "runtime_support_unknown_revision" {
			t.Fatal("digest mismatch not stale")
		}
		status, _ = runtimeRequest(t, h, rec.AppID, rec.ProviderID, rev, false)
		if status != 401 {
			t.Fatal("missing admin token not refused")
		}
		checked++
	}
	if checked == 0 || absent == 0 {
		t.Fatal("did not cover embedded legacy absence")
	}
	// 613951c5 has 36 disabled declarations. ISO/release have none. The
	// layout-compatible test asserts every served declaration is unavailable.
	t.Logf("checked=%d disabled=%d absent=%d", checked, declared, absent)
}

func TestRuntimeSupportDirectoryAndFabricatedCatalogCannotAdmit(t *testing.T) {
	t.Setenv("AGENTCC_ADMIN_TOKEN", supportTestToken)
	t.Setenv("AGENTCC_CONNECTORS_RUNTIME_URL", "https://runtime.invalid")
	t.Setenv("AGENTCC_MCP_K2_APPS_ENABLED", "true")
	apps, cat, err := loadRuntimeApps(cn4Catalog)
	if err != nil {
		t.Fatal(err)
	}
	records, digest := cat.RuntimeSnapshot()
	if digest != "" {
		t.Fatal("directory became embedded admission")
	}
	for _, r := range records {
		if len(r.Raw) != 0 {
			t.Fatal("directory retained unadmitted bytes")
		}
	}
	srv, err := restmcp.New(apps, restmcp.Options{})
	if err != nil {
		t.Fatal(err)
	}
	status, body := runtimeRequest(t, handler(srv, apps, cat), apps[0].ID, apps[0].Provider, "1.0.0+sha256:"+strings.Repeat("a", 64), true)
	if status != 200 || body["reason_code"] != "runtime_support_unknown_revision" {
		t.Fatal(body)
	}
	embedded, err := connectors.Embedded()
	if err != nil {
		t.Fatal(err)
	}
	// Copying public fields or an embedded-looking Source string is not admission.
	fake := &connectors.Catalog{Records: embedded.Records, Source: embedded.Source}
	copied, copyDigest := fake.RuntimeSnapshot()
	if copyDigest != "" {
		t.Fatal("public catalog fields forged admission")
	}
	for _, r := range copied {
		if len(r.Raw) != 0 {
			t.Fatal("public catalog forged canonical bytes")
		}
	}
}
