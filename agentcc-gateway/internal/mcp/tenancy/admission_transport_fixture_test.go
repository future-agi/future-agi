package tenancy

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/catalog/connectors"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/upstream"
	"github.com/futureagi/agentcc-gateway/internal/mcp/vendors"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Versions and declarations from gwc d2fd7fd6; never the old gmail=false fixture.
var r6Records = []struct{ app, version string }{
	{"google-docs", "1.0.2"}, {"google-drive", "1.0.2"}, {"google-meet", "1.0.2"},
	{"google-sheets", "1.0.2"}, {"google-slides", "1.0.2"}, {"gmail", "1.0.2"},
	{"google-calendar", "1.0.2"}, {"microsoft-excel", "2.0.0"}, {"microsoft-onedrive", "2.0.0"},
	{"microsoft-sharepoint", "2.0.0"}, {"microsoft-outlook", "1.0.1"}, {"microsoft-teams", "1.0.1"},
}

type r6Policy struct {
	mu       sync.Mutex
	gate     *vendors.ClientAdmission
	provider *manifest.Provider
	denied   chan struct{}
	once     sync.Once
}

func (p *r6Policy) AdmitClient(req mcp.ClientAdmissionRequest) mcp.ClientAdmission {
	p.mu.Lock()
	defer p.mu.Unlock()
	out := p.gate.AdmitClient(req)
	if out.Denial != nil {
		p.once.Do(func() { close(p.denied) })
	}
	return out
}
func (p *r6Policy) disable() {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.provider.XGateway.ConnectionEnablement.Enabled = false
}

func r6Catalog(t *testing.T, app, version string) (*manifest.Catalog, *manifest.Provider) {
	t.Helper()
	var bundle struct {
		Providers []map[string]any `json:"providers"`
	}
	if err := json.Unmarshal(connectors.BundleJSON, &bundle); err != nil {
		t.Fatal(err)
	}
	for _, rec := range bundle.Providers {
		if rec["app"] != app || rec["id"] != "futureagi-rest" {
			continue
		}
		rec["version"] = version
		rec["tools"] = map[string]any{"source": "operations", "items": []any{map[string]any{"name": "echo", "inputSchema": map[string]any{"type": "object"}}}}
		src := []string{"org"}
		if app == "google-calendar" {
			src = append(src, "platform")
		}
		rec["auth"].(map[string]any)["oauth2"].(map[string]any)["clientSources"] = src
		rec["x-gateway"] = map[string]any{"connectionEnablement": map[string]any{"schemaVersion": 1, "enabled": true, "operations": []string{"connect", "list_tools", "invoke"}}}
		raw, _ := json.Marshal(rec)
		var p manifest.Provider
		if err := json.Unmarshal(raw, &p); err != nil {
			t.Fatal(err)
		}
		return &manifest.Catalog{Apps: []*manifest.App{{ID: app, Providers: []*manifest.Provider{&p}}}}, &p
	}
	t.Fatalf("missing fixture %s", app)
	return nil, nil
}

type r6Wire struct {
	stage, transport string
	mutate           func()
	flipped          chan struct{}
	flipOnce         sync.Once
	after            atomic.Int64
	authenticated    atomic.Int64
	pings            atomic.Int64
	lists            atomic.Int64
	calls            atomic.Int64
	streamOpened     chan struct{}
	streamOnce       sync.Once
	releaseStream    chan struct{}
	replies          chan []byte
	methods          sync.Map
}

func (s *r6Wire) flip() { s.flipOnce.Do(func() { s.mutate(); close(s.flipped) }) }
func (s *r6Wire) serve(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("Authorization") == "Bearer synthetic-r6-loopback" {
		s.authenticated.Add(1)
		select {
		case <-s.flipped:
			s.after.Add(1)
		default:
		}
	} else {
		http.Error(w, "missing synthetic credential", http.StatusUnauthorized)
		return
	}
	if r.Method == http.MethodGet {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(200)
		if s.transport == "sse" {
			fmt.Fprintf(w, "event: endpoint\ndata: %s?post=1\n\n", r.URL.Path)
		}
		w.(http.Flusher).Flush()
		s.streamOnce.Do(func() { close(s.streamOpened) })
		release := s.releaseStream
		for {
			select {
			case <-r.Context().Done():
				return
			case raw := <-s.replies:
				fmt.Fprintf(w, "event: message\ndata: %s\n\n", raw)
				w.(http.Flusher).Flush()
			case <-release:
				release = nil
				s.flip()
				fmt.Fprint(w, "event: message\ndata: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/tools/list_changed\"}\n\n")
				w.(http.Flusher).Flush()
			}
		}
	}
	var msg struct {
		ID     json.RawMessage `json:"id"`
		Method string          `json:"method"`
	}
	if err := json.NewDecoder(r.Body).Decode(&msg); err != nil {
		http.Error(w, "bad rpc", 400)
		return
	}
	s.methods.Store(msg.Method, true)
	if msg.Method == "ping" && s.stage == "before-reconnect" {
		if s.pings.Add(1) == 3 {
			s.flip()
		}
		conn, _, err := w.(http.Hijacker).Hijack()
		if err == nil {
			_ = conn.Close()
		}
		return
	}
	if s.stage == "before-discovery" && msg.Method == "notifications/initialized" {
		s.flip()
	}
	var result any = map[string]any{}
	switch msg.Method {
	case "initialize":
		result = map[string]any{"protocolVersion": "2025-11-25", "serverInfo": map[string]any{"name": "synthetic-r6", "version": "1"}, "capabilities": map[string]any{"tools": map[string]any{}, "resources": map[string]any{}, "prompts": map[string]any{}}}
		if s.stage == "mid-stream" {
			w.Header().Set("Mcp-Session-Id", "synthetic-session")
		}
	case "tools/list":
		s.lists.Add(1)
		if s.stage == "after-tools" {
			s.flip()
		}
		result = map[string]any{"tools": []any{map[string]any{"name": "echo", "inputSchema": map[string]any{"type": "object"}}}}
	case "resources/list":
		result = map[string]any{"resources": []any{}}
	case "prompts/list":
		result = map[string]any{"prompts": []any{}}
	case "tools/call":
		s.calls.Add(1)
		result = map[string]any{"content": []any{map[string]any{"type": "text", "text": "synthetic success"}}}
	}
	if len(msg.ID) == 0 {
		w.WriteHeader(202)
		return
	}
	raw, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": msg.ID, "result": result})
	if s.transport == "sse" {
		select {
		case s.replies <- raw:
		case <-r.Context().Done():
		}
		w.WriteHeader(202)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write(raw)
}

type r6Fixture struct {
	rig    *rig
	wire   *r6Wire
	policy *r6Policy
	pool   *Pool
	entry  *entry
	auth   mcp.AuthOverride
}

func newR6Fixture(t *testing.T, app, version, transport, stage, mutation string) *r6Fixture {
	t.Helper()
	w := &r6Wire{stage: stage, transport: transport, flipped: make(chan struct{}), streamOpened: make(chan struct{}), releaseStream: make(chan struct{}), replies: make(chan []byte, 64)}
	srv := httptest.NewServer(http.HandlerFunc(w.serve))
	t.Cleanup(srv.Close)
	// The service below is a catalog_ref server: the release builds it only
	// with mcp.catalog_enabled on (spec.go orgSpecsForRelease, 28c5d0ecd4),
	// its default. Cleanup closes clients before the loopback server.
	r := newRig(t, Options{MCP: config.MCPConfig{CatalogEnabled: true}})
	cat, p := r6Catalog(t, app, version)
	gate, err := vendors.NewClientAdmission(cat, srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	policy := &r6Policy{gate: gate, provider: p, denied: make(chan struct{})}
	var cfg tenant.OrgConfig
	raw := fmt.Sprintf(`{"meta":{"version":1},"mcp":{"enabled":true,"servers":{"service":{"url":%q,"catalog_ref":%q,"era":"2025-11-25","transport":%q,"credential":{"mode":"per_user"}}}}}`, srv.URL+"/"+app+"/mcp", app+"/futureagi-rest@"+version, transport)
	if err := json.Unmarshal([]byte(raw), &cfg); err != nil {
		t.Fatal(err)
	}
	var changed tenant.OrgConfig
	if err := json.Unmarshal([]byte(raw), &changed); err != nil {
		t.Fatal(err)
	}
	// Decode a separately owned replacement; no mutation of store snapshots.
	if mutation == "ref" || mutation == "url" {
		var rec map[string]any
		_ = json.Unmarshal([]byte(raw), &rec)
		servers := rec["mcp"].(map[string]any)["servers"].(map[string]any)
		service := servers["service"].(map[string]any)
		if mutation == "ref" {
			service["catalog_ref"] = "gmail/futureagi-rest@1.0.2"
		} else {
			service["url"] = srv.URL + "/wrong/mcp"
		}
		servers["broken"] = map[string]any{"url": "not-a-url"} // keep r5 last-known-good scenario
		b, _ := json.Marshal(rec)
		if err := json.Unmarshal(b, &changed); err != nil {
			t.Fatal(err)
		}
	}
	w.mutate = func() {
		if mutation == "catalog" {
			policy.disable()
		} else {
			r.store.Set("org-a", &changed)
		}
	}
	real := upstream.NewFactory(upstream.Options{})
	d := *r.deps.Load()
	d.ClientAdmission = policy
	d.ClientFactory = func(c mcp.ClientConfig) (mcp.UpstreamClient, error) {
		c.PingInterval = time.Hour
		c.ToolsCacheTTL = time.Hour
		if stage == "before-health" || stage == "before-reconnect" {
			c.PingInterval = 50 * time.Millisecond
		}
		if stage == "before-refresh" {
			c.ToolsCacheTTL = 50 * time.Millisecond
		}
		// RED exposes cached tokens to the factory; GREEN must provide only an opaque transport.
		if stage != "positive" && (c.Auth.Token != "" || c.Auth.Key != "" || c.Auth.Source != nil || c.Auth.Template != nil) {
			t.Errorf("factory received reusable credential instead of sealed transport")
		}
		return real.Build(c)
	}
	r.deps.Store(&d)
	r.store.Set("org-a", &cfg)
	_, resolved := r.resolve("org-a")
	pool := resolved.(*Pool)
	// A missing entry fails this test; dereferencing it later would panic
	// and stop every tenancy test after it.
	service := pool.current()["service"]
	if service == nil {
		t.Fatalf("r6 fixture: the pool has no service entry")
	}
	return &r6Fixture{rig: r, wire: w, policy: policy, pool: pool, entry: service, auth: mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer synthetic-r6-loopback"}}, Lifetime: context.Background(), ExpiresAt: time.Now().Add(time.Hour)}}
}
