package server

// An org's retry and failover status lists match the upstream's own status
// (503) as well as the gateway's (502), and "Retry on Timeout" retries a
// provider timeout.

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const statusInternalKey = "sk-agentcc-gw-request-internal-0123456789abcdef"

// flakyUpstream answers a chat call with what answer(n) says (n counts
// calls from 1): a status to fail with, a delay before answering, or 0 for
// a success.
type flakyUpstream struct {
	*httptest.Server
	mu    sync.Mutex
	calls int
}

func startFlakyUpstream(t *testing.T, answer func(n int) (status int, delay time.Duration)) *flakyUpstream {
	t.Helper()
	u := &flakyUpstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		w.Header().Set("Content-Type", "application/json")
		if r.Method != http.MethodPost {
			_, _ = io.WriteString(w, `{"object":"list","data":[]}`)
			return
		}
		u.mu.Lock()
		u.calls++
		n := u.calls
		u.mu.Unlock()
		status, delay := answer(n)
		if delay > 0 {
			select {
			case <-time.After(delay):
			case <-r.Context().Done():
				return
			}
		}
		if status != 0 {
			w.WriteHeader(status)
			_, _ = io.WriteString(w, `{"error":{"message":"upstream unavailable","type":"server_error","code":"unavailable"}}`)
			return
		}
		_, _ = io.WriteString(w, `{"id":"x","object":"chat.completion","created":1,"model":"org-model","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	t.Cleanup(u.Close)
	return u
}

func (u *flakyUpstream) count() int {
	u.mu.Lock()
	defer u.mu.Unlock()
	return u.calls
}

// newStatusRoutingGateway: config.yaml providers alpha and beta serve org-model
// from their upstreams (both from one, so whichever target round robin
// picks first sees the upstream's first answer); clientTimeout
// is their client timeout, 0 for the default. An internal key of the org
// routes by the org's routing config.
func newStatusRoutingGateway(t *testing.T, alpha, beta *flakyUpstream, clientTimeout time.Duration, routing *tenant.RoutingConfig) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "internal", Key: statusInternalKey, Owner: reqHdrOrg, KeyType: "internal", Metadata: map[string]string{"org_id": reqHdrOrg}},
	}
	off := false
	cfg.Providers["alpha"] = config.ProviderConfig{BaseURL: alpha.URL + "/v1", APIKey: "node-alpha-key", APIFormat: "openai", Models: []string{"org-model"}, AutoDiscover: &off, DefaultTimeout: clientTimeout}
	if beta != nil {
		cfg.Providers["beta"] = config.ProviderConfig{BaseURL: beta.URL + "/v1", APIKey: "node-beta-key", APIFormat: "openai", Models: []string{"org-model"}, AutoDiscover: &off, DefaultTimeout: clientTimeout}
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	tenants.Set(reqHdrOrg, &tenant.OrgConfig{
		Providers: map[string]*tenant.ProviderConfig{
			"alpha": {APIKey: "org-alpha-key", APIFormat: "openai", Models: []string{"org-model"}, Enabled: true},
		},
		Routing: routing,
	})
	ks := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(ks, true)), ks, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	return srv
}

func statusOrgChat(srv *Server) *httptest.ResponseRecorder {
	return doRequest(srv, "POST", "/v1/chat/completions", `{"model":"org-model","messages":[{"role":"user","content":"hello"}]}`, statusInternalKey, nil)
}

func TestUpstreamStatusMatchRetryList(t *testing.T) {
	for _, tc := range []struct {
		name  string
		codes []int
	}{
		{"the upstream's own status", []int{503}},
		{"the gateway's status, as before", []int{502}},
		{"the default list", nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			up := startFlakyUpstream(t, func(n int) (int, time.Duration) {
				if n == 1 {
					return http.StatusServiceUnavailable, 0
				}
				return 0, 0
			})
			// The org's retry runs inside its failover (org_routing.go
			// orgFailoverSettings), with one target.
			srv := newStatusRoutingGateway(t, up, up, 0, &tenant.RoutingConfig{
				Strategy: "round_robin",
				Failover: &tenant.FailoverConfig{Enabled: true, MaxAttempts: 1, OnStatusCodes: tc.codes},
				Retry:    &tenant.RetryConfig{Enabled: true, MaxRetries: 2, InitialDelay: "1ms", MaxDelay: "2ms", Multiplier: 1, OnStatusCodes: tc.codes},
			})
			w := statusOrgChat(srv)
			if w.Code != http.StatusOK || up.count() != 2 {
				t.Errorf("retry list %v, upstream 503 then 200: -> %d after %d call(s), want 200 after 2; body %s", tc.codes, w.Code, up.count(), w.Body.String())
			}
		})
	}
	// A list the upstream's status is not in still does not retry.
	up := startFlakyUpstream(t, func(int) (int, time.Duration) { return http.StatusServiceUnavailable, 0 })
	srv := newStatusRoutingGateway(t, up, up, 0, &tenant.RoutingConfig{
		Strategy: "round_robin",
		Failover: &tenant.FailoverConfig{Enabled: true, MaxAttempts: 1, OnStatusCodes: []int{429}},
		Retry:    &tenant.RetryConfig{Enabled: true, MaxRetries: 2, InitialDelay: "1ms", MaxDelay: "2ms", Multiplier: 1, OnStatusCodes: []int{429}},
	})
	if w := statusOrgChat(srv); w.Code != http.StatusBadGateway || up.count() != 1 {
		t.Errorf("retry list [429], upstream 503: -> %d after %d call(s), want 502 after 1", w.Code, up.count())
	}
}

func TestUpstreamStatusMatchFailoverList(t *testing.T) {
	alpha := startFlakyUpstream(t, func(int) (int, time.Duration) { return http.StatusServiceUnavailable, 0 })
	beta := startFlakyUpstream(t, func(int) (int, time.Duration) { return 0, 0 })
	srv := newStatusRoutingGateway(t, alpha, beta, 0, &tenant.RoutingConfig{
		Strategy: "round_robin",
		Failover: &tenant.FailoverConfig{Enabled: true, MaxAttempts: 3, OnStatusCodes: []int{503}},
	})
	// Round robin may start at either target; send until alpha has failed
	// once, and check the request it failed on went on to beta.
	for i := 0; i < 4 && alpha.count() == 0; i++ {
		before := beta.count()
		w := statusOrgChat(srv)
		if w.Code != http.StatusOK {
			t.Fatalf("failover list [503], alpha answers 503: -> %d (alpha %d, beta %d calls), want 200 from beta; body %s", w.Code, alpha.count(), beta.count(), w.Body.String())
		}
		if alpha.count() == 1 && beta.count() != before+1 {
			t.Errorf("the request alpha failed was not failed over to beta")
		}
	}
	if alpha.count() == 0 {
		t.Fatal("round robin never picked alpha")
	}
}

func TestOrgRetryOnTimeout(t *testing.T) {
	for _, tc := range []struct {
		name      string
		onTimeout bool
		wantOK    bool
		wantCalls int
	}{
		{"retry on timeout", true, true, 2},
		{"retry on timeout off", false, false, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// The first call outlasts the provider's 150 ms client timeout.
			up := startFlakyUpstream(t, func(n int) (int, time.Duration) {
				if n == 1 {
					return 0, 600 * time.Millisecond
				}
				return 0, 0
			})
			srv := newStatusRoutingGateway(t, up, up, 150*time.Millisecond, &tenant.RoutingConfig{
				Strategy: "round_robin",
				Failover: &tenant.FailoverConfig{Enabled: true, MaxAttempts: 1, OnStatusCodes: []int{500}},
				Retry: &tenant.RetryConfig{Enabled: true, MaxRetries: 1, InitialDelay: "1ms", MaxDelay: "2ms", Multiplier: 1,
					OnStatusCodes: []int{500}, OnTimeout: tc.onTimeout},
			})
			w := statusOrgChat(srv)
			if (w.Code == http.StatusOK) != tc.wantOK || up.count() != tc.wantCalls {
				t.Errorf("retry list [500], on_timeout %v, the first call times out: -> %d after %d call(s), want ok=%v after %d; body %s",
					tc.onTimeout, w.Code, up.count(), tc.wantOK, tc.wantCalls, strings.TrimSpace(w.Body.String()))
			}
		})
	}
}
