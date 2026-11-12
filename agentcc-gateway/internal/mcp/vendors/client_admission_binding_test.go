package vendors

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

func admissionReason(d *mcp.Denial) string {
	if d == nil {
		return ""
	}
	return d.Metadata.(map[string]any)["reason"].(string)
}

// A client built for one binding is refused once its catalog_ref or URL
// differs, even when the new binding would be admitted on its own. Servers
// that are not K2 now and were not when their client was built are not
// governed here.
func TestK2ClientAdmissionRefusesChangedBinding(t *testing.T) {
	const base = "https://runtime.example.test/"
	cat := servedSetCatalog(t, []string{"org"})
	gate, err := NewClientAdmission(cat, "https://runtime.example.test")
	if err != nil {
		t.Fatal(err)
	}
	ref := func(app string) string {
		return app + "/futureagi-rest@" + cat.App(app).Provider("futureagi-rest").Version
	}
	docs := &mcp.ClientBinding{CatalogRef: ref("google-docs"), URL: base + "google-docs/mcp"}
	ordinary := &mcp.ClientBinding{CatalogRef: "ordinary/vendor-mcp@1.0.0", URL: base + "ordinary/mcp"}
	request := func(ref, url string, bound *mcp.ClientBinding) mcp.ClientAdmissionRequest {
		return mcp.ClientAdmissionRequest{OrgID: "org-a", ServerID: "service", CatalogRef: ref, URL: url, CredentialMode: "per_user", Bound: bound}
	}
	for _, tc := range []struct {
		name string
		req  mcp.ClientAdmissionRequest
		want string
	}{
		{"construction", request(ref("google-docs"), base+"google-docs/mcp", nil), ""},
		{"same-binding", request(ref("google-docs"), base+"google-docs/mcp", docs), ""},
		{"another-admitted-record", request(ref("google-drive"), base+"google-drive/mcp", docs), "k2_binding_changed"},
		{"url-only-to-wrong-endpoint", request(ref("google-docs"), base+"elsewhere", docs), "k2_endpoint_unavailable"},
		{"ref-only-to-stale-version", request("google-docs/futureagi-rest@9.9.9", base+"google-docs/mcp", docs), "k2_client_source_unavailable"},
		{"left-k2", request("ordinary/vendor-mcp@1.0.0", base+"ordinary/mcp", docs), "k2_binding_changed"},
		{"server-removed", mcp.ClientAdmissionRequest{OrgID: "org-a", ServerID: "service", Bound: docs}, "k2_binding_changed"},
		{"ordinary-to-k2", request(ref("google-docs"), base+"google-docs/mcp", ordinary), "k2_binding_changed"},
		{"ordinary-stays-ordinary", request("ordinary/vendor-mcp@1.0.0", base+"ordinary-next/mcp", ordinary), ""},
		{"ordinary-removed", mcp.ClientAdmissionRequest{OrgID: "org-a", ServerID: "service", Bound: ordinary}, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := admissionReason(gate.AdmitClient(tc.req).Denial); got != tc.want {
				t.Fatalf("refusal %q, want %q", got, tc.want)
			}
		})
	}
	none, err := NewClientAdmission(cat, "")
	if err != nil {
		t.Fatal(err)
	}
	for name, req := range map[string]mcp.ClientAdmissionRequest{
		"k2-now":        request(ref("google-docs"), base+"google-docs/mcp", nil),
		"k2-when-built": request("ordinary/vendor-mcp@1.0.0", base+"ordinary/mcp", docs),
	} {
		if got := admissionReason(none.AdmitClient(req).Denial); got != "k2_runtime_unavailable" {
			t.Errorf("%s without a runtime: refusal %q, want k2_runtime_unavailable", name, got)
		}
	}
}

// Only entry construction asks for the offline inventory, and a refused
// server keeps it, so it stays visible as Coming soon without discovery.
func TestK2ClientAdmissionInventoryOnlyWhenAsked(t *testing.T) {
	cat := servedSetCatalog(t, []string{"org"})
	gate, err := NewClientAdmission(cat, "https://runtime.example.test")
	if err != nil {
		t.Fatal(err)
	}
	p := cat.App("google-docs").Provider("futureagi-rest")
	request := mcp.ClientAdmissionRequest{OrgID: "org-a", CatalogRef: "google-docs/futureagi-rest@" + p.Version, URL: "https://runtime.example.test/google-docs/mcp", CredentialMode: "per_user"}
	if got := gate.AdmitClient(request); got.Denial != nil || len(got.Tools) != 0 {
		t.Fatalf("a lookup-time evaluation built an inventory: denial=%v tools=%d", got.Denial, len(got.Tools))
	}
	request.Inventory = true
	if got := gate.AdmitClient(request); got.Denial != nil || len(got.Tools) == 0 {
		t.Fatalf("construction got no inventory: denial=%v tools=%d", got.Denial, len(got.Tools))
	}
	p.XGateway.ConnectionEnablement.Enabled = false
	if got := gate.AdmitClient(request); got.Denial == nil || len(got.Tools) == 0 {
		t.Fatal("a refused server lost its offline inventory")
	}
}
