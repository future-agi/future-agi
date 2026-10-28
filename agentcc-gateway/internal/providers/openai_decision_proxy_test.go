package providers

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestConfiguredOpenAIDecisionsUseProxyButChatStaysDirect(t *testing.T) {
	proxy := useDecisionProxy(t)
	var upstreamHits atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upstreamHits.Add(1)
		io.WriteString(w, `{"object":"list","data":[]}`)
	}))
	defer upstream.Close()

	cfg := config.DefaultConfig()
	remote := false
	cfg.Providers = map[string]config.ProviderConfig{
		"openai": {APIFormat: "openai", BaseURL: upstream.URL, APIKey: proxyTestKey, Models: []string{"gpt-6-luna"}, Local: &remote},
	}
	registry, err := NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer registry.Close()
	p, _ := registry.GetProvider("openai")
	if _, err := decide(context.Background(), t, p, proxyTestOAIBody); err != nil {
		t.Fatalf("decision call through proxy: %v", err)
	}
	if _, err := p.ListModels(context.Background()); err != nil {
		t.Fatalf("direct chat-provider call: %v", err)
	}
	asked := proxy.requests()
	if len(asked) != 1 || asked[0] != "POST "+upstream.URL+"/v1/decisions" || upstreamHits.Load() != 1 {
		t.Fatalf("proxy requests %q, upstream hits %d", asked, upstreamHits.Load())
	}
}

func TestOrgOpenAIDecisionsUseGuardedProxy(t *testing.T) {
	for _, scheme := range []string{"http", "https"} {
		t.Run(scheme, func(t *testing.T) {
			proxy := useDecisionProxy(t)
			cache := NewOrgProviderCache(nil, false)
			defer cache.EvictAll()
			org := &tenant.ProviderConfig{APIKey: proxyTestKey, APIFormat: "openai", BaseURL: scheme + "://203.0.113.10", Enabled: true}
			p, err := cache.GetOrCreateWithTenantConfig("org-openai", "openai", proxyTestKey, org)
			if err != nil {
				t.Fatal(err)
			}
			_, err = decide(context.Background(), t, p, proxyTestOAIBody)
			want := "POST http://203.0.113.10/v1/decisions"
			if scheme == "https" {
				want = "CONNECT 203.0.113.10:443"
				if err == nil || proxy.sawKey() {
					t.Fatal("a refused tunnel succeeded or exposed the key")
				}
			} else if err != nil {
				t.Fatal(err)
			}
			if asked := proxy.requests(); len(asked) != 1 || asked[0] != want {
				t.Fatalf("proxy requests %q, want %q", asked, want)
			}
			for _, base := range []string{"http://169.254.169.254", "http://metadata.google.internal", "http://10.0.0.1", "http://127.0.0.1:9", "http://[::1]:9"} {
				org.BaseURL = base
				if _, err := cache.GetOrCreateWithTenantConfig("org-blocked-"+base, "openai", proxyTestKey, org); err == nil {
					t.Errorf("accepted %s with a proxy", base)
				}
			}
			if len(proxy.requests()) != 1 {
				t.Fatal("a blocked destination reached the proxy")
			}
		})
	}
}

func TestOrgOpenAIDecisionProxyChecksEveryRequest(t *testing.T) {
	proxy := useDecisionProxy(t)
	calls := 0
	previous := decisionProxy
	decisionProxy = func(r *http.Request) (*url.URL, error) {
		calls++
		return previous(r)
	}
	t.Cleanup(func() { decisionProxy = previous })
	cache := NewOrgProviderCache(nil, false)
	defer cache.EvictAll()
	p, err := cache.GetOrCreateWithTenantConfig("org-pooled", "openai", proxyTestKey,
		&tenant.ProviderConfig{APIFormat: "openai", BaseURL: "http://203.0.113.10"})
	if err != nil {
		t.Fatal(err)
	}
	for range 2 {
		if _, err := decide(context.Background(), t, p, proxyTestOAIBody); err != nil {
			t.Fatal(err)
		}
	}
	if calls != 2 || len(proxy.requests()) != 2 || !strings.HasSuffix(proxy.requests()[1], "/v1/decisions") {
		t.Fatalf("proxy selections %d, requests %q", calls, proxy.requests())
	}
}
