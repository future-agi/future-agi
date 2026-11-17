package restmcp

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/catalog/connectors"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// Fake credentials for the local fakes; none is a real token.
const (
	testToken      = "ya29.cn10-fake-user-token-0123456789abcdef"
	otherToken     = "ya29.cn10-fake-second-user-token-fedcba9876543210"
	slashToken     = "ya29.cn10-fake/slash+user-token-0123456789=="
	testAPIKey     = "cn10-fake-adyen-key-AQEyhmfxK4"
	gmailOrigin    = "https://gmail.googleapis.com"
	adyenOrigin    = "https://balanceplatform-api-test.adyen.com"
	testCN4Catalog = "scripts/connector-gen/rest/tests/fixtures/catalog"
)

// repoPath is a path from the repository root.
func repoPath(t *testing.T, rel string) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("no caller")
	}
	// .../agentcc-gateway/internal/connectorcatalog/restmcp/helpers_test.go
	return filepath.Join(filepath.Dir(file), "..", "..", "..", "..", rel)
}

// cn4Catalog is the rest example catalog (six rest apps, one or more per spec
// format), loaded as the gateway loads an operator's catalog directory.
func cn4Catalog(t *testing.T) *manifest.Catalog {
	t.Helper()
	cat, err := connectors.LoadDir(repoPath(t, testCN4Catalog))
	if err != nil {
		t.Fatalf("the rest example catalog does not load: %v", err)
	}
	return cat.Records
}

// compileApp compiles one app of cat.
func compileOne(t *testing.T, cat *manifest.Catalog, appID string) *App {
	t.Helper()
	apps, err := Compile(cat)
	if err != nil {
		t.Fatalf("Compile: %v", err)
	}
	for _, a := range apps {
		if a.ID == appID {
			return a
		}
	}
	t.Fatalf("no app %s among %d compiled", appID, len(apps))
	return nil
}

// vendorFake is a loopback stand-in for a vendor API: it answers with a
// handler and keeps every request it got.
type vendorFake struct {
	*httptest.Server
	mu   sync.Mutex
	reqs []recorded
}

type recorded struct {
	Method, Path, Query string
	Header              http.Header
	Body                []byte
}

func newVendorFake(t *testing.T, h http.HandlerFunc) *vendorFake {
	t.Helper()
	f := &vendorFake{}
	f.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		f.mu.Lock()
		f.reqs = append(f.reqs, recorded{r.Method, r.URL.EscapedPath(), r.URL.RawQuery, r.Header.Clone(), body})
		f.mu.Unlock()
		h(w, r)
	}))
	t.Cleanup(f.Close)
	return f
}

func (f *vendorFake) requests() []recorded {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]recorded(nil), f.reqs...)
}

// jsonAnswer answers status with v as JSON.
func jsonAnswer(status int, v any) http.HandlerFunc {
	return func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json; charset=UTF-8")
		w.WriteHeader(status)
		_ = json.NewEncoder(w).Encode(v)
	}
}

// logBuffer collects the runtime's log lines.
type logBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (l *logBuffer) Write(p []byte) (int, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.buf.Write(p)
}

func (l *logBuffer) String() string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.buf.String()
}

// runtimeUnder is the runtime serving apps over loopback HTTP, with each
// vendor origin in replay answered by that URL.
type runtimeUnder struct {
	*httptest.Server
	logs *logBuffer
}

func startRuntime(t *testing.T, apps []*App, replay map[string]string, mutate ...func(*Options)) *runtimeUnder {
	t.Helper()
	logs := &logBuffer{}
	o := Options{Replay: replay, Logger: slog.New(slog.NewJSONHandler(logs, &slog.HandlerOptions{Level: slog.LevelDebug})),
		Version: "test"}
	for _, m := range mutate {
		m(&o)
	}
	s, err := New(apps, o)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	s.vendor.retryBase = time.Millisecond
	srv := httptest.NewServer(s)
	t.Cleanup(srv.Close)
	return &runtimeUnder{Server: srv, logs: logs}
}

// rpcReply is one JSON-RPC answer.
type rpcReply struct {
	Status int
	Header http.Header
	Raw    string          // the body as sent
	ID     json.RawMessage `json:"id"`
	Result json.RawMessage `json:"result"`
	Error  *struct {
		Code    int             `json:"code"`
		Message string          `json:"message"`
		Data    json.RawMessage `json:"data"`
	} `json:"error"`
}

// post sends one JSON-RPC message to path with the headers given as
// name, value pairs (Content-Type and Accept are set unless given).
func (rt *runtimeUnder) post(t *testing.T, path, body string, headers ...string) rpcReply {
	t.Helper()
	req, err := http.NewRequestWithContext(context.Background(), http.MethodPost, rt.URL+path, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	var r rpcReply
	if len(bytes.TrimSpace(data)) > 0 {
		if err := json.Unmarshal(data, &r); err != nil {
			t.Fatalf("POST %s answered %d with non-JSON %q", path, resp.StatusCode, data)
		}
	}
	r.Status, r.Header, r.Raw = resp.StatusCode, resp.Header, string(data)
	return r
}

// legacyCall sends a legacy (2025-11-25) request with the bearer.
func (rt *runtimeUnder) legacyCall(t *testing.T, path, token, method, params string) rpcReply {
	t.Helper()
	body := `{"jsonrpc":"2.0","id":7,"method":"` + method + `"`
	if params != "" {
		body += `,"params":` + params
	}
	body += "}"
	return rt.post(t, path, body, "Authorization", "Bearer "+token, "MCP-Protocol-Version", "2025-11-25")
}

// callResult is a tools/call result.
type callResult struct {
	Content []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	} `json:"content"`
	Structured json.RawMessage            `json:"structuredContent"`
	IsError    bool                       `json:"isError"`
	Meta       map[string]json.RawMessage `json:"_meta"`
}

func decodeCall(t *testing.T, r rpcReply) callResult {
	t.Helper()
	if r.Error != nil || r.Status != http.StatusOK {
		t.Fatalf("tools/call answered HTTP %d, error %+v", r.Status, r.Error)
	}
	var c callResult
	if err := json.Unmarshal(r.Result, &c); err != nil {
		t.Fatalf("result %s: %v", r.Result, err)
	}
	return c
}

func (c callResult) text() string {
	var parts []string
	for _, b := range c.Content {
		parts = append(parts, b.Text)
	}
	return strings.Join(parts, "\n")
}

// callTool calls a tool of the app at path on the legacy era.
func (rt *runtimeUnder) callTool(t *testing.T, path, token, tool string, args any) callResult {
	t.Helper()
	params, _ := json.Marshal(map[string]any{"name": tool, "arguments": args})
	return decodeCall(t, rt.legacyCall(t, path, token, "tools/call", string(params)))
}
