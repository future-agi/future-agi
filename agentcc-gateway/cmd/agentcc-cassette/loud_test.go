package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/cassette"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// saveCassette writes c through cassette.Save (scrub and verify) into dir.
func saveCassette(t *testing.T, dir string, c *cassette.Cassette) string {
	t.Helper()
	c.TestAccount, c.RecordedAt = "ci-loud", time.Now().UTC().Format(time.RFC3339)
	path := filepath.Join(dir, string(c.Kind)+".cassette.json")
	if err := cassette.Save(path, c, cassette.ScrubConfig{}); err != nil {
		t.Fatal(err)
	}
	return path
}

func mcpCassette() *cassette.Cassette {
	return &cassette.Cassette{Kind: cassette.KindMCP, MCP: &cassette.MCPRecording{ProtocolVersion: "2025-11-25",
		Exchanges: []cassette.MCPExchange{
			{Method: "initialize", Params: json.RawMessage(`{"protocolVersion":"2025-11-25"}`),
				Result: json.RawMessage(`{"protocolVersion":"2025-11-25","capabilities":{"tools":{}},"serverInfo":{"name":"w"}}`)},
			{Method: "tools/list", Result: json.RawMessage(`{"tools":[{"name":"get_weather","inputSchema":{"type":"object"}}]}`)},
			{Method: "resources/list", Error: json.RawMessage(`{"code":-32601,"message":"no resources"}`)},
			{Method: "prompts/list", Error: json.RawMessage(`{"code":-32601,"message":"no prompts"}`)},
			{Method: "tools/call", Params: json.RawMessage(`{"name":"get_weather","arguments":{"city":"Paris"}}`),
				Result: json.RawMessage(`{"content":[{"type":"text","text":"Sunny"}]}`)},
		}}}
}

func rawRPC(t *testing.T, url, body string) (int, map[string]any, http.Header) {
	t.Helper()
	resp, err := http.Post(url, "application/json", strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var out map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&out)
	return resp.StatusCode, out, resp.Header
}

func getMisses(t *testing.T, base string) []string {
	t.Helper()
	resp, err := http.Get(base + "/__cassette/misses")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var out struct{ Misses []string }
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatal(err)
	}
	return out.Misses
}

// TestUnknownRequestFailsLoud: a request no cassette answers is an error,
// never a result that could pass for one. The gateway's own client gets a
// JSON-RPC error; a raw caller also sees X-Agentcc-Cassette: miss (HTTP
// 501 for REST); each miss is logged and listed without its values; and
// the replay exits 3 when stopped. Protocol housekeeping is not a miss.
func TestUnknownRequestFailsLoud(t *testing.T) {
	secretArg := tokenShaped("live-arg-value")
	t.Run("mcp", func(t *testing.T) {
		file := saveCassette(t, t.TempDir(), mcpCassette())
		client, r := replayClient(t, file) // connect: discover, initialize, lists, ping, GET: no miss
		addr := r.base
		if m := getMisses(t, addr); len(m) != 0 {
			t.Fatalf("housekeeping counted as misses: %v", m)
		}
		_, err := client.CallToolRaw(context.Background(), "get_weather", json.RawMessage(`{"city":"Berlin","token":"`+secretArg+`"}`), mcp.CallOptions{Timeout: 5 * time.Second})
		var ue *mcp.UpstreamError
		if !errors.As(err, &ue) || ue.Code != -32603 || !strings.Contains(ue.Message, "no recorded reply for tools/call get_weather") {
			t.Fatalf("an unrecorded call reached the gateway's client as %v, want a JSON-RPC error naming it", err)
		}
		if strings.Contains(ue.Message, "Berlin") || strings.Contains(ue.Message, secretArg) {
			t.Errorf("the miss carries argument values: %s", ue.Message)
		}
		for _, body := range []string{
			`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"no_such_tool","arguments":{}}}`,
			`{"jsonrpc":"2.0","id":2,"method":"resources/read","params":{"uri":"file:///etc/hosts"}}`,
		} {
			code, out, h := rawRPC(t, addr+"/mcp", body)
			if code != 200 || out["error"] == nil || out["result"] != nil || h.Get("X-Agentcc-Cassette") != "miss" {
				t.Errorf("raw miss = %d %v (header %q)", code, out, h.Get("X-Agentcc-Cassette"))
			}
		}
		if code, _, h := rawRPC(t, addr+"/not-mcp", `{"jsonrpc":"2.0","id":3,"method":"ping"}`); code != http.StatusNotImplemented || h.Get("X-Agentcc-Cassette") != "miss" {
			t.Errorf("a wrong path = %d", code)
		}
		for _, body := range []string{`{"jsonrpc":"2.0","id":4,"method":"ping"}`, `{"jsonrpc":"2.0","method":"notifications/initialized"}`,
			`{"jsonrpc":"2.0","id":5,"method":"subscriptions/listen","params":{}}`} {
			rawRPC(t, addr+"/mcp", body)
		}
		misses := getMisses(t, addr)
		if len(misses) != 4 {
			t.Fatalf("misses = %q, want 4", misses)
		}
		for _, m := range misses {
			if strings.Contains(m, "Berlin") || strings.Contains(m, secretArg) || strings.Contains(m, "/etc/hosts") {
				t.Errorf("a listed miss carries a value: %q", m)
			}
		}
		code, logs := stopReplay(t, client, r)
		if code != exitMisses || strings.Count(logs, "MISS") < 8 || !strings.Contains(logs, "4 request(s) had no recorded reply") {
			t.Errorf("replay exited %d (want %d) with logs: %s", code, exitMisses, logs)
		}
	})
	t.Run("http", func(t *testing.T) {
		file := saveCassette(t, t.TempDir(), &cassette.Cassette{Kind: cassette.KindHTTP, HTTP: &cassette.HTTPRecording{
			Exchanges: []cassette.HTTPExchange{{Request: cassette.HTTPRequest{Method: "GET", Path: "/v1/items", Query: "q=x"},
				Response: cassette.HTTPResponse{Status: 404, Headers: http.Header{"Content-Type": {"application/json"}},
					Body: &cassette.Body{JSON: json.RawMessage(`{"error":"not found"}`)}}}}}})
		addrFile := filepath.Join(t.TempDir(), "addr")
		r := startCLI(t, "replay", "--cassette", file, "--addr", "127.0.0.1:0", "--addr-file", addrFile)
		base := "http://" + waitFile(t, addrFile, r)
		if code, body, _ := send(t, "GET", base+"/v1/items?q=x", "any", ""); code != 404 || !strings.Contains(body, "not found") {
			t.Fatalf("a recorded 404 replayed as %d %s", code, body)
		}
		for _, req := range []struct{ method, path, body string }{
			{"GET", "/v1/items?q=y&api_key=" + secretArg, ""}, {"POST", "/v1/items", `{"name":"` + secretArg + `"}`}, {"GET", "/v2/other", ""},
		} {
			code, body, h := send(t, req.method, base+req.path, "any", req.body)
			var out struct {
				Error struct{ Type, Message string }
			}
			_ = json.Unmarshal([]byte(body), &out)
			if code != http.StatusNotImplemented || h.Get("X-Agentcc-Cassette") != "miss" || out.Error.Type != "cassette_miss" {
				t.Errorf("%s %s = %d %s, want a loud miss", req.method, req.path, code, body)
			}
			if strings.Contains(body, secretArg) {
				t.Errorf("the miss echoes a request value")
			}
		}
		code, logs := r.stop(t)
		if code != exitMisses || !strings.Contains(logs, "MISS GET /v1/items query(api_key,q)") || strings.Contains(logs, secretArg) {
			t.Errorf("replay exited %d with logs: %s", code, logs)
		}
	})
}
