package providers

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"os/exec"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	proxyTestKey     = "synthetic-proxied-provider-key"
	proxyTestSysBody = `{"model":"jev-latest","state":{"text":"private-state"},"questions":{"q":{"type":"noul","instructions":"private-instructions"}}}`
	proxyTestOAIBody = `{"model":"gpt-6-luna","input":"private-input","questions":[{"type":"predicate","name":"q","instructions":"private-instructions"}]}`
	proxyTestAnswer  = `{"model":"jev-1.13.0","answers":{"q":{"type":"noul","noul":0.9}},"usage":{"input_tokens":10,"output_tokens":0}}`
)

// decisionTestProxy is a forward proxy that answers a plain request with a
// System One answer itself and refuses a CONNECT. It records what it was asked
// for (the absolute URL, or CONNECT and the authority) and every header.
type decisionTestProxy struct {
	*httptest.Server
	mu      sync.Mutex
	asked   []string
	headers []http.Header
}

func newDecisionTestProxy(t *testing.T) *decisionTestProxy {
	t.Helper()
	p := &decisionTestProxy{}
	p.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		p.mu.Lock()
		if r.Method == http.MethodConnect {
			p.asked = append(p.asked, "CONNECT "+r.Host)
		} else {
			p.asked = append(p.asked, r.Method+" "+r.URL.String())
		}
		p.headers = append(p.headers, r.Header.Clone())
		p.mu.Unlock()
		if r.Method == http.MethodConnect {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		io.WriteString(w, proxyTestAnswer)
	}))
	t.Cleanup(p.Close)
	return p
}

func (p *decisionTestProxy) requests() []string {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]string(nil), p.asked...)
}

func (p *decisionTestProxy) sawKey() bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	for _, h := range p.headers {
		for _, values := range h {
			for _, v := range values {
				if strings.Contains(v, proxyTestKey) {
					return true
				}
			}
		}
	}
	return false
}

// useDecisionProxy sends every decision call through a test proxy, whatever
// its destination: unlike the environment's proxy, it proxies loopback too.
func useDecisionProxy(t *testing.T) *decisionTestProxy {
	t.Helper()
	p := newDecisionTestProxy(t)
	u, err := url.Parse(p.URL)
	if err != nil {
		t.Fatal(err)
	}
	orig := decisionProxy
	decisionProxy = http.ProxyURL(u)
	t.Cleanup(func() { decisionProxy = orig })
	return p
}

// decide sends one decision call in the provider's own format. A call that
// should have gone to the test proxy fails fast instead of waiting out the
// provider's timeout on an unreachable test address.
func decide(ctx context.Context, t *testing.T, p Provider, body string) (*models.DecisionResponse, error) {
	t.Helper()
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	req, apiErr := models.ParseDecisionRequest([]byte(body))
	if apiErr != nil {
		t.Fatal(apiErr)
	}
	if sp, ok := p.(SystemOneProvider); ok {
		return sp.SystemOne(ctx, req)
	}
	return p.(DecisionsProvider).CreateDecision(ctx, req)
}

// The providers built for an internal caller's key go through the operator's
// proxy. To an https vendor the proxy is asked only to CONNECT: the key and
// the request content travel inside the tunnel.
func TestKeyedDecisionProvidersUseTheProxy(t *testing.T) {
	proxy := useDecisionProxy(t)
	for _, tt := range []struct {
		dialect, base, body string
		want                string
	}{
		{"typesafe", "", proxyTestSysBody, "CONNECT api.typesafe.ai:443"},
		{"perplexity", "", proxyTestSysBody, "CONNECT api.perplexity.ai:443"},
		{"openai", "", proxyTestOAIBody, "CONNECT api.openai.com:443"},
		{"typesafe", "http://203.0.113.10", proxyTestSysBody, "POST http://203.0.113.10/v1/systemone"},
	} {
		t.Run(tt.want, func(t *testing.T) {
			p, err := NewKeyedDecisionProvider(tt.dialect, tt.base, proxyTestKey)
			if err != nil {
				t.Fatal(err)
			}
			defer p.Close()
			_, callErr := decide(context.Background(), t, p, tt.body)
			asked := proxy.requests()
			if len(asked) == 0 || asked[len(asked)-1] != tt.want {
				t.Fatalf("the proxy was asked for %q, want %q last", asked, tt.want)
			}
			if strings.HasPrefix(tt.want, "CONNECT") {
				if callErr == nil {
					t.Fatal("the call succeeded although the proxy refused the tunnel")
				}
				if proxy.sawKey() {
					t.Fatal("the provider key reached the proxy outside the tunnel")
				}
			} else if callErr != nil {
				t.Fatalf("the call through the proxy failed: %v", callErr)
			}
		})
	}
}

// An org's System One provider goes through the proxy too, to the base_url it
// saved. A base_url that names an internal address is refused whether or not
// a proxy is set, before anything reaches the proxy.
func TestOrgDecisionProviderUsesTheProxyAndKeepsItsChecks(t *testing.T) {
	proxy := useDecisionProxy(t)
	cache := NewOrgProviderCache(nil, false)
	defer cache.EvictAll()
	org := func(base string) *tenant.ProviderConfig {
		return &tenant.ProviderConfig{APIKey: proxyTestKey, APIFormat: "systemone", BaseURL: base, Models: []string{"jev-latest"}, Enabled: true}
	}

	p, err := cache.GetOrCreateWithTenantConfig("org-proxied", "typesafe", proxyTestKey, org("http://203.0.113.10"))
	if err != nil {
		t.Fatal(err)
	}
	resp, err := decide(context.Background(), t, p, proxyTestSysBody)
	if err != nil || resp == nil {
		t.Fatalf("the org call through the proxy: %v", err)
	}
	if asked := proxy.requests(); len(asked) != 1 || asked[0] != "POST http://203.0.113.10/v1/systemone" {
		t.Fatalf("the proxy was asked for %q", asked)
	}

	for _, base := range []string{"http://169.254.169.254", "http://metadata.google.internal", "http://10.0.0.1", "http://127.0.0.1:9", "http://[::1]:9"} {
		if _, err := cache.GetOrCreateWithTenantConfig("org-ssrf", "typesafe", proxyTestKey, org(base)); err == nil {
			t.Errorf("base_url %s was accepted with a proxy set", base)
		}
	}
	if asked := proxy.requests(); len(asked) != 1 {
		t.Fatalf("a refused base_url reached the proxy: %q", asked)
	}
}

// Chat providers do not use the proxy; System One providers from config.yaml
// do. The test proxy takes loopback, so a call that skips it lands on the
// upstream.
func TestOnlyDecisionProvidersUseTheProxy(t *testing.T) {
	proxy := useDecisionProxy(t)
	var upstreamHits atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upstreamHits.Add(1)
		io.WriteString(w, `{"object":"list","data":[]}`)
	}))
	defer upstream.Close()

	systemOne, err := createProvider("typesafe", config.ProviderConfig{APIFormat: "systemone", BaseURL: upstream.URL, APIKey: proxyTestKey})
	if err != nil {
		t.Fatal(err)
	}
	defer systemOne.Close()
	if _, err := decide(context.Background(), t, systemOne, proxyTestSysBody); err != nil {
		t.Fatalf("System One call: %v", err)
	}
	if asked := proxy.requests(); len(asked) != 1 || asked[0] != "POST "+upstream.URL+"/v1/systemone" {
		t.Fatalf("the proxy was asked for %q", asked)
	}

	chat, err := createProvider("openai", config.ProviderConfig{APIFormat: "openai", BaseURL: upstream.URL, APIKey: proxyTestKey})
	if err != nil {
		t.Fatal(err)
	}
	defer chat.Close()
	if _, err := chat.ListModels(context.Background()); err != nil {
		t.Fatalf("chat provider call: %v", err)
	}
	if upstreamHits.Load() != 1 || len(proxy.requests()) != 1 {
		t.Fatalf("upstream hits %d, proxy asked for %q: the chat provider must connect directly", upstreamHits.Load(), proxy.requests())
	}
}

// proxyEnvChild marks the child process TestDecisionProxyFromEnvironment runs
// in.
const proxyEnvChild = "AGENTCC_TEST_DECISION_PROXY_CHILD"

// Decision calls follow HTTP_PROXY, HTTPS_PROXY and NO_PROXY as set in the
// gateway's environment. net/http reads them once per process, so the test
// runs in a fresh one.
func TestDecisionProxyFromEnvironment(t *testing.T) {
	if os.Getenv(proxyEnvChild) == "" {
		cmd := exec.Command(os.Args[0], "-test.run=^TestDecisionProxyFromEnvironment$", "-test.count=1", "-test.v")
		cmd.Env = append(os.Environ(), proxyEnvChild+"=1")
		out, err := cmd.CombinedOutput()
		if err != nil || !strings.Contains(string(out), "--- PASS: TestDecisionProxyFromEnvironment") {
			t.Fatalf("child process: %v\n%s", err, out)
		}
		return
	}

	proxy := newDecisionTestProxy(t)
	for _, name := range []string{"HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"} {
		t.Setenv(name, proxy.URL)
	}
	t.Setenv("NO_PROXY", "203.0.113.20")
	t.Setenv("no_proxy", "203.0.113.20")
	t.Setenv("REQUEST_METHOD", "")

	keyed, err := NewKeyedDecisionProvider("typesafe", "http://203.0.113.10", proxyTestKey)
	if err != nil {
		t.Fatal(err)
	}
	defer keyed.Close()
	if _, err := decide(context.Background(), t, keyed, proxyTestSysBody); err != nil {
		t.Fatalf("keyed call through HTTP_PROXY: %v", err)
	}

	cache := NewOrgProviderCache(nil, false)
	defer cache.EvictAll()
	org, err := cache.GetOrCreateWithTenantConfig("org-env", "typesafe", proxyTestKey,
		&tenant.ProviderConfig{APIKey: proxyTestKey, APIFormat: "systemone", BaseURL: "http://203.0.113.11", Models: []string{"jev-latest"}, Enabled: true})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := decide(context.Background(), t, org, proxyTestSysBody); err != nil {
		t.Fatalf("org call through HTTP_PROXY: %v", err)
	}

	// A host in NO_PROXY is dialled directly. Nothing listens there, so the
	// call fails; what matters is that the proxy never hears of it.
	direct, err := NewKeyedDecisionProvider("typesafe", "http://203.0.113.20", proxyTestKey)
	if err != nil {
		t.Fatal(err)
	}
	defer direct.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	if _, err := decide(ctx, t, direct, proxyTestSysBody); err == nil {
		t.Fatal("a call to the NO_PROXY host was answered: it went through the proxy")
	}

	want := []string{"POST http://203.0.113.10/v1/systemone", "POST http://203.0.113.11/v1/systemone"}
	for _, orgProvider := range []bool{false, true} {
		for _, scheme := range []string{"http", "https"} {
			base := scheme + "://203.0.113.12"
			var p Provider
			if orgProvider {
				p, err = cache.GetOrCreateWithTenantConfig("org-env-"+scheme, "openai", proxyTestKey,
					&tenant.ProviderConfig{APIFormat: "openai", BaseURL: base})
			} else {
				p, err = createProvider("openai", config.ProviderConfig{APIFormat: "openai", BaseURL: base, APIKey: proxyTestKey})
			}
			if err != nil {
				t.Fatal(err)
			}
			_, callErr := decide(context.Background(), t, p, proxyTestOAIBody)
			p.Close()
			if scheme == "http" {
				if callErr != nil {
					t.Fatalf("OpenAI decision through HTTP_PROXY (org=%v): %v", orgProvider, callErr)
				}
				want = append(want, "POST "+base+"/v1/decisions")
			} else {
				if callErr == nil {
					t.Fatal("the refused HTTPS_PROXY tunnel succeeded")
				}
				want = append(want, "CONNECT 203.0.113.12:443")
			}
		}
		var p Provider
		if orgProvider {
			p, err = cache.GetOrCreateWithTenantConfig("org-no-proxy", "openai", proxyTestKey,
				&tenant.ProviderConfig{APIFormat: "openai", BaseURL: "http://203.0.113.20"})
		} else {
			p, err = createProvider("openai", config.ProviderConfig{APIFormat: "openai", BaseURL: "http://203.0.113.20", APIKey: proxyTestKey})
		}
		if err != nil {
			t.Fatal(err)
		}
		ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
		_, callErr := decide(ctx, t, p, proxyTestOAIBody)
		cancel()
		p.Close()
		if callErr == nil {
			t.Fatal("OpenAI NO_PROXY request reached the proxy")
		}
	}
	if got := proxy.requests(); strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("the proxy was asked for %q, want %q", got, want)
	}
}
