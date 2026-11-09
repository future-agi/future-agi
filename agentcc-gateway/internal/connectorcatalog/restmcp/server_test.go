package restmcp

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// TestBearerRequired: every request, initialize and server/discover
// included, needs the app's credential: none gets 401 with a Bearer
// challenge naming the app and its scopes, a non-bearer Authorization 401
// with error="invalid_request", and the vendor is never called; an api_key
// app's missing part gets 401 with a challenge naming where it goes. With
// the credential the same requests succeed.
func TestBearerRequired(t *testing.T) {
	rt, fake := gmailRuntime(t)
	bodies := map[string]string{
		"initialize": `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}`,
		"tools/list": `{"jsonrpc":"2.0","id":2,"method":"tools/list"}`,
		"tools/call": `{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_profile","arguments":{}}}`,
	}
	for method, body := range bodies {
		for _, c := range []struct {
			name, auth, wantErr string
		}{{"none", "", ""}, {"basic", "Basic dXNlcjpwYXNz", `error="invalid_request"`}, {"empty bearer", "Bearer ", `error="invalid_request"`}} {
			t.Run(method+"/"+c.name, func(t *testing.T) {
				headers := []string{"MCP-Protocol-Version", "2025-11-25"}
				if c.auth != "" {
					headers = append(headers, "Authorization", c.auth)
				}
				r := rt.post(t, "/gmail/mcp", body, headers...)
				if r.Status != http.StatusUnauthorized {
					t.Fatalf("HTTP %d, want 401", r.Status)
				}
				ch := r.Header.Get("WWW-Authenticate")
				if !strings.HasPrefix(ch, `Bearer realm="gmail"`) || !strings.Contains(ch, "https://www.googleapis.com/auth/gmail.readonly") {
					t.Errorf("WWW-Authenticate = %q, want a Bearer challenge for gmail with its scopes", ch)
				}
				if c.wantErr != "" && !strings.Contains(ch, c.wantErr) {
					t.Errorf("WWW-Authenticate = %q, want %s", ch, c.wantErr)
				}
				if c.wantErr == "" && strings.Contains(ch, "error=") {
					t.Errorf("WWW-Authenticate = %q: RFC 6750 sends no error code when no credential was sent", ch)
				}
				if r.Error == nil || r.Error.Code != codeInvalidRequest {
					t.Errorf("body error = %+v, want a JSON-RPC invalid request", r.Error)
				}
			})
		}
	}
	discover := `{"jsonrpc":"2.0","id":4,"method":"server/discover","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}`
	if r := rt.post(t, "/gmail/mcp", discover, "MCP-Protocol-Version", "2026-07-28", "Mcp-Method", "server/discover"); r.Status != http.StatusUnauthorized {
		t.Errorf("server/discover without a bearer: HTTP %d, want 401", r.Status)
	}
	if n := len(fake.requests()); n != 0 {
		t.Fatalf("the vendor got %d requests from unauthenticated calls", n)
	}

	for method, body := range bodies {
		r := rt.post(t, "/gmail/mcp", body, "MCP-Protocol-Version", "2025-11-25", "Authorization", "Bearer "+testToken)
		if r.Status != http.StatusOK || r.Error != nil {
			t.Errorf("%s with the bearer: HTTP %d, error %+v", method, r.Status, r.Error)
		}
	}
	if got := fake.requests(); len(got) != 1 || got[0].Header.Get("Authorization") != "Bearer "+testToken {
		t.Errorf("the vendor got %d requests; the call's Authorization was not the caller's bearer", len(got))
	}

	// An api_key app: the challenge names the part and where it goes.
	adyen := compileOne(t, cn4Catalog(t), "adyen")
	vendor := newVendorFake(t, jsonAnswer(200, map[string]any{"data": []any{}}))
	ka := startRuntime(t, []*App{adyen}, map[string]string{adyenOrigin: vendor.URL})
	r := ka.post(t, "/adyen/mcp", bodies["tools/list"], "MCP-Protocol-Version", "2025-11-25", "Authorization", "Bearer "+testToken)
	if r.Status != http.StatusUnauthorized || r.Header.Get("WWW-Authenticate") != `APIKey realm="adyen", in="header", name="X-API-Key"` {
		t.Errorf("api_key app without X-API-Key: HTTP %d, WWW-Authenticate %q", r.Status, r.Header.Get("WWW-Authenticate"))
	}
	r = ka.post(t, "/adyen/mcp", bodies["tools/list"], "MCP-Protocol-Version", "2025-11-25", "X-API-Key", testAPIKey)
	if r.Status != http.StatusOK || r.Error != nil {
		t.Errorf("api_key app with X-API-Key: HTTP %d, error %+v", r.Status, r.Error)
	}
}

// TestBothEras: the gateway's own upstream client (M13) connects to the
// runtime pinned to 2025-11-25 and to 2026-07-28, and with era detection,
// lists every tool and calls one, each call with its own bearer
// (CallOptions.Auth, how M25 sends a user's token per call). On the wire:
// initialize negotiates a served legacy revision and opens no session; a
// 2026 answer carries the envelope; a 2026 request without its _meta is
// refused with -32021; an unserved version with -32022.
func TestBothEras(t *testing.T) {
	for _, era := range []string{"2025-11-25", "2026-07-28", "auto"} {
		t.Run(era, func(t *testing.T) {
			rt, fake := gmailRuntime(t)
			c := mcp.NewClient(mcp.ClientConfig{ServerID: "gmail", URL: rt.URL + "/gmail/mcp", TransportType: "http",
				Auth: mcp.AuthConfig{Type: "bearer", Token: testToken}, Era: era})
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			if err := c.Connect(ctx); err != nil {
				t.Fatalf("Connect: %v", err)
			}
			defer c.Close()
			wantEra := era
			if era == "auto" {
				wantEra = "2026-07-28"
			}
			if c.Era() != wantEra {
				t.Errorf("negotiated %q, want %q", c.Era(), wantEra)
			}
			if c.ToolCount() != 10 {
				t.Errorf("listed %d tools, want Gmail's 10", c.ToolCount())
			}
			for _, tool := range c.Tools() {
				if strings.Contains(string(tool.InputSchema), `"x-`) {
					t.Errorf("tool %s lists a generator annotation: %s", tool.Name, tool.InputSchema)
				}
			}
			user := &mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer " + otherToken}}}
			raw, err := c.CallToolRaw(ctx, "search_threads", json.RawMessage(`{"q":"from:alice","maxResults":2}`), mcp.CallOptions{Auth: user})
			if err != nil {
				t.Fatalf("CallToolRaw: %v", err)
			}
			var res callResult
			if err := json.Unmarshal(raw, &res); err != nil || res.IsError || !strings.Contains(res.text(), "Quarterly numbers") {
				t.Fatalf("search_threads: %s (%v)", raw, err)
			}
			calls := fake.requests()
			if len(calls) != 1 {
				t.Fatalf("the vendor got %d requests, want 1", len(calls))
			}
			if got := calls[0].Header.Get("Authorization"); got != "Bearer "+otherToken {
				t.Errorf("the vendor got Authorization %q, want the call's own bearer, not the connection's", got)
			}
			if calls[0].Query != "maxResults=2&q=from%3Aalice" {
				t.Errorf("the vendor got query %q", calls[0].Query)
			}
		})
	}

	rt, _ := gmailRuntime(t)
	auth := []string{"Authorization", "Bearer " + testToken}
	init := func(version string) rpcReply {
		return rt.post(t, "/gmail/mcp", `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"`+version+
			`","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}`, auth...)
	}
	for version, want := range map[string]string{"2025-11-25": "2025-11-25", "2025-06-18": "2025-06-18",
		"2025-03-26": "2025-03-26", "2099-01-01": "2025-11-25", "2026-07-28": "2025-11-25"} {
		r := init(version)
		var res struct {
			ProtocolVersion string          `json:"protocolVersion"`
			Capabilities    json.RawMessage `json:"capabilities"`
			ServerInfo      struct{ Name string }
		}
		_ = json.Unmarshal(r.Result, &res)
		if r.Status != 200 || res.ProtocolVersion != want || res.ServerInfo.Name != ServerName || string(res.Capabilities) != `{"tools":{}}` {
			t.Errorf("initialize %s: HTTP %d, %s; want version %s", version, r.Status, r.Result, want)
		}
		if sid := r.Header.Get("Mcp-Session-Id"); sid != "" {
			t.Errorf("initialize opened a session %q; the runtime keeps none", sid)
		}
	}

	meta := `"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}`
	r := rt.post(t, "/gmail/mcp", `{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{`+meta+`}}`,
		append(auth, "MCP-Protocol-Version", "2026-07-28", "Mcp-Method", "tools/list")...)
	var env struct {
		ResultType string                     `json:"resultType"`
		TTLMs      int64                      `json:"ttlMs"`
		CacheScope string                     `json:"cacheScope"`
		Meta       map[string]json.RawMessage `json:"_meta"`
		Tools      []json.RawMessage          `json:"tools"`
	}
	_ = json.Unmarshal(r.Result, &env)
	if r.Status != 200 || env.ResultType != "complete" || env.TTLMs <= 0 || env.CacheScope != "public" || len(env.Tools) != 10 ||
		!strings.Contains(string(env.Meta["io.modelcontextprotocol/serverInfo"]), ServerName) {
		t.Errorf("2026 tools/list: HTTP %d, %s", r.Status, r.Result)
	}
	r = rt.post(t, "/gmail/mcp", `{"jsonrpc":"2.0","id":3,"method":"tools/list"}`,
		append(auth, "MCP-Protocol-Version", "2026-07-28", "Mcp-Method", "tools/list")...)
	if r.Status != 400 || r.Error == nil || r.Error.Code != -32021 {
		t.Errorf("2026 request without _meta: HTTP %d, error %+v; want 400 -32021", r.Status, r.Error)
	}
	r = rt.post(t, "/gmail/mcp", `{"jsonrpc":"2.0","id":4,"method":"tools/list"}`, append(auth, "MCP-Protocol-Version", "2024-11-05")...)
	if r.Status != 400 || r.Error == nil || r.Error.Code != -32022 {
		t.Errorf("legacy 2024-11-05: HTTP %d, error %+v; want 400 -32022", r.Status, r.Error)
	}
	r = rt.post(t, "/gmail/mcp", `{"jsonrpc":"2.0","id":5,"method":"server/discover"}`, append(auth, "MCP-Protocol-Version", "2025-11-25")...)
	if r.Error == nil || r.Error.Code != codeMethodNotFound {
		t.Errorf("server/discover on a legacy request: %+v, want method not found", r.Error)
	}
	r = rt.post(t, "/gmail/mcp", `{"jsonrpc":"2.0","method":"notifications/initialized"}`, auth...)
	if r.Status != http.StatusAccepted {
		t.Errorf("a notification: HTTP %d, want 202", r.Status)
	}
	req, _ := http.NewRequest(http.MethodGet, rt.URL+"/gmail/mcp", nil)
	req.Header.Set("Authorization", "Bearer "+testToken)
	if resp, err := http.DefaultClient.Do(req); err != nil || resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("GET: %v %v, want 405 (no stream is served)", resp, err)
	} else {
		resp.Body.Close()
	}
	if r := rt.post(t, "/nope/mcp", `{"jsonrpc":"2.0","id":6,"method":"ping"}`, auth...); r.Status != http.StatusNotFound {
		t.Errorf("an unserved path: HTTP %d, want 404", r.Status)
	}
}

// TestEveryRequestLogged: a request refused before dispatch (an unserved
// path, a wrong method, a foreign Origin, a bad body, a refused era) still
// gets its one log line, with its status and why, and never the bearer.
func TestEveryRequestLogged(t *testing.T) {
	rt, _ := gmailRuntime(t)
	auth := []string{"Authorization", "Bearer " + testToken, "MCP-Protocol-Version", "2025-11-25"}
	list := `{"jsonrpc":"2.0","id":1,"method":"tools/list"}`
	rt.post(t, "/nope/mcp", list, auth...)
	rt.post(t, "/gmail/mcp", list, append(auth, "Origin", "https://evil.example")...)
	rt.post(t, "/gmail/mcp", list, append(auth, "Content-Type", "text/plain")...)
	rt.post(t, "/gmail/mcp", `[`+list+`]`, auth...)
	rt.post(t, "/gmail/mcp", `{"jsonrpc":"1.0","id":1}`, auth...)
	rt.post(t, "/gmail/mcp", list, "Authorization", "Bearer "+testToken, "MCP-Protocol-Version", "2024-11-05")
	req, _ := http.NewRequest(http.MethodDelete, rt.URL+"/gmail/mcp", nil)
	req.Header.Set("Authorization", "Bearer "+testToken)
	if resp, err := http.DefaultClient.Do(req); err == nil {
		resp.Body.Close()
	}
	logs := rt.logs.String()
	for outcome, want := range map[string]int{"no_app": 1, "origin_refused": 1, "bad_request": 3, "era_refused": 1,
		"method_not_allowed": 1} {
		if got := strings.Count(logs, `"outcome":"`+outcome+`"`); got != want {
			t.Errorf("%d log lines with outcome %s, want %d:\n%s", got, outcome, want, logs)
		}
	}
	if n := strings.Count(logs, `"msg":"connectors request"`); n != 7 {
		t.Errorf("%d request lines for 7 requests", n)
	}
	if strings.Contains(logs, testToken) {
		t.Error("the log holds the bearer")
	}
}
