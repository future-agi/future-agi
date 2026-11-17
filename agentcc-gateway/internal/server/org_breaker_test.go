package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// keyedUpstream answers by the provider key it is called with, so one
// upstream can serve several orgs' own keys. Keys in failing get the status
// in failStatus (503 by default).
type keyedUpstream struct {
	mu         sync.Mutex
	calls      map[string]int
	failing    map[string]bool
	failStatus int
}

func newKeyedUpstream(t *testing.T, failStatus int, failing ...string) (*keyedUpstream, *httptest.Server) {
	t.Helper()
	if failStatus == 0 {
		failStatus = http.StatusServiceUnavailable
	}
	k := &keyedUpstream{calls: map[string]int{}, failing: map[string]bool{}, failStatus: failStatus}
	for _, key := range failing {
		k.failing[key] = true
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" {
			w.WriteHeader(http.StatusOK)
			return
		}
		key := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		k.mu.Lock()
		k.calls[key]++
		fail := k.failing[key]
		k.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		if fail {
			w.WriteHeader(k.failStatus)
			io.WriteString(w, `{"error":{"message":"synthetic upstream failure","type":"server_error"}}`)
			return
		}
		var request struct {
			Stream bool `json:"stream"`
		}
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Error(err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		if request.Stream {
			w.Header().Set("Content-Type", "text/event-stream")
			io.WriteString(w, "data: {\"id\":\"synthetic\",\"model\":\"adaptive-sample\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"synthetic response\"}}]}\n\ndata: [DONE]\n\n")
			return
		}
		io.WriteString(w, `{"id":"synthetic-completion","object":"chat.completion","model":"adaptive-sample","choices":[{"index":0,"message":{"role":"assistant","content":"synthetic response"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	t.Cleanup(srv.Close)
	return k, srv
}

func (k *keyedUpstream) count(key string) int {
	k.mu.Lock()
	defer k.mu.Unlock()
	return k.calls[key]
}

// Two orgs bring their own key for "primary"; "backup" is operator-only.
// Requests are locked to primary first and fail over to backup.
func orgBreakerGateway(t *testing.T, failing ...string) (*Server, *httptest.Server, *keyedUpstream) {
	t.Helper()
	return orgBreakerGatewayWithStatus(t, 0, failing...)
}

// orgBreakerGatewayWithStatus is orgBreakerGateway with the failing keys'
// status. The breaker keeps the default on_status_codes (429 and 5xx).
func orgBreakerGatewayWithStatus(t *testing.T, failStatus int, failing ...string) (*Server, *httptest.Server, *keyedUpstream) {
	t.Helper()
	return orgBreakerGatewayWithConfig(t, failStatus, nil, failing...)
}

func orgBreakerGatewayWithConfig(t *testing.T, failStatus int, configure func(*config.Config), failing ...string) (*Server, *httptest.Server, *keyedUpstream) {
	t.Helper()
	keyed, primary := newKeyedUpstream(t, failStatus, failing...)
	var backupCalls atomic.Int64
	backup := adaptiveUpstream(t, false, 0, &backupCalls)
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{
		"primary": {BaseURL: primary.URL, APIKey: "synthetic-operator-key", APIFormat: "openai", Models: []string{"adaptive-sample"}},
		"backup":  {BaseURL: backup.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
	}
	cfg.Routing = config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets:         map[string][]config.RoutingTargetConfig{"adaptive-sample": {{Provider: "primary"}, {Provider: "backup"}}},
		Failover:        config.FailoverConfig{Enabled: true, MaxAttempts: 2},
		CircuitBreaker:  config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 2, Cooldown: time.Hour},
		ProviderLock:    config.ProviderLockConfig{Enabled: true},
	}
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "org-a", Key: "synthetic-org-a-probe", KeyType: "byok", Metadata: map[string]string{"org_id": "org-a"}},
		{Name: "org-b", Key: "synthetic-org-b-probe", KeyType: "byok", Metadata: map[string]string{"org_id": "org-b"}},
		{Name: "operator", Key: "synthetic-operator-probe", KeyType: "internal"},
	}
	if configure != nil {
		configure(cfg)
	}
	srv, gateway := adaptiveServer(t, cfg)
	store := tenant.NewStore()
	for _, org := range []string{"org-a", "org-b"} {
		store.Set(org, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
			"primary": {Enabled: true, APIKey: "synthetic-" + org + "-key", Models: []string{"different-model"}},
		}})
	}
	orgCache := providers.NewOrgProviderCache(cfg.Providers, true)
	t.Cleanup(orgCache.EvictAll)
	srv.handlers.tenantStore, srv.handlers.orgProviderCache = store, orgCache
	srv.handlers.keyStore = auth.NewKeyStore(cfg.Auth)
	return srv, gateway, keyed
}

// orgBreakerGatewayOwnBackup is orgBreakerGatewayWithStatus with the orgs'
// own providers only: on the line a customer (byok) key stays on its org's
// own providers, and under a provider lock its request makes one attempt on
// the org's provider for the locked target. With ownBackup each org also
// brings a working key for "backup", which a request locked to primary does
// not reach. backupCalls counts every call the backup upstream gets, the
// orgs' own backup keys and the operator's alike.
func orgBreakerGatewayOwnBackup(t *testing.T, failStatus int, ownBackup bool, failing ...string) (*Server, *httptest.Server, *keyedUpstream, *atomic.Int64) {
	t.Helper()
	keyed, primary := newKeyedUpstream(t, failStatus, failing...)
	backupCalls := &atomic.Int64{}
	backup := adaptiveUpstream(t, false, 0, backupCalls)
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{
		"primary": {BaseURL: primary.URL, APIKey: "synthetic-operator-key", APIFormat: "openai", Models: []string{"adaptive-sample"}},
		"backup":  {BaseURL: backup.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
	}
	cfg.Routing = config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets:         map[string][]config.RoutingTargetConfig{"adaptive-sample": {{Provider: "primary"}, {Provider: "backup"}}},
		Failover:        config.FailoverConfig{Enabled: true, MaxAttempts: 2},
		CircuitBreaker:  config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 2, Cooldown: time.Hour},
		ProviderLock:    config.ProviderLockConfig{Enabled: true},
	}
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "org-a", Key: "synthetic-org-a-probe", KeyType: "byok", Metadata: map[string]string{"org_id": "org-a"}},
		{Name: "org-b", Key: "synthetic-org-b-probe", KeyType: "byok", Metadata: map[string]string{"org_id": "org-b"}},
		{Name: "operator", Key: "synthetic-operator-probe", KeyType: "internal"},
	}
	srv, gateway := adaptiveServer(t, cfg)
	store := tenant.NewStore()
	for _, org := range []string{"org-a", "org-b"} {
		own := map[string]*tenant.ProviderConfig{
			"primary": {Enabled: true, APIKey: "synthetic-" + org + "-key", Models: []string{"different-model"}},
		}
		if ownBackup {
			own["backup"] = &tenant.ProviderConfig{Enabled: true, APIKey: "synthetic-" + org + "-backup-key", Models: []string{"different-model"}}
		}
		store.Set(org, &tenant.OrgConfig{Providers: own})
	}
	orgCache := providers.NewOrgProviderCache(cfg.Providers, true)
	t.Cleanup(orgCache.EvictAll)
	srv.handlers.tenantStore, srv.handlers.orgProviderCache = store, orgCache
	srv.handlers.keyStore = auth.NewKeyStore(cfg.Auth)
	return srv, gateway, keyed, backupCalls
}

func sendOrgRequests(t *testing.T, gateway *httptest.Server, probe string, n int) {
	t.Helper()
	for i := 0; i < n; i++ {
		if status, body := adaptiveHTTP(t, gateway, "adaptive-sample", probe, false, http.Header{"X-Agentcc-Provider-Lock": {"primary"}}); status != http.StatusOK || !strings.Contains(body, "synthetic response") {
			t.Fatalf("%s request %d: status=%d body=%s", probe, i, status, body)
		}
	}
}

// sendFailingOrgRequests sends n requests from probe locked to primary and
// checks that each answers 502 with the org provider's error, which names
// the provider's HTTP status.
func sendFailingOrgRequests(t *testing.T, gateway *httptest.Server, probe string, n, providerStatus int) {
	t.Helper()
	for i := 0; i < n; i++ {
		status, body := adaptiveHTTP(t, gateway, "adaptive-sample", probe, false, http.Header{"X-Agentcc-Provider-Lock": {"primary"}})
		if status != http.StatusBadGateway || !strings.Contains(body, fmt.Sprintf("HTTP %d", providerStatus)) || strings.Contains(body, "synthetic response") {
			t.Fatalf("%s request %d: status=%d body=%s; want 502 with the org provider's HTTP %d", probe, i, status, body, providerStatus)
		}
	}
}

// requireNoOperatorCalls checks that the operator's primary key and the
// backup upstream (the orgs' own backup keys and the operator's) got no call.
func requireNoOperatorCalls(t *testing.T, keyed *keyedUpstream, backupCalls *atomic.Int64) {
	t.Helper()
	if got := keyed.count("synthetic-operator-key"); got != 0 || backupCalls.Load() != 0 {
		t.Fatalf("a customer key's request went past its org's primary: operator primary=%d backup=%d", got, backupCalls.Load())
	}
}

// orgBreakerOwnRouting gives each org its own providers for primary and
// backup, both listing the model (orgOwnProviders), and org routing that
// turns the org's provider failover and circuit breaker on, threshold 2
// (orgFailoverConfig). A customer key's request without a provider lock runs
// the org's own failover over those providers. The operator's backup reaches
// the same keyed upstream with the operator's key, so every call is counted
// by the key it carries.
func orgBreakerOwnRouting(t *testing.T, failing ...string) (*Server, *httptest.Server, *keyedUpstream) {
	t.Helper()
	srv, gateway, keyed := orgBreakerGatewayWithConfig(t, http.StatusServiceUnavailable, func(c *config.Config) {
		c.Providers["backup"] = c.Providers["primary"]
	}, failing...)
	for _, org := range []string{"org-a", "org-b"} {
		orgCfg := *srv.handlers.tenantStore.Get(org)
		orgCfg.Providers = orgOwnProviders(org)
		orgCfg.Routing = orgFailoverConfig(false, true)
		srv.handlers.tenantStore.Set(org, &orgCfg)
	}
	return srv, gateway, keyed
}

// sendOwnRoutedRequests sends n requests from probe with no provider lock and
// checks that each is served.
func sendOwnRoutedRequests(t *testing.T, gateway *httptest.Server, probe string, n int) {
	t.Helper()
	for i := 0; i < n; i++ {
		if status, body := adaptiveHTTP(t, gateway, "adaptive-sample", probe, false); status != http.StatusOK || !strings.Contains(body, "synthetic response") {
			t.Fatalf("%s request %d: status=%d body=%s", probe, i, status, body)
		}
	}
}

func TestOrgBreakerOverHTTP(t *testing.T) {
	t.Run("one org's failing key opens only its breaker", func(t *testing.T) {
		srv, gateway, keyed := orgBreakerOwnRouting(t, "synthetic-org-a-key")
		// org-a's requests run the org's own failover: a failed attempt on
		// its primary key moves on to its own backup key, and org-a's breaker
		// opens after 2 failures, so later requests skip its primary key.
		sendOwnRoutedRequests(t, gateway, "synthetic-org-a-probe", 12)
		if got := keyed.count("synthetic-org-a-key"); got != 2 {
			t.Fatalf("org-a's own key was called %d times; its breaker should open after 2 failures", got)
		}
		if keyed.count("synthetic-org-a-backup-key") == 0 {
			t.Fatal("org-a's requests did not reach its own backup key")
		}
		if got := keyed.count("synthetic-operator-key"); got != 0 {
			t.Fatalf("org-a's customer key reached the operator's providers: %d calls", got)
		}
		sendOwnRoutedRequests(t, gateway, "synthetic-org-b-probe", 12)
		if got := keyed.count("synthetic-org-b-key"); got < 3 {
			t.Fatalf("org-b's key was called %d times after org-a's breaker opened", got)
		}
		if got := keyed.count("synthetic-operator-key"); got != 0 {
			t.Fatalf("org-b's customer key reached the operator's providers: %d calls", got)
		}
		sendOrgRequests(t, gateway, "synthetic-operator-probe", 4)
		if got := keyed.count("synthetic-operator-key"); got != 4 {
			t.Fatalf("operator traffic to primary after org-a's failures: %d calls, want 4", got)
		}
		health := srv.handlers.healthMonitor.GetHealth("primary")
		if health.CircuitState != "closed" || !health.Healthy || health.ErrorCount != 0 {
			t.Fatalf("org-a's key changed shared health: circuit=%s healthy=%t errors=%d", health.CircuitState, health.Healthy, health.ErrorCount)
		}
	})
	t.Run("a customer key whose own providers all fail gets their error and never reaches the operator", func(t *testing.T) {
		_, gateway, keyed, backupCalls := orgBreakerGatewayOwnBackup(t, 0, false, "synthetic-org-a-key")
		for i := 0; i < 3; i++ {
			status, body := adaptiveHTTP(t, gateway, "adaptive-sample", "synthetic-org-a-probe", false, http.Header{"X-Agentcc-Provider-Lock": {"primary"}})
			if status < 500 || strings.Contains(body, "synthetic response") {
				t.Fatalf("request %d: status=%d body=%s; want the org provider's failure", i, status, body)
			}
		}
		if got := keyed.count("synthetic-operator-key"); got != 0 || backupCalls.Load() != 0 {
			t.Fatalf("a customer key reached the operator's providers: primary=%d backup=%d", got, backupCalls.Load())
		}
		if got := keyed.count("synthetic-org-a-key"); got == 0 {
			t.Fatal("the org's own key was never tried")
		}
	})
	t.Run("provider-wide outage opens every org's breaker", func(t *testing.T) {
		_, gateway, keyed := orgBreakerOwnRouting(t, "synthetic-org-a-key", "synthetic-org-b-key")
		for _, org := range []string{"org-a", "org-b"} {
			sendOwnRoutedRequests(t, gateway, fmt.Sprintf("synthetic-%s-probe", org), 12)
			if got := keyed.count(fmt.Sprintf("synthetic-%s-key", org)); got != 2 {
				t.Fatalf("%s burned %d attempts on a down provider; want 2", org, got)
			}
		}
		if got := keyed.count("synthetic-operator-key"); got != 0 {
			t.Fatalf("a customer key reached the operator's providers: %d calls", got)
		}
	})
}

// An org's own key answering 401 or 403 under a provider lock: each of the
// org's requests makes one attempt on that key and answers 502 with the
// provider's rejection, its breaker does not open, and its own backup key and
// the operator's providers get no call. Other orgs and the shared operator
// health are unaffected. The row stays under the lock: without one the line
// answers a key its provider rejects in the same way, not routed around and
// not counted by the org's breaker (TestOrgFailoverRejectedOwnKeyIsAnswered).
func TestOrgBreakerCountsRejectedOrgKeyByDefault(t *testing.T) {
	for _, status := range []int{http.StatusUnauthorized, http.StatusForbidden} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			srv, gateway, keyed, backupCalls := orgBreakerGatewayOwnBackup(t, status, true, "synthetic-org-a-key")
			sendFailingOrgRequests(t, gateway, "synthetic-org-a-probe", 12, status)
			if got := keyed.count("synthetic-org-a-key"); got != 12 {
				t.Fatalf("org-a's rejected key was called %d times; want 12, one attempt per request", got)
			}
			requireNoOperatorCalls(t, keyed, backupCalls)
			sendOrgRequests(t, gateway, "synthetic-org-b-probe", 12)
			if got := keyed.count("synthetic-org-b-key"); got != 12 {
				t.Fatalf("org-b's key was called %d times after org-a's rejections; want 12", got)
			}
			health := srv.handlers.healthMonitor.GetHealth("primary")
			if health.CircuitState != "closed" || !health.Healthy || health.ErrorCount != 0 {
				t.Fatalf("org-a's key changed shared health: circuit=%s healthy=%t errors=%d", health.CircuitState, health.Healthy, health.ErrorCount)
			}
		})
	}
}
