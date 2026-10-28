package providers

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// ---------------------------------------------------------------------------
// resolveOrgConfig: the tenant's path prefix has to survive a provider that is
// also present in config.yaml, which is the path that inherits the YAML prefix.
// ---------------------------------------------------------------------------

func TestResolveOrgConfig_PathPrefix(t *testing.T) {
	ptr := func(s string) *string { return &s }

	tests := []struct {
		name      string
		yaml      *string
		tenantCfg *tenant.ProviderConfig
		want      string
	}{
		{
			name:      "no tenant config keeps the yaml prefix",
			yaml:      ptr("/v1"),
			tenantCfg: nil,
			want:      "/v1",
		},
		{
			name:      "tenant that states nothing keeps the yaml prefix",
			yaml:      ptr("/v1"),
			tenantCfg: &tenant.ProviderConfig{},
			want:      "/v1",
		},
		{
			name:      "explicitly empty tenant prefix overrides the yaml one",
			yaml:      ptr("/v1"),
			tenantCfg: &tenant.ProviderConfig{APIPathPrefix: ptr("")},
			want:      "",
		},
		{
			name:      "non-empty tenant prefix overrides the yaml one",
			yaml:      ptr("/v1"),
			tenantCfg: &tenant.ProviderConfig{APIPathPrefix: ptr("/openai/v1")},
			want:      "/openai/v1",
		},
		{
			name:      "tenant prefix applies where yaml states none",
			yaml:      nil,
			tenantCfg: &tenant.ProviderConfig{APIPathPrefix: ptr("")},
			want:      "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			baseCfg := config.ProviderConfig{
				BaseURL:       "https://api.perplexity.ai",
				APIKey:        "yaml-key",
				APIFormat:     "openai",
				APIPathPrefix: tt.yaml,
			}

			got := resolveOrgConfig(baseCfg, "org-key", tt.tenantCfg)

			if got.EffectiveAPIPathPrefix() != tt.want {
				t.Errorf("EffectiveAPIPathPrefix() = %q, want %q",
					got.EffectiveAPIPathPrefix(), tt.want)
			}
			if got.APIKey != "org-key" {
				t.Errorf("APIKey = %q, want the org's key", got.APIKey)
			}
			if baseCfg.APIPathPrefix != tt.yaml {
				t.Error("resolveOrgConfig mutated the shared base config")
			}
		})
	}
}

// The endpoint the proxy actually builds, so an empty prefix on a YAML-backed
// provider stops sending requests to /v1.
func TestResolveOrgConfig_EmptyPrefixDropsVersionSegment(t *testing.T) {
	empty := ""
	v1 := "/v1"
	baseCfg := config.ProviderConfig{
		BaseURL:       "https://api.perplexity.ai",
		APIFormat:     "openai",
		APIPathPrefix: &v1,
	}

	orgCfg := resolveOrgConfig(baseCfg, "org-key", &tenant.ProviderConfig{APIPathPrefix: &empty})

	const want = "https://api.perplexity.ai/chat/completions"
	if got := orgCfg.EndpointURL("/v1/chat/completions"); got != want {
		t.Errorf("EndpointURL() = %q, want %q", got, want)
	}
}

// ---------------------------------------------------------------------------
// An org's own base_url wins over a config.yaml entry with the same provider
// ID, and the org's key never reaches the operator's upstream.
// ---------------------------------------------------------------------------

// stubLookupIP makes validateBaseURL see the given addresses for host names.
func stubLookupIP(t *testing.T, answers map[string][]string) {
	t.Helper()
	orig := lookupIP
	lookupIP = func(host string) ([]net.IP, error) {
		addrs, ok := answers[host]
		if !ok {
			return nil, &net.DNSError{Err: "no such host", Name: host, IsNotFound: true}
		}
		ips := make([]net.IP, len(addrs))
		for i, a := range addrs {
			ips[i] = net.ParseIP(a)
		}
		return ips, nil
	}
	t.Cleanup(func() { lookupIP = orig })
}

// allowLoopbackDial lets org providers connect to a test server on loopback,
// for tests of something other than the dial check.
func allowLoopbackDial(t *testing.T) {
	t.Helper()
	orig := orgDialContext
	orgDialContext = func(bool) func(context.Context, string, string) (net.Conn, error) { return nil }
	t.Cleanup(func() { orgDialContext = orig })
}

// recordingUpstream is an OpenAI-compatible server that records the bearer
// token of every request it receives.
func recordingUpstream(t *testing.T) (*httptest.Server, *atomic.Int32, *atomic.Value) {
	t.Helper()
	var hits atomic.Int32
	var lastAuth atomic.Value
	lastAuth.Store("")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		lastAuth.Store(r.Header.Get("Authorization"))
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(models.ChatCompletionResponse{
			ID:      "chatcmpl-test",
			Object:  "chat.completion",
			Model:   "mock-gw",
			Choices: []models.Choice{{Index: 0, Message: models.Message{Role: "assistant", Content: json.RawMessage(`"ok"`)}, FinishReason: "stop"}},
		})
	}))
	t.Cleanup(srv.Close)
	return srv, &hits, &lastAuth
}

func TestGetOrCreate_TenantBaseURLWinsOverConfigYAML(t *testing.T) {
	operator, operatorHits, _ := recordingUpstream(t)
	orgUpstream, orgHits, orgAuth := recordingUpstream(t)

	// The org's upstream is reached as "localhost". DNS is stubbed for the
	// URL check only, so the check sees a public address, and the dial check
	// is off so the dial still lands on the test server.
	_, port, _ := net.SplitHostPort(strings.TrimPrefix(orgUpstream.URL, "http://"))
	stubLookupIP(t, map[string][]string{"localhost": {"203.0.113.7"}})
	allowLoopbackDial(t)

	cache := NewOrgProviderCache(map[string]config.ProviderConfig{
		"openai": {BaseURL: operator.URL, APIKey: "operator-key", APIFormat: "openai", Models: []string{"gpt-4o"}},
	}, false)
	p, err := cache.GetOrCreateWithTenantConfig("org-1", "openai", "org-key", &tenant.ProviderConfig{
		APIKey:    "org-key",
		BaseURL:   "http://localhost:" + port,
		APIFormat: "openai",
		Models:    []string{"mock-gw"},
		Enabled:   true,
	})
	if err != nil {
		t.Fatalf("GetOrCreateWithTenantConfig: %v", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if _, err := p.ChatCompletion(ctx, &models.ChatCompletionRequest{
		Model:    "mock-gw",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
	}); err != nil {
		t.Fatalf("ChatCompletion: %v", err)
	}

	if got := orgHits.Load(); got != 1 {
		t.Errorf("org upstream hits = %d, want 1", got)
	}
	if got := orgAuth.Load(); got != "Bearer org-key" {
		t.Errorf("org upstream saw Authorization %q, want the org's key", got)
	}
	if got := operatorHits.Load(); got != 0 {
		t.Errorf("operator upstream hits = %d, want 0: the org's request went to config.yaml's base_url", got)
	}
}

// The URL check runs once, when the provider is built. A host that resolved
// to a public address then but now points somewhere the gateway refuses (DNS
// rebinding) must still not receive the org's key: the dialer checks the
// address it connects to.
func TestGetOrCreate_DialRefusesAHostThatNoLongerResolvesPublic(t *testing.T) {
	orgUpstream, orgHits, _ := recordingUpstream(t)
	_, port, _ := net.SplitHostPort(strings.TrimPrefix(orgUpstream.URL, "http://"))
	stubLookupIP(t, map[string][]string{"localhost": {"203.0.113.7"}})

	for _, allowPrivate := range []bool{false, true} {
		p, err := NewOrgProviderCache(nil, allowPrivate).GetOrCreateWithTenantConfig("org-1", "custom", "org-key", &tenant.ProviderConfig{
			APIKey:    "org-key",
			BaseURL:   "http://localhost:" + port,
			APIFormat: "openai",
			Enabled:   true,
		})
		if err != nil {
			t.Fatalf("GetOrCreateWithTenantConfig: %v", err)
		}

		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		_, err = p.ChatCompletion(ctx, &models.ChatCompletionRequest{
			Model:    "mock-gw",
			Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
		})
		cancel()
		if err == nil {
			t.Errorf("allowPrivate=%v: ChatCompletion succeeded against a loopback upstream", allowPrivate)
			continue
		}
		// The error reaches the API caller, who chose the host: the address
		// it resolved to would map the operator's network for them.
		if msg := err.Error(); strings.Contains(msg, "127.0.0.1") || strings.Contains(msg, "::1") {
			t.Errorf("allowPrivate=%v: error %q names the address the host resolved to", allowPrivate, msg)
		}
	}
	if got := orgHits.Load(); got != 0 {
		t.Errorf("loopback upstream received %d request(s) carrying the org's key", got)
	}
}

// A refused org base_url must fail the build; it must not fall back to the
// config.yaml entry and carry the org's key there.
func TestGetOrCreate_RefusedTenantBaseURLDoesNotFallBackToConfigYAML(t *testing.T) {
	cache := NewOrgProviderCache(map[string]config.ProviderConfig{
		"openai": {BaseURL: "https://api.openai.com", APIKey: "operator-key", APIFormat: "openai"},
	}, false)
	p, err := cache.GetOrCreateWithTenantConfig("org-1", "openai", "org-key", &tenant.ProviderConfig{
		APIKey:  "org-key",
		BaseURL: "http://10.20.30.40:8080",
		Enabled: true,
	})
	if err == nil {
		t.Fatalf("got provider %v, want the private base_url refused", p)
	}
	var urlErr *BaseURLError
	if !errors.As(err, &urlErr) || urlErr.Reason != BaseURLPrivate {
		t.Fatalf("err = %v, want a BaseURLPrivate *BaseURLError", err)
	}
	if cache.Count() != 0 {
		t.Errorf("cache holds %d providers after a refused build", cache.Count())
	}
}

func TestGetOrCreate_PrivateBaseURLNeedsOptIn(t *testing.T) {
	stubLookupIP(t, map[string][]string{"mock-llm": {"172.20.0.5"}})
	tenantCfg := &tenant.ProviderConfig{APIKey: "org-key", BaseURL: "http://mock-llm:8080", Enabled: true}

	if _, err := NewOrgProviderCache(nil, false).GetOrCreateWithTenantConfig("org-1", "custom", "org-key", tenantCfg); err == nil {
		t.Fatal("private base_url accepted without the opt-in")
	}
	if _, err := NewOrgProviderCache(nil, true).GetOrCreateWithTenantConfig("org-1", "custom", "org-key", tenantCfg); err != nil {
		t.Fatalf("private base_url refused with the opt-in: %v", err)
	}
}

func TestResolveOrgConfig_NeverInheritsOperatorCredentials(t *testing.T) {
	baseCfg := config.ProviderConfig{
		BaseURL:            "https://bedrock-runtime.us-east-1.amazonaws.com",
		APIKey:             "operator-key",
		APIFormat:          "bedrock",
		AWSAccessKeyID:     "OPERATORKEYID",
		AWSSecretAccessKey: "operator-secret",
		AWSSessionToken:    "operator-session",
		AWSRegion:          "us-east-1",
		CredentialsFile:    "/secrets/operator-sa.json",
		ServiceAccountJSON: `{"operator":true}`,
	}

	t.Run("no tenant config", func(t *testing.T) {
		got := resolveOrgConfig(baseCfg, "org-key", nil)
		if got.AWSAccessKeyID != "" || got.AWSSecretAccessKey != "" || got.AWSSessionToken != "" ||
			got.CredentialsFile != "" || got.ServiceAccountJSON != "" {
			t.Errorf("operator credentials carried over: %+v", got)
		}
	})

	t.Run("tenant credentials win", func(t *testing.T) {
		got := resolveOrgConfig(baseCfg, "", &tenant.ProviderConfig{
			AWSAccessKeyID:     "ORGKEYID",
			AWSSecretAccessKey: "org-secret",
			AWSRegion:          "eu-west-1",
		})
		if got.AWSAccessKeyID != "ORGKEYID" || got.AWSSecretAccessKey != "org-secret" {
			t.Errorf("AWS keys = %q/%q, want the org's", got.AWSAccessKeyID, got.AWSSecretAccessKey)
		}
		if got.AWSSessionToken != "" || got.CredentialsFile != "" || got.ServiceAccountJSON != "" {
			t.Errorf("operator credentials carried over: %+v", got)
		}
		if got.AWSRegion != "eu-west-1" {
			t.Errorf("AWSRegion = %q, want the org's", got.AWSRegion)
		}
	})

	if baseCfg.AWSAccessKeyID != "OPERATORKEYID" {
		t.Error("resolveOrgConfig mutated the shared base config")
	}
}

func TestResolveOrgConfig_TenantSettingsWin(t *testing.T) {
	baseCfg := config.ProviderConfig{
		BaseURL:        "https://api.anthropic.com",
		APIFormat:      "anthropic",
		DefaultTimeout: 120 * time.Second,
		MaxConcurrent:  50,
		ConnPoolSize:   50,
		Models:         []string{"claude-operator"},
	}

	got := resolveOrgConfig(baseCfg, "org-key", &tenant.ProviderConfig{
		Timeout:       30,
		MaxConcurrent: 5,
		ConnPoolSize:  7,
		Models:        []string{"claude-org"},
	})
	if got.DefaultTimeout != 30*time.Second || got.MaxConcurrent != 5 || got.ConnPoolSize != 7 {
		t.Errorf("timeout/concurrency/pool = %v/%d/%d, want the org's", got.DefaultTimeout, got.MaxConcurrent, got.ConnPoolSize)
	}
	if len(got.Models) != 1 || got.Models[0] != "claude-org" {
		t.Errorf("Models = %v, want the org's", got.Models)
	}
	if got.BaseURL != baseCfg.BaseURL || got.APIFormat != baseCfg.APIFormat {
		t.Errorf("upstream = %s/%s, want config.yaml's when the org names none", got.BaseURL, got.APIFormat)
	}

	// Unset tenant fields leave config.yaml's values alone.
	got = resolveOrgConfig(baseCfg, "org-key", &tenant.ProviderConfig{})
	if got.DefaultTimeout != 120*time.Second || got.MaxConcurrent != 50 || got.Models[0] != "claude-operator" {
		t.Errorf("unset tenant fields overrode config.yaml: %+v", got)
	}
}

// ---------------------------------------------------------------------------
// validateBaseURL: which org base URLs the gateway will call.
// ---------------------------------------------------------------------------

func TestValidateBaseURL(t *testing.T) {
	// The cases are shared with the backend's test of the same policy.
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "api_contracts", "gateway", "provider-url-policy.json"))
	if err != nil {
		t.Fatalf("reading the shared URL policy cases: %v", err)
	}
	type urlCase struct {
		URL   string `json:"url"`
		Class string `json:"class"`
	}
	var policy struct {
		DNS  map[string][]string `json:"dns"`
		URLs []urlCase           `json:"urls"`
	}
	if err := json.Unmarshal(raw, &policy); err != nil {
		t.Fatalf("parsing the shared URL policy cases: %v", err)
	}
	// Cases for the gateway alone: an empty base_url means the provider's
	// default endpoint, and a resolver can answer with no addresses, which
	// the backend's getaddrinfo cannot.
	policy.DNS["empty-answer"] = nil
	policy.URLs = append(policy.URLs, urlCase{"", "public"}, urlCase{"http://empty-answer", "unresolvable"})
	stubLookupIP(t, policy.DNS)

	const pass = -1
	reasons := map[string]BaseURLRejection{
		"public":       pass,
		"private":      BaseURLPrivate,
		"loopback":     BaseURLLoopback,
		"forbidden":    BaseURLForbidden,
		"invalid":      BaseURLInvalid,
		"unresolvable": BaseURLUnresolvable,
	}

	check := func(t *testing.T, url string, allowPrivate bool, want BaseURLRejection) {
		t.Helper()
		err := validateBaseURL(url, allowPrivate)
		if want == pass {
			if err != nil {
				t.Errorf("validateBaseURL(%q, allowPrivate=%v) = %v, want nil", url, allowPrivate, err)
			}
			return
		}
		var urlErr *BaseURLError
		if !errors.As(err, &urlErr) {
			t.Errorf("validateBaseURL(%q, allowPrivate=%v) = %v, want a *BaseURLError", url, allowPrivate, err)
			return
		}
		if urlErr.Reason != want {
			t.Errorf("validateBaseURL(%q, allowPrivate=%v) reason = %d (%v), want %d", url, allowPrivate, urlErr.Reason, err, want)
		}
	}
	for _, tt := range policy.URLs {
		want, ok := reasons[tt.Class]
		if !ok {
			t.Fatalf("%s: unknown class %q", tt.URL, tt.Class)
		}
		t.Run(tt.URL, func(t *testing.T) {
			check(t, tt.URL, false, want)
			if want == BaseURLPrivate {
				want = pass
			}
			check(t, tt.URL, true, want)
		})
	}
}

// The message an API caller sees names the fix for a private address and never
// echoes the resolved IP.
func TestBaseURLError_PublicMessage(t *testing.T) {
	stubLookupIP(t, map[string][]string{"mock-llm": {"172.20.0.5"}})

	var urlErr *BaseURLError
	if !errors.As(validateBaseURL("http://mock-llm:8080", false), &urlErr) {
		t.Fatal("private base_url not refused")
	}
	msg := urlErr.PublicMessage()
	if !strings.Contains(msg, config.EnvAllowPrivateProviderURLs+"=true") {
		t.Errorf("PublicMessage() = %q, want it to name %s", msg, config.EnvAllowPrivateProviderURLs)
	}
	if strings.Contains(msg, "172.20.0.5") {
		t.Errorf("PublicMessage() = %q leaks the resolved address", msg)
	}
	if !strings.Contains(urlErr.Error(), "172.20.0.5") {
		t.Errorf("Error() = %q, want the resolved address for the operator's logs", urlErr.Error())
	}
}
