package server

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// assistants.enabled and prometheus.path do what the config says.

const (
	switchesAdmin = "gw-ops-switches-admin-token-0123456789abcdef0123456789ab"
	switchesKey   = "sk-gw-ops-switches-internal"
)

// switchesServer is a gateway whose openai provider is a counting upstream.
func switchesServer(t *testing.T, reg *metrics.Registry, mutate func(*config.Config)) (*Server, *atomic.Int32) {
	t.Helper()
	var calls atomic.Int32
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		if r.Header.Get("Authorization") != "" { // not the registry's connectivity check
			calls.Add(1)
		}
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"id":"asst_1","object":"assistant","data":[]}`)
	}))
	t.Cleanup(up.Close)
	cfg := config.DefaultConfig()
	cfg.Admin.Token = switchesAdmin
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "internal", Key: switchesKey, Owner: "t", KeyType: "internal"}}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "fake-operator-key", APIFormat: "openai", Models: []string{"gpt-4o"}}
	if mutate != nil {
		mutate(cfg)
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, reg, testModelDBPtr(), tenant.NewStore(), nil)
	srv.ready.Store(true)
	return srv, &calls
}

func switchesDo(srv *Server, method, path, body, key string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// assistantSwitchRoutes is one request per registered assistants, threads and
// vector stores route.
var assistantSwitchRoutes = []struct{ method, path, body string }{
	{"POST", "/v1/assistants", `{"model":"gpt-4o"}`},
	{"GET", "/v1/assistants", ""},
	{"GET", "/v1/assistants/asst_1", ""},
	{"POST", "/v1/assistants/asst_1", `{"name":"x"}`},
	{"DELETE", "/v1/assistants/asst_1", ""},
	{"POST", "/v1/threads", `{}`},
	{"POST", "/v1/threads/runs", `{"assistant_id":"asst_1"}`},
	{"GET", "/v1/threads/thread_1", ""},
	{"POST", "/v1/threads/thread_1", `{}`},
	{"DELETE", "/v1/threads/thread_1", ""},
	{"POST", "/v1/threads/thread_1/messages", `{"role":"user","content":"hi"}`},
	{"GET", "/v1/threads/thread_1/messages", ""},
	{"GET", "/v1/threads/thread_1/messages/msg_1", ""},
	{"POST", "/v1/threads/thread_1/messages/msg_1", `{}`},
	{"DELETE", "/v1/threads/thread_1/messages/msg_1", ""},
	{"POST", "/v1/threads/thread_1/runs", `{"assistant_id":"asst_1"}`},
	{"GET", "/v1/threads/thread_1/runs", ""},
	{"POST", "/v1/threads/thread_1/runs/run_1/cancel", ""},
	{"POST", "/v1/threads/thread_1/runs/run_1/submit_tool_outputs", `{"tool_outputs":[]}`},
	{"GET", "/v1/threads/thread_1/runs/run_1", ""},
	{"POST", "/v1/threads/thread_1/runs/run_1", `{}`},
	{"GET", "/v1/threads/thread_1/runs/run_1/steps", ""},
	{"GET", "/v1/threads/thread_1/runs/run_1/steps/step_1", ""},
	{"POST", "/v1/vector_stores", `{"name":"vs"}`},
	{"GET", "/v1/vector_stores", ""},
	{"POST", "/v1/vector_stores/vs_1/search", `{"query":"q"}`},
	{"POST", "/v1/vector_stores/vs_1/files", `{"file_id":"file_1"}`},
	{"GET", "/v1/vector_stores/vs_1/files", ""},
	{"DELETE", "/v1/vector_stores/vs_1/files/file_1", ""},
	{"POST", "/v1/vector_stores/vs_1/file_batches", `{"file_ids":["file_1"]}`},
	{"GET", "/v1/vector_stores/vs_1", ""},
	{"POST", "/v1/vector_stores/vs_1", `{}`},
	{"DELETE", "/v1/vector_stores/vs_1", ""},
}

// TestAssistantsSwitch: with assistants.enabled false every assistants,
// threads and vector stores route answers 404 naming the switch and nothing
// reaches the provider; with it true (the default) they proxy to it.
func TestAssistantsSwitch(t *testing.T) {
	srv, calls := switchesServer(t, nil, func(c *config.Config) { c.Assistants.Enabled = false })
	for _, rt := range assistantSwitchRoutes {
		w := switchesDo(srv, rt.method, rt.path, rt.body, switchesKey)
		if w.Code != http.StatusNotFound || !strings.Contains(w.Body.String(), "assistants.enabled") {
			t.Errorf("assistants.enabled false: %s %s -> %d %s; want 404 naming assistants.enabled", rt.method, rt.path, w.Code, w.Body.String())
		}
	}
	if n := calls.Load(); n != 0 {
		t.Errorf("assistants.enabled false: %d upstream call(s), want 0", n)
	}

	srv, calls = switchesServer(t, nil, nil)
	w := switchesDo(srv, "POST", "/v1/assistants", `{"model":"gpt-4o"}`, switchesKey)
	if w.Code != http.StatusOK || calls.Load() != 1 {
		t.Errorf("assistants.enabled true (default): POST /v1/assistants -> %d %s, %d upstream call(s); want 200 and 1", w.Code, w.Body.String(), calls.Load())
	}
}

// TestPrometheusPathSwitch: prometheus.path moves the metrics endpoint (behind
// the admin token); unset keeps /-/metrics.
func TestPrometheusPathSwitch(t *testing.T) {
	srv, _ := switchesServer(t, metrics.NewRegistry(), func(c *config.Config) {
		c.Prometheus.Enabled = true
		c.Prometheus.Path = "/metrics-custom"
	})
	if w := switchesDo(srv, "GET", "/metrics-custom", "", switchesAdmin); w.Code != http.StatusOK {
		t.Errorf("prometheus.path /metrics-custom: GET /metrics-custom -> %d %s; want 200", w.Code, w.Body.String())
	}
	if w := switchesDo(srv, "GET", "/metrics-custom", "", ""); w.Code != http.StatusUnauthorized {
		t.Errorf("prometheus.path /metrics-custom without the admin token -> %d; want 401", w.Code)
	}
	if w := switchesDo(srv, "GET", "/-/metrics", "", switchesAdmin); w.Code != http.StatusNotFound {
		t.Errorf("prometheus.path /metrics-custom: GET /-/metrics -> %d; want 404", w.Code)
	}

	srv, _ = switchesServer(t, metrics.NewRegistry(), func(c *config.Config) {
		c.Prometheus.Enabled = true
		c.Prometheus.Path = "metrics-no-slash"
	})
	if w := switchesDo(srv, "GET", "/metrics-no-slash", "", switchesAdmin); w.Code != http.StatusOK {
		t.Errorf("prometheus.path without a leading slash: GET /metrics-no-slash -> %d; want 200", w.Code)
	}

	srv, _ = switchesServer(t, metrics.NewRegistry(), func(c *config.Config) { c.Prometheus.Enabled = true })
	if w := switchesDo(srv, "GET", "/-/metrics", "", switchesAdmin); w.Code != http.StatusOK {
		t.Errorf("prometheus.path unset: GET /-/metrics -> %d; want 200", w.Code)
	}
}
