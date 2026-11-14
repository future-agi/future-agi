package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/async"
	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/plugins/ipacl"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const asyncNetworkKey = "synthetic-network"

func asyncNetworkTestServer(t *testing.T, global config.IPACLConfig, org *tenant.IPACLConfig, allowed []string, anonymous bool) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = !anonymous
	cfg.Auth.Keys = []config.AuthKeyConfig{{Key: asyncNetworkKey, AllowedIPs: allowed, Metadata: map[string]string{"org_id": "network-org"}}}
	cfg.IPACL = global
	cfg.Admin.Token = "synthetic-network-admin"
	keys := auth.NewKeyStore(cfg.Auth)
	tenants := tenant.NewStore()
	if org != nil {
		tenants.Set("network-org", &tenant.OrgConfig{IPACL: org})
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	engine := pipeline.NewEngine(ipacl.New(cfg.IPACL, tenants), authplugin.New(keys, cfg.Auth.Enabled))
	s := New(cfg, "", registry, engine, keys, nil, nil, nil, testModelDBPtr(), tenants, nil)
	// Keep the handler's worker stopped so submitted jobs stay queued.
	s.asyncWorker.Stop()
	s.asyncWorker = nil
	t.Cleanup(func() { _ = s.Shutdown(context.Background()) })
	return s
}

// asyncNetworkJob submits a job as the configured key and returns its id. The
// cancel flag reports whether the job's cancel callback ran.
func asyncNetworkJob(t *testing.T, h *Handlers, key string) (string, *bool) {
	t.Helper()
	id := asyncCallerSubmit(t, h, key)
	cancelled := false
	h.asyncStore.Update(id, func(j *async.Job) {
		j.Status = async.StatusCompleted
		j.CancelFn = func() { cancelled = true }
	})
	return id, &cancelled
}

func TestAsyncResultHonoursNetworkRules(t *testing.T) {
	for _, tc := range []struct {
		name      string
		global    config.IPACLConfig
		org       *tenant.IPACLConfig
		allowed   []string
		anonymous bool
	}{
		{name: "key", allowed: []string{"192.0.2.10/32"}},
		{name: "invalid-key-rule", allowed: []string{"not-an-ip"}},
		{name: "global-allow", global: config.IPACLConfig{Enabled: true, Allow: []string{"192.0.2.10/32"}}},
		{name: "global-deny-wins", global: config.IPACLConfig{Enabled: true, Allow: []string{"192.0.2.0/24"}, Deny: []string{"192.0.2.11"}}},
		{name: "org-allow-global-disabled", org: &tenant.IPACLConfig{Enabled: true, Allow: []string{"192.0.2.10/32"}}},
		{name: "org-deny", org: &tenant.IPACLConfig{Enabled: true, Deny: []string{"192.0.2.11"}}},
		{name: "anonymous-global", global: config.IPACLConfig{Enabled: true, Allow: []string{"192.0.2.10/32"}}, anonymous: true},
	} {
		for _, method := range []string{http.MethodGet, http.MethodDelete} {
			t.Run(tc.name+"/"+method, func(t *testing.T) {
				s := asyncNetworkTestServer(t, tc.global, tc.org, tc.allowed, tc.anonymous)
				h := s.handlers
				id, cancelled := asyncNetworkJob(t, h, asyncNetworkKey)
				r := asyncCallerRequest(method, id, asyncNetworkKey)
				r.RemoteAddr = "192.0.2.11:1234"
				// Neither caller metadata nor a forwarded-for entry chooses the address.
				r.Header.Set("x-agentcc-metadata", `{"org_id":"forged","client_ip":"192.0.2.10"}`)
				r.Header.Set("X-Forwarded-For", "192.0.2.10")
				call := h.GetAsyncJob
				if method == http.MethodDelete {
					call = h.DeleteAsyncJob
				}
				w := httptest.NewRecorder()
				call(w, r)
				if w.Code != http.StatusForbidden {
					t.Errorf("source outside the network rules got %d; want 403", w.Code)
				}
				if *cancelled || h.asyncStore.Get(id) == nil {
					t.Error("refused request removed the result or ran its cancel callback")
				}
				if tc.name != "invalid-key-rule" {
					r.RemoteAddr = "192.0.2.10:1234"
					w = httptest.NewRecorder()
					call(w, r)
					if w.Code != http.StatusOK {
						t.Errorf("allowed source got %d: %s", w.Code, w.Body.String())
					}
				}
			})
		}
	}
}

func TestAsyncResultUnknownSourceAndOrgRuleChange(t *testing.T) {
	s := asyncNetworkTestServer(t, config.IPACLConfig{}, &tenant.IPACLConfig{Enabled: true, Allow: []string{"192.0.2.10"}}, nil, false)
	h := s.handlers
	id, _ := asyncNetworkJob(t, h, asyncNetworkKey)
	r := asyncCallerRequest(http.MethodGet, id, asyncNetworkKey)
	for _, source := range []string{"unknown", "", "[2001:db8::2]:1234"} {
		r.RemoteAddr = source
		w := httptest.NewRecorder()
		h.GetAsyncJob(w, r)
		if w.Code != http.StatusForbidden {
			t.Errorf("unknown or unmatched source %q got %d", source, w.Code)
		}
	}
	r.RemoteAddr = "192.0.2.10:1234"
	w := httptest.NewRecorder()
	h.GetAsyncJob(w, r)
	if w.Code != http.StatusOK {
		t.Fatalf("initial allowed: %d", w.Code)
	}
	s.TenantStore.Set("network-org", &tenant.OrgConfig{IPACL: &tenant.IPACLConfig{Enabled: true, Deny: []string{"192.0.2.10"}}})
	w = httptest.NewRecorder()
	h.GetAsyncJob(w, r)
	if w.Code != http.StatusForbidden {
		t.Errorf("updated org rule got %d; want 403", w.Code)
	}
}

func TestBatchRoutesHonourGlobalNetworkRules(t *testing.T) {
	for _, route := range []struct{ method, path string }{{"GET", "/-/batches/network-batch"}, {"POST", "/-/batches/network-batch/cancel"}, {"POST", "/-/batches"}} {
		t.Run(route.method+route.path, func(t *testing.T) {
			s := asyncNetworkTestServer(t, config.IPACLConfig{Enabled: true, Allow: []string{"192.0.2.10"}}, nil, nil, false)
			call := func(r *http.Request) *httptest.ResponseRecorder {
				w := httptest.NewRecorder()
				switch {
				case route.path == "/-/batches":
					s.submitBatchHandler(w, r)
				case route.method == "GET":
					s.getBatchHandler(w, r)
				default:
					s.cancelBatchHandler(w, r)
				}
				return w
			}
			for _, credential := range []string{asyncNetworkKey, "synthetic-network-admin"} {
				r := httptest.NewRequest(route.method, route.path+"?batch_id=network-batch", strings.NewReader(`{}`))
				r.RemoteAddr = "192.0.2.11:1234"
				r.Header.Set("Authorization", "Bearer "+credential)
				want := http.StatusUnauthorized
				if credential == "synthetic-network-admin" {
					want = http.StatusForbidden
				}
				if w := call(r); w.Code != want {
					t.Errorf("batch access with %s got %d want %d", credential, w.Code, want)
				}
			}
			// A valid administrator on an allowed network reaches the normal response.
			r := httptest.NewRequest(route.method, route.path+"?batch_id=network-batch", strings.NewReader(`{}`))
			r.RemoteAddr = "192.0.2.10:1234"
			r.Header.Set("Authorization", "Bearer synthetic-network-admin")
			want := http.StatusNotFound
			if route.path == "/-/batches" {
				want = http.StatusBadRequest
			}
			if w := call(r); w.Code != want {
				t.Errorf("allowed admin got %d want %d: %s", w.Code, want, w.Body.String())
			}
		})
	}
}

func TestAsyncResultRoutedNetworkRulesBothKeyHeaders(t *testing.T) {
	for _, header := range []string{"Authorization", "x-api-key"} {
		t.Run(header, func(t *testing.T) {
			s := asyncNetworkTestServer(t, config.IPACLConfig{}, nil, []string{"192.0.2.10"}, false)
			for _, method := range []string{"GET", "DELETE"} {
				id, _ := asyncNetworkJob(t, s.handlers, asyncNetworkKey)
				r := httptest.NewRequest(method, "/v1/async/"+id, nil)
				value := asyncNetworkKey
				if header == "Authorization" {
					value = "Bearer " + value
				}
				r.Header.Set(header, value)
				r.RemoteAddr = "192.0.2.11:1234"
				w := httptest.NewRecorder()
				s.httpServer.Handler.ServeHTTP(w, r)
				if w.Code != http.StatusForbidden {
					t.Errorf("routed %s got %d", method, w.Code)
				}
				if strings.Contains(w.Body.String(), id) {
					t.Error("403 response mentions the job id")
				}
			}
		})
	}
}
