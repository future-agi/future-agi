package providers

import (
	"context"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Resolve synthetically and dial only the loopback fixture, even for Vertex URLs.
func r4cPinOrgDial(t *testing.T, upstream *httptest.Server) func(context.Context, string, string) (net.Conn, error) {
	t.Helper()
	oldLookup, oldDial := lookupIP, orgDialContext
	lookupIP = func(string) ([]net.IP, error) { return []net.IP{net.ParseIP("8.8.8.8")}, nil }
	dial := func(ctx context.Context, network, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, network, upstream.Listener.Addr().String())
	}
	orgDialContext = func(bool) func(context.Context, string, string) (net.Conn, error) { return dial }
	t.Cleanup(func() { lookupIP, orgDialContext = oldLookup, oldDial })
	return dial
}

func TestOptBR4CTenantBedrockRegionWins(t *testing.T) {
	wire := make(chan http.Header, 1)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		wire <- r.Header.Clone()
		w.WriteHeader(http.StatusBadRequest)
		_, _ = io.WriteString(w, `{"message":"synthetic refusal"}`)
	}))
	defer upstream.Close()
	base := config.ProviderConfig{APIFormat: "bedrock", BaseURL: upstream.URL, Headers: map[string]string{"x-aws-region": "eu-west-3"}}
	tenantCfg := &tenant.ProviderConfig{AWSRegion: "ap-southeast-2", AWSAccessKeyID: "r4c-tenant-access", AWSSecretAccessKey: "r4c-tenant-secret"}
	resolved := resolveOrgConfig(base, "", tenantCfg)
	if resolved.Headers["x-aws-region"] != "eu-west-3" || resolved.AWSRegion != "ap-southeast-2" {
		t.Fatal("fixture must retain both conflicting region sources")
	}
	cache := NewOrgProviderCache(map[string]config.ProviderConfig{"bedrock": base}, false)
	defer cache.EvictAll()
	p, err := cache.GetOrCreateWithTenantConfig("r4c-org", "bedrock", "", tenantCfg)
	if err != nil {
		t.Fatal(err)
	}
	r4cDispatch(t, p, "bedrock")
	select {
	case sent := <-wire:
		auth := sent.Get("Authorization")
		if !strings.Contains(auth, "Credential=r4c-tenant-access/") || !strings.Contains(auth, "/ap-southeast-2/bedrock/aws4_request") || strings.Contains(auth, "/eu-west-3/") {
			t.Error("tenant AWSRegion did not win over the inherited signing-region header")
		}
	default:
		t.Fatal("no outbound signed request")
	}
}

func TestOptBR4CTenantVertexProjectPathWins(t *testing.T) {
	for _, format := range []string{"gemini", "google"} {
		for _, route := range []string{"org_cache", "adapter_with_inherited_settings"} {
			t.Run(format+"/"+route, func(t *testing.T) {
				wire := make(chan *http.Request, 1)
				upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					_, _ = io.Copy(io.Discard, r.Body)
					wire <- r.Clone(context.Background())
					w.WriteHeader(http.StatusBadRequest)
					_, _ = io.WriteString(w, `{"error":{"message":"synthetic refusal"}}`)
				}))
				defer upstream.Close()
				dial := r4cPinOrgDial(t, upstream)
				const tenantURL = "http://r4c.aiplatform.googleapis.com/v1beta1/projects/r4c-tenant-project/locations/us-west1"
				base := config.ProviderConfig{APIFormat: format, BaseURL: "http://r4c.aiplatform.googleapis.com", Headers: map[string]string{"x-gcp-project": "r4c-operator-project", "x-gcp-location": "europe-west4"}, DialContext: dial}
				var p Provider
				var err error
				if route == "org_cache" {
					cache := NewOrgProviderCache(map[string]config.ProviderConfig{format: base}, false)
					defer cache.EvictAll()
					p, err = cache.GetOrCreateWithTenantConfig("r4c-org", format, r4cTenantKey, &tenant.ProviderConfig{APIFormat: format, BaseURL: tenantURL})
				} else {
					// The cache's explicit-URL branch drops inherited settings. Also
					// exercise Gemini's own /projects/ guard with both settings present.
					base.BaseURL = tenantURL
					resolved := resolveOrgConfig(base, r4cTenantKey, nil)
					if resolved.Headers["x-gcp-project"] != "r4c-operator-project" || resolved.Headers["x-gcp-location"] != "europe-west4" {
						t.Fatal("adapter guard control lost the conflicting inherited settings")
					}
					p, err = createProvider("r4c-org-adapter", resolved)
					if err == nil {
						defer p.Close()
					}
				}
				if err != nil {
					t.Fatal(err)
				}
				r4cDispatch(t, p, format)
				select {
				case sent := <-wire:
					if sent.URL.Path != "/v1beta1/projects/r4c-tenant-project/locations/us-west1/publishers/google/models/fixture:generateContent" {
						t.Error("inherited project/location rewrote the tenant's explicit Vertex path")
					}
					if sent.Header.Get("Authorization") != "Bearer "+r4cTenantKey {
						t.Error("Vertex request did not use the tenant credential")
					}
				default:
					t.Fatal("no outbound Vertex request")
				}
			})
		}
	}
}
