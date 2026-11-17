package vendors

import (
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A connector the gateway does not serve is refused in the control plane's
// own words: Microsoft's sentence for a Microsoft app or a record in
// Microsoft's client group, the app sentence for any other, whatever the
// reason. The code, type and metadata keys stay; no text or metadata value
// the caller reads carries an internal code name.

const (
	sentenceMicrosoft = "Microsoft connections are not available in this release."
	sentenceApp       = "Connections to this app are not available in this release."
	comingSoonRuntime = "https://runtime.example.test"
)

func TestComingSoonRefusalReadsThePlainSentence(t *testing.T) {
	for _, item := range servedSetApps {
		want := sentenceApp
		if item.adapter == "microsoft" {
			want = sentenceMicrosoft
		}
		for _, tc := range []struct {
			name, reason, runtime string
			edit                  func(p *manifest.Provider, r *mcp.ClientAdmissionRequest)
		}{
			{"no runtime", "runtime_unavailable", "", nil},
			{"no org client", "client_source_unavailable", comingSoonRuntime, func(p *manifest.Provider, _ *mcp.ClientAdmissionRequest) {
				p.Auth.OAuth2.ClientSources = nil
			}},
			{"not served", "connection_disabled", comingSoonRuntime, func(p *manifest.Provider, _ *mcp.ClientAdmissionRequest) {
				p.XGateway.ConnectionEnablement = nil
			}},
			{"unknown client group", "provider_unavailable", comingSoonRuntime, func(p *manifest.Provider, _ *mcp.ClientAdmissionRequest) {
				p.Auth.OAuth2.ClientGroup = "other"
			}},
			{"shared account", "posture_unavailable", comingSoonRuntime, func(_ *manifest.Provider, r *mcp.ClientAdmissionRequest) {
				r.CredentialMode = "shared"
			}},
			{"wrong endpoint", "endpoint_unavailable", comingSoonRuntime, func(_ *manifest.Provider, r *mcp.ClientAdmissionRequest) {
				r.URL = comingSoonRuntime + "/elsewhere/mcp"
			}},
			{"rebound", "binding_changed", comingSoonRuntime, func(_ *manifest.Provider, r *mcp.ClientAdmissionRequest) {
				r.Bound = &mcp.ClientBinding{CatalogRef: "other-app/futureagi-rest@1.0.0", URL: comingSoonRuntime + "/other-app/mcp"}
			}},
			{"version not in the catalogue", "client_source_unavailable", comingSoonRuntime, func(_ *manifest.Provider, r *mcp.ClientAdmissionRequest) {
				r.CatalogRef = item.app + "/futureagi-rest@9.9.9"
			}},
		} {
			t.Run(item.app+"/"+tc.name, func(t *testing.T) {
				cat := servedSetCatalog(t, []string{"org"})
				p := cat.App(item.app).Provider("futureagi-rest")
				request := mcp.ClientAdmissionRequest{OrgID: "org-a", ServerID: "service", CatalogRef: item.app + "/futureagi-rest@" + p.Version,
					URL: comingSoonRuntime + "/" + item.app + "/mcp", CredentialMode: "per_user"}
				if tc.edit != nil {
					tc.edit(p, &request)
				}
				gate, err := NewClientAdmission(cat, tc.runtime)
				if err != nil {
					t.Fatal(err)
				}
				plainComingSoon(t, gate.AdmitClient(request).Denial, want, tc.reason)
			})
		}
	}
}

// A record in Microsoft's client group reads Microsoft's sentence whatever
// its app is called.
func TestComingSoonRefusalForMicrosoftsClientGroup(t *testing.T) {
	cat := servedSetCatalog(t, []string{"org"})
	outlook := cat.App("microsoft-outlook").Provider("futureagi-rest")
	p := *outlook
	p.App = "contoso-mail"
	gateway := *outlook.XGateway
	gateway.ConnectionEnablement = nil
	p.XGateway = &gateway
	cat.Apps = append(cat.Apps, &manifest.App{ID: "contoso-mail", Providers: []*manifest.Provider{&p}})
	gate, err := NewClientAdmission(cat, comingSoonRuntime)
	if err != nil {
		t.Fatal(err)
	}
	request := mcp.ClientAdmissionRequest{OrgID: "org-a", ServerID: "service", CatalogRef: "contoso-mail/futureagi-rest@" + p.Version,
		URL: comingSoonRuntime + "/contoso-mail/mcp", CredentialMode: "per_user"}
	plainComingSoon(t, gate.AdmitClient(request).Denial, sentenceMicrosoft, "connection_disabled")
}

// A pool without the admission gate fails closed with the same sentences.
func TestComingSoonRefusalWithoutTheGate(t *testing.T) {
	for _, c := range []struct{ ref, want string }{
		{"microsoft-teams/futureagi-rest@1.0.0", sentenceMicrosoft},
		{"gmail/futureagi-rest@1.0.0", sentenceApp},
	} {
		t.Run(c.ref, func(t *testing.T) {
			cc := &mcp.CallContext{
				OrgConfig: &tenant.OrgConfig{MCP: &tenant.MCPOrgConfig{Enabled: true, Servers: map[string]*tenant.MCPServerOrgConfig{
					"service": {URL: comingSoonRuntime + "/app/mcp", CatalogRef: c.ref},
				}}},
				Upstream: mcp.UpstreamRef{ServerID: "service"},
			}
			auth, d := resolveConstructed(nil, cc)
			if auth != nil {
				t.Fatal("a refused call got a credential")
			}
			plainComingSoon(t, d, c.want, "construction_unavailable")
		})
	}
}

func plainComingSoon(t *testing.T, d *mcp.Denial, sentence, reason string) {
	t.Helper()
	if d == nil {
		t.Fatal("not refused")
	}
	unavailable := mcp.CredentialUnavailable()
	meta := map[string]any{"state": "coming_soon", "reason": reason, "client_source": "org"}
	if d.Message != sentence || d.Code != unavailable.Code || d.Type != unavailable.Type || !reflect.DeepEqual(d.Metadata, meta) {
		t.Fatalf("refusal %q (%s, %s, %v); want %q (%s, %s, %v)", d.Message, d.Type, d.Code, d.Metadata, sentence, unavailable.Type, unavailable.Code, meta)
	}
	if strings.Contains(strings.ToLower(d.Error()), "k2") {
		t.Fatalf("refusal %q names an internal code", d.Error())
	}
}
