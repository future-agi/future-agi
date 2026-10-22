package mcpmock

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"
)

// TestEveryEraAnswersHandshake: each --era answers its own handshake, and
// refuses the other era's.
func TestEveryEraAnswersHandshake(t *testing.T) {
	t.Run("2026", func(t *testing.T) {
		_, srv := testMock(t, "2026", "none")
		res := resultMap(t, call2026(t, srv.URL+"/mcp", "server/discover", nil))
		if v, _ := json.Marshal(res["supportedVersions"]); string(v) != `["2026-07-28"]` {
			t.Fatalf("supportedVersions = %s", v)
		}
		if res["resultType"] != "complete" || res["serverInfo"] == nil || res["ttlMs"] == nil || res["cacheScope"] != "public" {
			t.Fatalf("discover result lacks resultType, serverInfo or caching: %v", res)
		}
		tools := resultMap(t, call2026(t, srv.URL+"/mcp", "tools/list", nil))
		if tools["resultType"] != "complete" || len(tools["tools"].([]any)) != 3 {
			t.Fatalf("tools/list = %v", tools)
		}
		// 2026 has no initialize, no GET stream and no DELETE.
		if r := rpc(t, srv.URL+"/mcp", "initialize", 1, map[string]any{"protocolVersion": V1125}); r.msg.Error == nil {
			t.Fatalf("a 2026-only mock answered initialize: %s", r.body)
		}
		for _, method := range []string{http.MethodGet, http.MethodDelete} {
			req, _ := http.NewRequest(method, srv.URL+"/mcp", nil)
			req.Header.Set("Mcp-Session-Id", "abc")
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatal(err)
			}
			resp.Body.Close()
			if resp.StatusCode != http.StatusMethodNotAllowed {
				t.Fatalf("%s on a 2026 mock = %d, want 405", method, resp.StatusCode)
			}
		}
	})
	for _, version := range []string{V1125, V0618, V0326} {
		t.Run(version, func(t *testing.T) {
			_, srv := testMock(t, version, "none")
			// The gateway asks for 2025-11-25; the mock answers its own era.
			r := rpc(t, srv.URL+"/mcp", "initialize", 1, map[string]any{
				"protocolVersion": V1125, "capabilities": map[string]any{},
				"clientInfo": map[string]any{"name": "t", "version": "1"}})
			res := resultMap(t, r)
			if res["protocolVersion"] != version || r.header.Get("Mcp-Session-Id") == "" {
				t.Fatalf("initialize = %v, session %q", res, r.header.Get("Mcp-Session-Id"))
			}
			sid := r.header.Get("Mcp-Session-Id")
			tools := resultMap(t, rpc(t, srv.URL+"/mcp", "tools/list", 2, nil, "Mcp-Session-Id: "+sid))
			if _, ok := tools["resultType"]; ok || len(tools["tools"].([]any)) != 3 {
				t.Fatalf("legacy tools/list = %v", tools)
			}
			if r := rpc(t, srv.URL+"/mcp", "tools/list", 3, nil); r.status != http.StatusBadRequest {
				t.Fatalf("a legacy request without a session = %d, want 400", r.status)
			}
			if r := call2026(t, srv.URL+"/mcp", "server/discover", nil); r.msg.Error == nil {
				t.Fatalf("a %s mock answered server/discover", version)
			}
		})
	}
	t.Run("2024-11-05", func(t *testing.T) {
		m, srv := testMock(t, V1105, "none")
		res := sseHandshake(t, srv.URL)
		if res["protocolVersion"] != V1105 {
			t.Fatalf("2024-11-05 initialize = %v", res)
		}
		if r := rpc(t, srv.URL+"/mcp", "initialize", 1, map[string]any{"protocolVersion": V1105}); r.status != http.StatusNotFound {
			t.Fatalf("a 2024-11-05 mock serves Streamable HTTP: %d", r.status)
		}
		_ = m
	})
	t.Run("dual", func(t *testing.T) {
		_, srv := testMock(t, "dual", "none")
		if res := resultMap(t, call2026(t, srv.URL+"/mcp", "server/discover", nil)); res["resultType"] != "complete" {
			t.Fatalf("dual discover = %v", res)
		}
		for _, version := range []string{V1125, V0618, V0326} {
			r := rpc(t, srv.URL+"/mcp", "initialize", 1, map[string]any{"protocolVersion": version, "capabilities": map[string]any{}})
			if res := resultMap(t, r); res["protocolVersion"] != version {
				t.Fatalf("dual initialize %s = %v", version, res)
			}
		}
		if res := sseHandshake(t, srv.URL); res["protocolVersion"] != V1105 {
			t.Fatalf("dual 2024-11-05 initialize = %v", res)
		}
	})
}

// sseHandshake runs 2024-11-05's handshake: GET /sse, read the endpoint
// event, POST initialize there (202), read the answer from the stream, then
// call a tool the same way.
func sseHandshake(t *testing.T, base string) map[string]any {
	t.Helper()
	req, _ := http.NewRequest(http.MethodGet, base+"/sse", nil)
	req.Header.Set("Accept", "text/event-stream")
	events, _, cancel := sseStream(t, req)
	defer cancel()
	ep := nextEvent(t, events, 5*time.Second)
	if ep.name != "endpoint" || !strings.HasPrefix(ep.data, "/messages?sessionId=") {
		t.Fatalf("first /sse event = %+v, want the endpoint", ep)
	}
	post := func(method string, id int, params map[string]any) {
		body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": id, "method": method, "params": params})
		resp, err := http.Post(base+ep.data, "application/json", strings.NewReader(string(body)))
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != http.StatusAccepted {
			t.Fatalf("POST %s = %d, want 202", ep.data, resp.StatusCode)
		}
	}
	post("initialize", 1, map[string]any{"protocolVersion": V1105, "capabilities": map[string]any{}})
	init := decodeMessage(t, nextEvent(t, events, 5*time.Second).data)
	res := map[string]any{}
	_ = json.Unmarshal(init.Result, &res)
	post("tools/call", 2, map[string]any{"name": "echo", "arguments": map[string]any{"text": "hi"}})
	callMsg := decodeMessage(t, nextEvent(t, events, 5*time.Second).data)
	if string(callMsg.ID) != "2" || !strings.Contains(string(callMsg.Result), "echo ok") {
		t.Fatalf("tools/call over /messages = %+v", callMsg)
	}
	return res
}

// TestMissingMetaGetsMinus32021: a 2026 request without _meta (or without its
// required keys) is -32021 with HTTP 400; a wrong version is -32022 with the
// supported list; a mirrored header that disagrees is -32020.
func TestMissingMetaGetsMinus32021(t *testing.T) {
	for _, era := range []string{"2026", "dual"} {
		_, srv := testMock(t, era, "none")
		url := srv.URL + "/mcp"
		r := rpc(t, url, "tools/list", 1, nil, headers2026("tools/list", "")...)
		if r.status != http.StatusBadRequest || r.msg.Error == nil || r.msg.Error.Code != CodeMissingClientCapability {
			t.Fatalf("%s: no _meta = HTTP %d %s, want 400 and -32021", era, r.status, r.body)
		}
		var seen struct{ Received []Received }
		getJSON(t, srv.URL+"/__received?last=1", &seen)
		if len(seen.Received) != 1 || seen.Received[0].Rejected != "meta" {
			t.Fatalf("%s: /__received = %+v, want the request marked rejected: meta", era, seen.Received)
		}
		noCaps := map[string]any{"_meta": map[string]any{metaProtocolVersion: V2026}}
		if r := rpc(t, url, "tools/list", 1, noCaps, headers2026("tools/list", "")...); r.msg.Error == nil || r.msg.Error.Code != CodeMissingClientCapability {
			t.Fatalf("%s: _meta without clientCapabilities = %s, want -32021", era, r.body)
		}
	}
	_, srv := testMock(t, "2026", "none")
	url := srv.URL + "/mcp"
	old := map[string]any{"_meta": map[string]any{metaProtocolVersion: V1125, metaClientCaps: map[string]any{}}}
	r := rpc(t, url, "tools/list", 1, old, "MCP-Protocol-Version: "+V1125, "Mcp-Method: tools/list")
	if r.msg.Error == nil || r.msg.Error.Code != CodeUnsupportedVersion || !strings.Contains(string(r.msg.Error.Data), V2026) {
		t.Fatalf("an old version in _meta = %s, want -32022 listing %s", r.body, V2026)
	}
	params := meta2026(map[string]any{"name": "echo", "arguments": map[string]any{"text": "x"}})
	for _, hdrs := range [][]string{
		{"MCP-Protocol-Version: " + V2026, "Mcp-Method: tools/list", "Mcp-Name: echo"},  // method mismatch
		{"MCP-Protocol-Version: " + V2026, "Mcp-Method: tools/call", "Mcp-Name: other"}, // name mismatch
		{"MCP-Protocol-Version: " + V0618, "Mcp-Method: tools/call", "Mcp-Name: echo"},  // version mismatch
		{"Mcp-Method: tools/call", "Mcp-Name: echo"},                                    // version header missing
	} {
		r := rpc(t, url, "tools/call", 1, params, hdrs...)
		if r.status != http.StatusBadRequest || r.msg.Error == nil || r.msg.Error.Code != CodeHeaderMismatch {
			t.Fatalf("headers %v = HTTP %d %s, want 400 and -32020", hdrs, r.status, r.body)
		}
	}
	if r := call2026(t, url, "no/such", nil); r.status != http.StatusNotFound || r.msg.Error.Code != CodeMethodNotFound {
		t.Fatalf("unknown 2026 method = HTTP %d %s, want 404 and -32601", r.status, r.body)
	}
}
