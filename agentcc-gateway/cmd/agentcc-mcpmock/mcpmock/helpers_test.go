package mcpmock

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

const fixtureDir = "../../../scripts/scenarios/fixtures/mcp/common"

// testMock starts a mock over the shared fixtures behind an httptest server.
func testMock(t *testing.T, era, auth string) (*Mock, *httptest.Server) {
	t.Helper()
	return testMockWith(t, era, Config{Auth: auth})
}

// testMockWith is testMock with more of the Config set (Era and a 5 s
// ElicitTimeout are filled in).
func testMockWith(t *testing.T, era string, cfg Config) (*Mock, *httptest.Server) {
	t.Helper()
	e, err := ParseEra(era)
	if err != nil {
		t.Fatal(err)
	}
	fix, err := LoadFixtures(fixtureDir)
	if err != nil {
		t.Fatal(err)
	}
	cfg.Era, cfg.ElicitTimeout = e, 5*time.Second
	m, err := New(cfg, fix)
	if err != nil {
		t.Fatal(err)
	}
	// The public URL must be set before the server serves anything.
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	m.SetPublicURL("http://" + ln.Addr().String())
	srv := httptest.NewUnstartedServer(m.Handler())
	srv.Listener.Close()
	srv.Listener = ln
	srv.Start()
	t.Cleanup(srv.Close)
	return m, srv
}

// meta2026 is a valid 2026-07-28 _meta, merged into params.
func meta2026(params map[string]any) map[string]any {
	if params == nil {
		params = map[string]any{}
	}
	meta := map[string]any{
		metaProtocolVersion: V2026,
		metaClientCaps:      map[string]any{},
		metaClientInfo:      map[string]any{"name": "mcpmock-test", "version": "1"},
	}
	if extra, ok := params["_meta"].(map[string]any); ok {
		for k, v := range extra {
			meta[k] = v
		}
	}
	params["_meta"] = meta
	return params
}

type httpReply struct {
	status int
	header http.Header
	body   []byte
	msg    Message
}

// rpc POSTs one JSON-RPC request. headers are "Name: value" strings.
func rpc(t *testing.T, url, method string, id any, params any, headers ...string) httpReply {
	t.Helper()
	body := map[string]any{"jsonrpc": "2.0", "method": method}
	if id != nil {
		body["id"] = id
	}
	if params != nil {
		body["params"] = params
	}
	data, _ := json.Marshal(body)
	req, _ := http.NewRequest(http.MethodPost, url, bytes.NewReader(data))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	for _, h := range headers {
		name, value, _ := strings.Cut(h, ":")
		req.Header.Set(strings.TrimSpace(name), strings.TrimSpace(value))
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("POST %s %s: %v", url, method, err)
	}
	defer resp.Body.Close()
	out := httpReply{status: resp.StatusCode, header: resp.Header}
	out.body, _ = io.ReadAll(resp.Body)
	if strings.HasPrefix(resp.Header.Get("Content-Type"), "application/json") && len(out.body) > 0 {
		_ = json.Unmarshal(out.body, &out.msg)
	}
	return out
}

// headers2026 are the mirrored headers a 2026 request needs.
func headers2026(method, name string) []string {
	h := []string{"MCP-Protocol-Version: " + V2026, "Mcp-Method: " + method}
	if name != "" {
		h = append(h, "Mcp-Name: "+name)
	}
	return h
}

// call2026 sends a well-formed 2026 request and returns the reply.
func call2026(t *testing.T, url, method string, params map[string]any, extra ...string) httpReply {
	t.Helper()
	name, _ := params["name"].(string)
	if uri, ok := params["uri"].(string); ok {
		name = uri
	}
	return rpc(t, url, method, 1, meta2026(params), append(headers2026(method, name), extra...)...)
}

// initLegacy opens a legacy session and returns its id.
func initLegacy(t *testing.T, url, version string, caps map[string]any) string {
	t.Helper()
	if caps == nil {
		caps = map[string]any{}
	}
	r := rpc(t, url, "initialize", 1, map[string]any{
		"protocolVersion": version, "capabilities": caps,
		"clientInfo": map[string]any{"name": "mcpmock-test", "version": "1"},
	})
	if r.status != 200 || r.msg.Error != nil {
		t.Fatalf("initialize %s: status %d body %s", version, r.status, r.body)
	}
	sid := r.header.Get("Mcp-Session-Id")
	if sid == "" {
		t.Fatalf("initialize %s: no Mcp-Session-Id", version)
	}
	return sid
}

func resultMap(t *testing.T, r httpReply) map[string]any {
	t.Helper()
	if r.msg.Error != nil {
		t.Fatalf("unexpected error %d %s (HTTP %d)", r.msg.Error.Code, r.msg.Error.Message, r.status)
	}
	out := map[string]any{}
	if err := json.Unmarshal(r.msg.Result, &out); err != nil {
		t.Fatalf("result: %v: %s", err, r.body)
	}
	return out
}

// sseEvent is one parsed Server-Sent Event.
type sseEvent struct {
	id, name string
	data     string
}

// sseStream opens a GET (or POST) stream and returns its events on a channel
// and a cancel func.
func sseStream(t *testing.T, req *http.Request) (<-chan sseEvent, *http.Response, func()) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	req = req.WithContext(ctx)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		cancel()
		t.Fatalf("stream: %v", err)
	}
	ch := make(chan sseEvent, 64)
	go func() {
		defer close(ch)
		defer resp.Body.Close()
		readSSE(resp.Body, ch)
	}()
	return ch, resp, cancel
}

func readSSE(r io.Reader, ch chan<- sseEvent) {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 64<<10), 16<<20)
	var ev sseEvent
	for sc.Scan() {
		line := sc.Text()
		switch {
		case line == "":
			if ev.data != "" || ev.name != "" {
				ch <- ev
			}
			ev = sseEvent{}
		case strings.HasPrefix(line, "id: "):
			ev.id = strings.TrimPrefix(line, "id: ")
		case strings.HasPrefix(line, "event: "):
			ev.name = strings.TrimPrefix(line, "event: ")
		case strings.HasPrefix(line, "data: "):
			ev.data += strings.TrimPrefix(line, "data: ")
		}
	}
}

func nextEvent(t *testing.T, ch <-chan sseEvent, within time.Duration) sseEvent {
	t.Helper()
	select {
	case ev, ok := <-ch:
		if !ok {
			t.Fatal("stream closed before the next event")
		}
		return ev
	case <-time.After(within):
		t.Fatalf("no event within %s", within)
	}
	return sseEvent{}
}

func decodeMessage(t *testing.T, data string) Message {
	t.Helper()
	var msg Message
	if err := json.Unmarshal([]byte(data), &msg); err != nil {
		t.Fatalf("event data %q: %v", data, err)
	}
	return msg
}

func postControl(t *testing.T, url, path, body string) map[string]any {
	t.Helper()
	resp, err := http.Post(url+path, "application/json", strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	out := map[string]any{}
	_ = json.NewDecoder(resp.Body).Decode(&out)
	if resp.StatusCode != 200 {
		t.Fatalf("POST %s: %d %v", path, resp.StatusCode, out)
	}
	return out
}

func getJSON(t *testing.T, url string, dst any) {
	t.Helper()
	resp, err := http.Get(url)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if err := json.NewDecoder(resp.Body).Decode(dst); err != nil {
		t.Fatal(err)
	}
}
