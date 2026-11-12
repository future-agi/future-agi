package record

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestCheckUpstream(t *testing.T) {
	for _, tc := range []struct {
		url    string
		origin bool
		ok     bool
	}{
		{"https://mcp.vendor.example/mcp", false, true},
		{"https://api.vendor.example", true, true},
		{"http://127.0.0.1:9/mcp", false, true},
		{"http://localhost:9", true, true},
		{"http://[::1]:9", true, true},
		{"http://mcp.vendor.example/mcp", false, false},       // a credential over plain http
		{"https://user:pass@api.vendor.example", true, false}, // credentials in the URL
		{"https://api.vendor.example/v1", true, false},        // a proxy upstream is an origin
		{"https://api.vendor.example?x=1", true, false},
		{"ftp://files.vendor.example", false, false},
		{"/relative/mcp", false, false},
		{"", false, false},
	} {
		if err := CheckUpstream(tc.url, tc.origin); (err == nil) != tc.ok {
			t.Errorf("CheckUpstream(%q, %v) = %v, want ok=%v", tc.url, tc.origin, err, tc.ok)
		}
	}
}

func TestCheckLoopback(t *testing.T) {
	for addr, ok := range map[string]bool{"127.0.0.1:0": true, "localhost:8080": true, "[::1]:0": true,
		"0.0.0.0:0": false, ":8080": false, "192.168.1.5:0": false, "nonsense": false} {
		if err := CheckLoopback(addr); (err == nil) != ok {
			t.Errorf("CheckLoopback(%q) = %v, want ok=%v", addr, err, ok)
		}
	}
}

// TestProxyRecordsWithoutFollowingRedirects: the proxy passes a redirect
// back as it came (an OAuth code in Location is recorded, then scrubbed by
// Save), forwards the client's credential, keeps Proxy-Authorization for
// itself, and records the request body.
func TestProxyRecordsWithoutFollowingRedirects(t *testing.T) {
	var gotAuth, gotProxyAuth atomic.Value
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth.Store(r.Header.Get("Authorization"))
		gotProxyAuth.Store(r.Header.Get("Proxy-Authorization"))
		_, _ = io.Copy(io.Discard, r.Body)
		w.Header().Set("Location", "http://127.0.0.1:9/cb?code=abc&state=xyz")
		w.WriteHeader(http.StatusFound)
	}))
	defer upstream.Close()
	p, err := NewProxy(ProxyOptions{Upstream: upstream.URL, TestAccount: "ci-proxy"})
	if err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(p)
	defer srv.Close()
	req, _ := http.NewRequest("POST", srv.URL+"/authorize?response_type=code", strings.NewReader(`{"a":1}`))
	req.Header.Set("Authorization", "Bearer client-credential")
	req.Header.Set("Proxy-Authorization", "Basic for-the-proxy")
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusFound || resp.Header.Get("Location") == "" {
		t.Fatalf("the client got %d, Location %q: the proxy followed or dropped the redirect", resp.StatusCode, resp.Header.Get("Location"))
	}
	if gotAuth.Load() != "Bearer client-credential" || gotProxyAuth.Load() != "" {
		t.Errorf("upstream saw Authorization %q, Proxy-Authorization %q", gotAuth.Load(), gotProxyAuth.Load())
	}
	c := p.Cassette()
	if len(c.HTTP.Exchanges) != 1 {
		t.Fatalf("recorded %d exchanges", len(c.HTTP.Exchanges))
	}
	x := c.HTTP.Exchanges[0]
	if x.Request.Method != "POST" || x.Request.Path != "/authorize" || x.Request.Query != "response_type=code" ||
		string(x.Request.Body.JSON) != `{"a":1}` || x.Response.Status != http.StatusFound || x.Request.Headers.Get("Proxy-Authorization") != "" {
		t.Errorf("recorded %+v", x)
	}
}

// fakeMCP answers every POST with handle's status and body.
func fakeMCP(t *testing.T, handle func(method string) (int, string)) (*httptest.Server, *atomic.Int64) {
	t.Helper()
	var hits atomic.Int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		var msg struct {
			ID     json.RawMessage `json:"id"`
			Method string          `json:"method"`
		}
		_ = json.NewDecoder(r.Body).Decode(&msg)
		status, body := handle(msg.Method)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = io.WriteString(w, strings.ReplaceAll(body, "ID", string(msg.ID)))
	}))
	t.Cleanup(srv.Close)
	return srv, &hits
}

// TestMCPRecorderStops: a refused credential, a protocol error, an
// unreachable server or a bad call list stops the recording with an error
// that never carries the URL (which may hold a URL-variable credential).
func TestMCPRecorderStops(t *testing.T) {
	ctx := context.Background()
	t.Run("credential refused", func(t *testing.T) {
		srv, _ := fakeMCP(t, func(string) (int, string) { return http.StatusUnauthorized, "" })
		_, err := MCP(ctx, MCPOptions{URL: srv.URL + "/mcp", TestAccount: "ci-x", Era: "2025-11-25"})
		if err == nil || !strings.Contains(err.Error(), "refused the credential") {
			t.Fatalf("err = %v", err)
		}
	})
	t.Run("protocol error", func(t *testing.T) {
		srv, _ := fakeMCP(t, func(m string) (int, string) {
			if m == "initialize" {
				return 200, `{"jsonrpc":"2.0","id":ID,"result":{"protocolVersion":"2025-11-25"}}`
			}
			if m == "notifications/initialized" {
				return 202, ""
			}
			return 400, `{"jsonrpc":"2.0","id":ID,"error":{"code":-32020,"message":"header mismatch"}}`
		})
		_, err := MCP(ctx, MCPOptions{URL: srv.URL + "/mcp", TestAccount: "ci-x", Era: "2025-11-25"})
		if err == nil || !strings.Contains(err.Error(), "protocol error -32020") {
			t.Fatalf("err = %v", err)
		}
	})
	t.Run("2026 asked of a legacy server", func(t *testing.T) {
		srv, _ := fakeMCP(t, func(string) (int, string) {
			return 400, `{"jsonrpc":"2.0","id":ID,"error":{"code":-32600,"message":"missing session"}}`
		})
		if _, err := MCP(ctx, MCPOptions{URL: srv.URL + "/mcp", TestAccount: "ci-x", Era: "2026-07-28"}); err == nil {
			t.Fatal("a 2026-only recording accepted a legacy server")
		}
	})
	t.Run("unreachable, without the URL in the error", func(t *testing.T) {
		addr := refusedAddr(t)
		_, err := MCP(ctx, MCPOptions{URL: "http://" + addr + "/secret-url-variable-value/mcp", TestAccount: "ci-x",
			Era: "2025-11-25", Timeout: 2 * time.Second})
		if err == nil || strings.Contains(err.Error(), "secret-url-variable-value") {
			t.Fatalf("err = %v", err)
		}
	})
	t.Run("an era the recorder does not speak, before any request", func(t *testing.T) {
		srv, hits := fakeMCP(t, func(string) (int, string) { return 200, "" })
		for _, era := range []string{"2024-11-05", "2099-01-01", "latest"} {
			if _, err := MCP(ctx, MCPOptions{URL: srv.URL + "/mcp", TestAccount: "ci-x", Era: era}); err == nil {
				t.Errorf("era %q was accepted", era)
			}
		}
		if hits.Load() != 0 {
			t.Fatalf("hits = %d", hits.Load())
		}
	})
	t.Run("bad call list, before any request", func(t *testing.T) {
		srv, hits := fakeMCP(t, func(string) (int, string) { return 200, "" })
		_, err := MCP(ctx, MCPOptions{URL: srv.URL + "/mcp", TestAccount: "ci-x", Calls: []Call{{Name: ""}}})
		if err == nil || hits.Load() != 0 {
			t.Fatalf("err = %v, hits = %d", err, hits.Load())
		}
	})
}

// TestMCPRecorderPaginatesAndRecordsErrors: every tools/list page is
// recorded with the cursor that asked for it, and a list the server does
// not serve is recorded as its error.
func TestMCPRecorderPaginatesAndRecordsErrors(t *testing.T) {
	var page atomic.Int64
	srv, _ := fakeMCP(t, func(m string) (int, string) {
		switch m {
		case "initialize":
			return 200, `{"jsonrpc":"2.0","id":ID,"result":{"protocolVersion":"2025-06-18"}}`
		case "notifications/initialized":
			return 202, ""
		case "tools/list":
			if page.Add(1) == 1 {
				return 200, `{"jsonrpc":"2.0","id":ID,"result":{"tools":[{"name":"a","inputSchema":{}}],"nextCursor":"c2"}}`
			}
			return 200, `{"jsonrpc":"2.0","id":ID,"result":{"tools":[{"name":"b","inputSchema":{}}]}}`
		}
		return 200, `{"jsonrpc":"2.0","id":ID,"error":{"code":-32601,"message":"not here"}}`
	})
	c, err := MCP(context.Background(), MCPOptions{URL: srv.URL + "/mcp", TestAccount: "ci-x", Era: "2025-06-18"})
	if err != nil {
		t.Fatal(err)
	}
	var got []string
	for _, x := range c.MCP.Exchanges {
		got = append(got, x.Method+" "+string(x.Params))
	}
	want := "initialize {\"capabilities\":{},\"clientInfo\":{\"name\":\"agentcc-cassette\",\"version\":\"1\"},\"protocolVersion\":\"2025-06-18\"}|" +
		"tools/list |tools/list {\"cursor\":\"c2\"}|resources/list |prompts/list "
	if strings.Join(got, "|") != want || c.MCP.ProtocolVersion != "2025-06-18" {
		t.Errorf("recorded\n%s\nwant\n%s", strings.Join(got, "|"), want)
	}
	if len(c.MCP.Exchanges[3].Error) == 0 || len(c.MCP.Exchanges[4].Error) == 0 {
		t.Error("the list errors were not recorded")
	}
}
