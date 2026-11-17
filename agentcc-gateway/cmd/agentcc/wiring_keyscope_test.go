package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/server"
)

// TestKeyScopeWiring runs main's wiring with request auth on and drives the
// chat path through the real server: a budgeted EE agent key whose budget is
// spent gets 402 agent_key_budget_exhausted before any upstream call, and the
// key gate and the plugin report refusals to the control plane's audit route
// (CN22c).
func TestKeyScopeWiring(t *testing.T) {
	var upstreamCalls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upstreamCalls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"id":"c1","object":"chat.completion","created":1,"model":"gpt-4o",` +
			`"choices":[{"index":0,"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}],` +
			`"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`))
	}))
	defer upstream.Close()

	refusals := make(chan string, 8)
	controlPlane := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if r.URL.Path == "/agentcc/mcp/service/agent-key-refusals/" && r.Header.Get("Authorization") == "Bearer wiring-admin-token-0123456789" {
			refusals <- string(body)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer controlPlane.Close()

	const spent, open = "sk-agentcc-wiring-spent-budget-key", "sk-agentcc-wiring-open-budget-key"
	cfg := config.DefaultConfig()
	cfg.Admin.Token = "wiring-admin-token-0123456789"
	cfg.Server.Host = "127.0.0.1"
	cfg.Server.Port = freePort(t)
	cfg.ControlPlane.URL = controlPlane.URL
	cfg.ControlPlane.AdminToken = cfg.Admin.Token
	cfg.ControlPlane.SyncOnStartup = false
	cfg.Auth.Enabled = true
	scoped := func(budget string) map[string]string {
		return map[string]string{"org_id": "org-ks", "scopes": "mcp,models", "budget_limit_usd": budget, "budget_period": "monthly"}
	}
	// Internal keys resolve the global provider, so the chat path reaches the
	// pipeline without an org config.
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "spent", Key: spent, KeyType: "internal", Metadata: scoped("0")},
		{Name: "open", Key: open, KeyType: "internal", Metadata: scoped("100")},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)

	d := &wiringDeps{cfg: cfg, registry: registry, keyStore: keyStore, catalogStore: catalog.NewStore(),
		plugins: []pipeline.Plugin{authplugin.New(keyStore, true)}}
	if err := runWiring(d, wiringOrder); err != nil {
		t.Fatalf("runWiring: %v", err)
	}
	engine := pipeline.NewEngine(d.plugins...)
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(modeldb.New(modeldb.BundledModels, nil))
	srv := server.New(cfg, "", registry, engine, keyStore, nil, nil, nil, &mdb, nil, nil)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	d.engine, d.server, d.handlers, d.ctx = engine, srv, srv.Handlers(), ctx
	if err := d.startServerWiring(); err != nil {
		t.Fatalf("startServerWiring: %v", err)
	}
	served := make(chan error, 1)
	go func() { served <- srv.Start() }()
	stopped := false
	shutdown := func() {
		if stopped {
			return
		}
		stopped = true
		shutdownCtx, stop := context.WithTimeout(context.Background(), 5*time.Second)
		defer stop()
		_ = srv.Shutdown(shutdownCtx)
		<-served
	}
	defer shutdown()

	base := "http://" + cfg.Addr()
	chat := func(key string) (int, string) {
		t.Helper()
		body := []byte(`{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`)
		deadline := time.Now().Add(5 * time.Second)
		for {
			req, _ := http.NewRequest(http.MethodPost, base+"/v1/chat/completions", bytes.NewReader(body))
			req.Header.Set("Authorization", "Bearer "+key)
			req.Header.Set("Content-Type", "application/json")
			resp, err := http.DefaultClient.Do(req)
			if err == nil {
				defer resp.Body.Close()
				out, _ := io.ReadAll(resp.Body)
				return resp.StatusCode, string(out)
			}
			if time.Now().After(deadline) {
				t.Fatalf("POST /v1/chat/completions: %v", err)
			}
			time.Sleep(20 * time.Millisecond)
		}
	}

	if code, body := chat(open); code != http.StatusOK {
		t.Fatalf("a key with budget left: %d %s", code, body)
	}
	calls := upstreamCalls.Load()
	code, body := chat(spent)
	var parsed struct {
		Error struct {
			Code     string         `json:"code"`
			Metadata map[string]any `json:"metadata"`
		} `json:"error"`
	}
	_ = json.Unmarshal([]byte(body), &parsed)
	if code != http.StatusPaymentRequired || parsed.Error.Code != "agent_key_budget_exhausted" {
		t.Fatalf("a key whose budget is spent: %d %s, want 402 agent_key_budget_exhausted", code, body)
	}
	if parsed.Error.Metadata["limit_source"] != "agentcc_key_limit" || !strings.HasPrefix(parsed.Error.Metadata["scope"].(string), "key:") {
		t.Fatalf("402 metadata %v", parsed.Error.Metadata)
	}
	if upstreamCalls.Load() != calls {
		t.Fatal("the refused call reached the upstream")
	}

	// A route outside the scope: the key gate's 403.
	if code, _ := getWithRetry(t, base+"/v1/models", "Bearer "+open); code != http.StatusForbidden {
		t.Fatalf("GET /v1/models with a scoped key: %d, want 403", code)
	}

	// Shutdown flushes both refusals to the control plane.
	shutdown()
	var got []string
	for len(got) < 1 {
		select {
		case b := <-refusals:
			got = append(got, b)
		case <-time.After(5 * time.Second):
			t.Fatal("no refusal reached the control plane")
		}
	}
	all := strings.Join(got, "\n")
	for _, want := range []string{`"code":"agent_key_budget_exhausted"`, `"code":"key_scope_denied"`, `"path":"/v1/models"`} {
		if !strings.Contains(all, want) {
			t.Fatalf("refusals sent %s, missing %s", all, want)
		}
	}
}
