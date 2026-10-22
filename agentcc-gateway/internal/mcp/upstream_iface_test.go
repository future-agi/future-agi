package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// mockUpstream is an MCP server over HTTP that answers every method with
// fixed results carrying fields the decoded types do not know (title, _meta,
// structuredContent), so the tests can tell raw bytes from re-encoded ones.
type mockUpstream struct {
	*httptest.Server
	mu    sync.Mutex
	calls []json.RawMessage // tools/call params received
}

const (
	mockTool     = `{"name":"echo","title":"Echo","inputSchema":{"type":"object","required":["input"]},"_meta":{"x":1}}`
	mockResource = `{"uri":"file:///a","name":"a","size":3}`
	mockPrompt   = `{"name":"greet","title":"Greet"}`
)

func startMockUpstream(t *testing.T) *mockUpstream {
	t.Helper()
	m := &mockUpstream{}
	m.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var msg Message
		if err := json.Unmarshal(body, &msg); err != nil {
			http.Error(w, "bad json", http.StatusBadRequest)
			return
		}
		if msg.IsNotification() {
			w.WriteHeader(http.StatusAccepted)
			return
		}
		var result string
		switch msg.Method {
		case MethodInitialize:
			result = `{"protocolVersion":"2025-11-25","capabilities":{"tools":{}},"serverInfo":{"name":"mock","version":"1"}}`
		case MethodToolsList:
			result = `{"tools":[` + mockTool + `]}`
		case MethodToolsCall:
			m.mu.Lock()
			m.calls = append(m.calls, append(json.RawMessage(nil), msg.Params...))
			m.mu.Unlock()
			result = `{"content":[{"type":"text","text":"echoed"}],"structuredContent":{"ok":true}}`
		case MethodResourcesList:
			result = `{"resources":[` + mockResource + `]}`
		case MethodResourcesRead:
			result = `{"contents":[{"uri":"file:///a","text":"abc"}],"_meta":{"r":1}}`
		case MethodPromptsList:
			result = `{"prompts":[` + mockPrompt + `]}`
		case MethodPromptsGet:
			result = `{"messages":[{"role":"user","content":{"type":"text","text":"hi"}}],"_meta":{"p":1}}`
		default:
			result = `{}`
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"jsonrpc":"2.0","id":` + string(msg.ID) + `,"result":` + result + `}`))
	}))
	t.Cleanup(m.Close)
	return m
}

func (m *mockUpstream) received() []json.RawMessage {
	m.mu.Lock()
	defer m.mu.Unlock()
	return append([]json.RawMessage(nil), m.calls...)
}

func TestClientFactoryDefault(t *testing.T) {
	s := NewServer(ServerConfig{})
	if got, want := reflect.ValueOf(s.Deps().ClientFactory).Pointer(), reflect.ValueOf(DefaultClientFactory).Pointer(); got != want {
		t.Fatal("Deps.ClientFactory does not default to DefaultClientFactory")
	}

	up := startMockUpstream(t)
	client, err := s.Deps().ClientFactory(ClientConfig{ServerID: "m", URL: up.URL, TransportType: "http"})
	if err != nil {
		t.Fatalf("default factory: %v", err)
	}
	defer client.Close()
	if !client.Healthy() {
		t.Fatal("a connected client is not healthy")
	}
	ctx := context.Background()

	tools, err := client.ListTools(ctx)
	if err != nil || len(tools) != 1 || string(tools[0]) != mockTool {
		t.Fatalf("ListTools = %s, %v; want the upstream's raw tool %s", tools, err, mockTool)
	}
	res, err := client.ListResources(ctx)
	if err != nil || len(res) != 1 || string(res[0]) != mockResource {
		t.Fatalf("ListResources = %s, %v; want the raw resource", res, err)
	}
	prompts, err := client.ListPrompts(ctx)
	if err != nil || len(prompts) != 1 || string(prompts[0]) != mockPrompt {
		t.Fatalf("ListPrompts = %s, %v; want the raw prompt", prompts, err)
	}

	out, err := client.CallTool(ctx, "echo", json.RawMessage(`{"input":"hi"}`), CallOptions{})
	if err != nil || !strings.Contains(string(out), `"structuredContent":{"ok":true}`) {
		t.Fatalf("CallTool = %s, %v; want the raw result with structuredContent", out, err)
	}
	calls := up.received()
	if len(calls) != 1 || !strings.Contains(string(calls[0]), `"name":"echo"`) || !strings.Contains(string(calls[0]), `"input":"hi"`) {
		t.Fatalf("upstream received %s, want the tool name and the arguments", calls)
	}
	read, err := client.ReadResource(ctx, "file:///a", CallOptions{})
	if err != nil || !strings.Contains(string(read), `"_meta":{"r":1}`) {
		t.Fatalf("ReadResource = %s, %v; want the raw result", read, err)
	}
	got, err := client.GetPrompt(ctx, "greet", json.RawMessage(`{"name":"x"}`), CallOptions{})
	if err != nil || !strings.Contains(string(got), `"_meta":{"p":1}`) {
		t.Fatalf("GetPrompt = %s, %v; want the raw result", got, err)
	}

	// The built-in client cannot send a per-call credential or forwarded headers:
	// such a call fails instead of going out with the shared credential.
	auth := &AuthOverride{Header: http.Header{"Authorization": {"Bearer user-token"}}}
	if _, err := client.CallTool(ctx, "echo", nil, CallOptions{Auth: auth}); !errors.Is(err, errOptionUnsupported) {
		t.Fatalf("CallTool with an auth override: err = %v, want errOptionUnsupported", err)
	}
	if _, err := client.ReadResource(ctx, "file:///a", CallOptions{ForwardHeaders: http.Header{"X-A": {"1"}}}); !errors.Is(err, errOptionUnsupported) {
		t.Fatalf("ReadResource with forwarded headers: err = %v, want errOptionUnsupported", err)
	}
	if n := len(up.received()); n != 1 {
		t.Fatalf("a refused call reached the upstream (%d calls)", n)
	}
	if s := auth.String() + " " + fmtGo(auth); strings.Contains(s, "user-token") {
		t.Fatalf("AuthOverride prints its secret: %s", s)
	}

	if _, err := DefaultClientFactory(ClientConfig{ServerID: "down", URL: "http://127.0.0.1:1", TransportType: "http"}); err == nil {
		t.Fatal("the default factory returned a client for a server that does not answer")
	}
}

func fmtGo(a *AuthOverride) string { return a.GoString() }

func TestBuildStaticPoolSkipsFailedServers(t *testing.T) {
	up := startMockUpstream(t)
	pool := BuildStaticPool(context.Background(), config.MCPConfig{Servers: map[string]config.MCPServerConfig{
		"up":   {URL: up.URL, Transport: "http"},
		"down": {URL: "http://127.0.0.1:1", Transport: "http"},
	}})
	if len(pool) != 1 || pool[0].ServerID() != "up" || pool[0].ToolCount() != 1 {
		t.Fatalf("pool = %d clients, want only the reachable server with its tool", len(pool))
	}
	for _, c := range pool {
		c.Close()
	}
}
