package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

// Test-account data a vendor-shaped server returns, so a recording has
// tokens, emails, ids and template-looking text to deal with.
const (
	vendorThread    = "18c2f0a1b2c3d4e5"
	vendorUser      = 12345678
	vendorWorkspace = "550e8400-e29b-41d4-a716-446655440000"
	vendorAlice     = "alice@corp-test.dev"
	vendorBob       = "bob.smith@corp-test.dev"
	vendorText      = "subject: {{.Secret}} %s ${HOME} $(id) <b>bold</b> & co"
)

// vendorMCP is a small legacy (2025-11-25) Streamable HTTP MCP server that
// wants a bearer token and a session, and answers one call as an SSE stream.
type vendorMCP struct {
	token string
	hits  atomic.Int64
}

func newVendorMCP(t *testing.T, token string) (*vendorMCP, *httptest.Server) {
	t.Helper()
	v := &vendorMCP{token: token}
	srv := httptest.NewServer(v)
	t.Cleanup(srv.Close)
	return v, srv
}

func (v *vendorMCP) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	v.hits.Add(1)
	if r.Header.Get("Authorization") != "Bearer "+v.token {
		w.Header().Set("WWW-Authenticate", `Bearer realm="vendor"`)
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	if r.Method == http.MethodDelete {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	var msg struct {
		ID     json.RawMessage `json:"id"`
		Method string          `json:"method"`
		Params json.RawMessage `json:"params"`
	}
	if r.Method != http.MethodPost || json.NewDecoder(r.Body).Decode(&msg) != nil {
		w.WriteHeader(http.StatusBadRequest)
		return
	}
	if msg.Method != "initialize" && r.Header.Get("Mcp-Session-Id") != "vendor-session-1" {
		writeRPC(w, msg.ID, nil, map[string]any{"code": -32600, "message": "no session"}, http.StatusBadRequest)
		return
	}
	if len(msg.ID) == 0 {
		w.WriteHeader(http.StatusAccepted)
		return
	}
	switch msg.Method {
	case "initialize":
		w.Header().Set("Mcp-Session-Id", "vendor-session-1")
		writeRPC(w, msg.ID, map[string]any{"protocolVersion": "2025-11-25", "capabilities": map[string]any{"tools": map[string]any{}},
			"serverInfo": map[string]any{"name": "vendor-mail", "version": "3.1"}, "instructions": "Mail for " + vendorAlice + ". " + vendorText}, nil, 0)
	case "tools/list":
		writeRPC(w, msg.ID, map[string]any{"tools": []map[string]any{
			{"name": "search_threads", "inputSchema": map[string]any{"type": "object", "properties": map[string]any{"query": map[string]any{"type": "string"}}}},
			{"name": "get_thread", "inputSchema": map[string]any{"type": "object", "properties": map[string]any{"thread_id": map[string]any{"type": "string"}}}},
		}}, nil, 0)
	case "tools/call":
		v.call(w, msg.ID, msg.Params, r.URL.Path)
	default:
		writeRPC(w, msg.ID, nil, map[string]any{"code": -32601, "message": "method not found: " + msg.Method}, 0)
	}
}

func (v *vendorMCP) call(w http.ResponseWriter, id, params json.RawMessage, endpoint string) {
	var p struct {
		Name      string            `json:"name"`
		Arguments map[string]string `json:"arguments"`
	}
	_ = json.Unmarshal(params, &p)
	switch {
	case p.Name == "search_threads":
		found := map[string]any{"threads": []map[string]any{{"id": vendorThread, "from": vendorAlice, "user_id": vendorUser}},
			"workspace_id": vendorWorkspace, "query": p.Arguments["query"], "endpoint": endpoint}
		text, _ := json.Marshal(found)
		writeRPC(w, id, map[string]any{"content": []map[string]any{{"type": "text", "text": string(text)}}, "structuredContent": found}, nil, 0)
	case p.Name == "get_thread" && p.Arguments["thread_id"] == vendorThread:
		// An SSE answer: a progress notification first, then the response.
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		fmt.Fprint(w, "event: message\ndata: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/progress\",\"params\":{\"progress\":1}}\n\n")
		res, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": id, "result": map[string]any{
			"content":           []map[string]any{{"type": "text", "text": "debug: authorized with " + v.token + "; " + vendorText}},
			"structuredContent": map[string]any{"id": vendorThread, "owner": vendorUser, "messages": []map[string]string{{"from": vendorBob}}}}})
		fmt.Fprintf(w, "event: message\ndata: %s\n\n", res)
	case p.Name == "get_thread":
		writeRPC(w, id, map[string]any{"isError": true, "content": []map[string]any{{"type": "text", "text": "no such thread"}}}, nil, 0)
	default:
		writeRPC(w, id, nil, map[string]any{"code": -32602, "message": "unknown tool"}, 0)
	}
}

func writeRPC(w http.ResponseWriter, id json.RawMessage, result, rpcErr any, status int) {
	msg := map[string]any{"jsonrpc": "2.0", "id": id}
	if rpcErr != nil {
		msg["error"] = rpcErr
	} else {
		msg["result"] = result
	}
	data, _ := json.Marshal(msg)
	w.Header().Set("Content-Type", "application/json")
	if status == 0 {
		status = http.StatusOK
	}
	w.WriteHeader(status)
	_, _ = w.Write(data)
}

// tokenShaped is a credential value that matches no secret shape, so only
// the scrubber, not the shape refusal, keeps it off disk.
func tokenShaped(label string) string { return label + "-Qz8" + strings.Repeat("vK3", 6) }
