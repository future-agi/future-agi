package server

// What the request log entry the control plane
// receives keeps of the session name and path, the property headers and the
// client's request id.

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	loggingplugin "github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// logSink is the control plane's log webhook: it keeps every entry.
type logSink struct {
	*httptest.Server
	mu      sync.Mutex
	entries []map[string]any
}

func startLogSink(t *testing.T) *logSink {
	t.Helper()
	s := &logSink{}
	s.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var batch struct {
			Logs []map[string]any `json:"logs"`
		}
		_ = json.NewDecoder(r.Body).Decode(&batch)
		s.mu.Lock()
		s.entries = append(s.entries, batch.Logs...)
		s.mu.Unlock()
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(s.Close)
	return s
}

func TestSessionNamePathLogged(t *testing.T) {
	up := startHeadersUpstream(t)
	sink := startLogSink(t)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "byok", Key: reqHdrByokKey, Owner: "t", KeyType: "byok", Metadata: map[string]string{"org_id": reqHdrOrg}},
	}
	off := false
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "operator-key", APIFormat: "openai", Models: []string{"m-node"}, AutoDiscover: &off}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	tenants.Set(reqHdrOrg, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {APIKey: "org-key", Enabled: true, Models: []string{"m-chat"}},
	}})
	ks := auth.NewKeyStore(cfg.Auth)
	flusher := loggingplugin.NewLogFlusher(sink.URL, "", time.Hour, 100)
	logPlugin := loggingplugin.New(config.RequestLoggingConfig{Enabled: true}, tenants)
	logPlugin.SetFlusher(flusher)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(ks, true), logPlugin), ks, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)

	w := doRequest(srv, "POST", "/v1/chat/completions", headersChatBody, reqHdrByokKey, map[string]string{
		"x-agentcc-session-id":   "s1",
		"x-agentcc-session-name": "My session",
		"x-agentcc-session-path": "/search/rerank",
		"x-agentcc-property-env": "prod",
		"x-agentcc-request-id":   "client-req-123",
	})
	if w.Code != http.StatusOK {
		t.Fatalf("-> %d; body %s", w.Code, w.Body.String())
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	flusher.Close(ctx)
	sink.mu.Lock()
	defer sink.mu.Unlock()
	if len(sink.entries) != 1 {
		t.Fatalf("%d log entries, want 1", len(sink.entries))
	}
	e := sink.entries[0]
	meta, _ := e["metadata"].(map[string]any)
	want := map[string]string{
		"session_name":      "My session",
		"session_path":      "/search/rerank",
		"env":               "prod",
		"client_request_id": "client-req-123",
	}
	for k, v := range want {
		if meta[k] != v {
			t.Errorf("log entry metadata %s = %v, want %q (metadata %v)", k, meta[k], v, meta)
		}
	}
	if e["request_id"] != w.Header().Get("x-agentcc-request-id") || e["request_id"] == "client-req-123" {
		t.Errorf("log entry request_id %v, want the gateway's %q", e["request_id"], w.Header().Get("x-agentcc-request-id"))
	}
	if e["session_id"] != "s1" {
		t.Errorf("log entry session_id %v, want s1", e["session_id"])
	}
}
