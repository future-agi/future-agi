package upstream

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// rawTool and rawResult carry fields the gateway's types do not name, and a
// number literal, so a pass-through that re-encodes them would change them.
const (
	rawTool   = `{"name":"echo","inputSchema":{"type":"object"},"x-vendor":{"rank":1.50}}`
	rawResult = `{"content":[{"type":"text","text":"ok"}],"structuredContent":{"price":1.50},"resultType":"complete"}`
)

// legacyUpstream answers a legacy session and records each request's
// Authorization header by method.
type legacyUpstream struct {
	*httptest.Server
	mu   sync.Mutex
	auth map[string][]string
}

func newLegacyUpstream(t *testing.T) *legacyUpstream {
	u := &legacyUpstream{auth: map[string][]string{}}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var m mcp.Message
		_ = json.Unmarshal(body, &m)
		u.mu.Lock()
		u.auth[m.Method] = append(u.auth[m.Method], r.Header.Get("Authorization"))
		u.mu.Unlock()
		if r.Method != http.MethodPost || len(m.ID) == 0 {
			w.WriteHeader(http.StatusAccepted)
			return
		}
		var result string
		switch m.Method {
		case mcp.MethodInitialize:
			w.Header().Set("Mcp-Session-Id", "s1")
			result = `{"protocolVersion":"2025-11-25","capabilities":{"tools":{}},"serverInfo":{"name":"u","version":"1"}}`
		case mcp.MethodToolsList:
			result = `{"tools":[` + rawTool + `]}`
		case mcp.MethodToolsCall:
			result = rawResult
		case mcp.MethodResourcesList:
			result = `{"resources":[]}`
		case mcp.MethodPromptsList:
			result = `{"prompts":[]}`
		default:
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"jsonrpc":"2.0","id":` + string(m.ID) + `,"error":{"code":-32601,"message":"unknown"}}`))
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"jsonrpc":"2.0","id":` + string(m.ID) + `,"result":` + result + `}`))
	}))
	t.Cleanup(u.Close)
	return u
}

func (u *legacyUpstream) authOf(method string) []string {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([]string(nil), u.auth[method]...)
}

type countingDialer struct {
	n atomic.Int64
	d net.Dialer
}

func (c *countingDialer) DialContext(ctx context.Context, network, addr string) (net.Conn, error) {
	c.n.Add(1)
	return c.d.DialContext(ctx, network, addr)
}

// TestFactoryImplementsFrozenSeam: Factory.Build is an mcp.ClientFactory
// that a server takes as Deps.ClientFactory; its clients implement M1's
// frozen UpstreamClient, pass lists and results through byte for byte,
// honour CallOptions (a per-call credential here), dial through the
// dialer the source names when they are built, and close.
func TestFactoryImplementsFrozenSeam(t *testing.T) {
	var factory mcp.ClientFactory = NewFactory(Options{MaxResultBytes: 1 << 20}).Build
	srv := mcp.NewServer(mcp.ServerConfig{})
	defer srv.Close()
	srv.SetDeps(mcp.Deps{ClientFactory: factory})
	if reflect.ValueOf(srv.Deps().ClientFactory).Pointer() != reflect.ValueOf(factory).Pointer() {
		t.Fatal("the server does not hold the factory as Deps.ClientFactory")
	}

	u := newLegacyUpstream(t)
	f := NewFactory(Options{})
	dialer := &countingDialer{}
	f.SetDialerSource(func() mcp.Dialer { return dialer })
	client, err := f.Build(mcp.ClientConfig{ServerID: "u", URL: u.URL, Auth: mcp.AuthConfig{Type: "bearer", Token: "configured"}})
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	if dialer.n.Load() == 0 {
		t.Error("the client did not dial through the source's dialer")
	}
	tools, err := client.ListTools(context.Background())
	if err != nil || len(tools) != 1 || string(tools[0]) != rawTool {
		t.Errorf("ListTools = %s, %v; want the upstream's bytes", tools, err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	res, err := client.CallTool(ctx, "echo", json.RawMessage(`{"a":1.50}`), mcp.CallOptions{
		Auth: &mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer per-user"}}},
	})
	if err != nil || string(res) != rawResult {
		t.Errorf("CallTool = %s, %v; want the upstream's bytes", res, err)
	}
	if got := u.authOf(mcp.MethodToolsCall); len(got) != 1 || got[0] != "Bearer per-user" {
		t.Errorf("tools/call sent Authorization %v, want the per-call credential", got)
	}
	if got := u.authOf(mcp.MethodToolsList); len(got) == 0 || !strings.HasSuffix(got[0], "configured") {
		t.Errorf("tools/list sent Authorization %v, want the configured credential", got)
	}
	if !client.Healthy() {
		t.Error("a connected client reports unhealthy")
	}
	if err := client.Close(); err != nil || client.Healthy() {
		t.Errorf("Close = %v, healthy after close %v", err, client.Healthy())
	}
	if _, err := f.Build(mcp.ClientConfig{ServerID: "down", URL: "http://127.0.0.1:9/mcp"}); err == nil {
		t.Error("Build of an unreachable upstream returned a client")
	}
}
