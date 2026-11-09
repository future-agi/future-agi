package capmock

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"time"
)

// --- Kernel browsers: create, get, delete.

func kernelCreate(m *Mock, c *call) {
	m.newSession(c.id)
	m.render(c, http.StatusOK, "kernel_browser.json", nil)
}

func kernelGet(m *Mock, c *call) {
	s, ok := m.session(c.r.PathValue("id"))
	if !ok {
		writeJSON(c.w, http.StatusNotFound, map[string]string{"code": "not_found", "message": "browser not found"})
		return
	}
	m.render(c, http.StatusOK, "kernel_browser.json", map[string]string{"id": s.id, "now": s.created.UTC().Format(time.RFC3339)})
}

func kernelDelete(m *Mock, c *call) {
	if !m.deleteSession(c.r.PathValue("id")) {
		writeJSON(c.w, http.StatusNotFound, map[string]string{"code": "not_found", "message": "browser not found"})
		return
	}
	c.w.WriteHeader(http.StatusNoContent)
}

// --- Browserbase sessions: create (RUNNING), release, get.

func browserbaseCreate(m *Mock, c *call) {
	m.browserbaseAnswer(c, m.newSession(c.id))
}

func browserbaseGet(m *Mock, c *call) {
	s, ok := m.session(c.r.PathValue("id"))
	if !ok {
		writeJSON(c.w, http.StatusNotFound, map[string]any{"statusCode": 404, "error": "Not Found", "message": "Session not found"})
		return
	}
	m.browserbaseAnswer(c, s)
}

func browserbaseUpdate(m *Mock, c *call) {
	if status, _ := c.json()["status"].(string); status != "REQUEST_RELEASE" {
		writeJSON(c.w, http.StatusBadRequest, map[string]any{"statusCode": 400, "error": "Bad Request", "message": "status must be REQUEST_RELEASE"})
		return
	}
	id := c.r.PathValue("id")
	if !m.endSession(id) {
		writeJSON(c.w, http.StatusNotFound, map[string]any{"statusCode": 404, "error": "Not Found", "message": "Session not found"})
		return
	}
	s, _ := m.session(id)
	m.browserbaseAnswer(c, s)
}

func (m *Mock) browserbaseAnswer(c *call, s *session) {
	m.mu.Lock()
	created, ended := s.created, s.ended
	m.mu.Unlock()
	status := "RUNNING"
	if !ended.IsZero() {
		status = "COMPLETED"
	}
	doc, err := m.fill(c, "browserbase_session.json", map[string]string{"id": s.id, "status": status,
		"created": created.UTC().Format(time.RFC3339), "expires": created.Add(time.Hour).UTC().Format(time.RFC3339),
		"ended": ended.UTC().Format(time.RFC3339)})
	if err != nil {
		writeJSON(c.w, 500, vendorError(500, err.Error()))
		return
	}
	if ended.IsZero() {
		doc["endedAt"] = nil
	}
	code := http.StatusOK
	if c.r.URL.Path == "/browserbase/v1/sessions" {
		code = http.StatusCreated
	}
	m.respond(c, code, doc)
}

// --- Pipedream Connect's remote MCP server: stateless JSON-RPC, keyed on
// the end user and app headers it requires.

var pipedreamHeaders = []string{"x-pd-project-id", "x-pd-environment", "x-pd-external-user-id", "x-pd-app-slug"}

func pipedreamMCP(m *Mock, c *call) {
	for _, h := range pipedreamHeaders {
		if c.r.Header.Get(h) == "" {
			writeJSON(c.w, http.StatusBadRequest, map[string]string{"error": "missing header " + h})
			return
		}
	}
	var req struct {
		ID     any             `json:"id"`
		Method string          `json:"method"`
		Params json.RawMessage `json:"params"`
	}
	if json.Unmarshal(c.body, &req) != nil {
		writeJSON(c.w, http.StatusBadRequest, rpcError(nil, -32700, "parse error"))
		return
	}
	app, user := c.r.Header.Get("x-pd-app-slug"), c.r.Header.Get("x-pd-external-user-id")
	tool := app + "-send-message"
	var result any
	switch req.Method {
	case "initialize":
		result = map[string]any{"protocolVersion": "2025-06-18", "capabilities": map[string]any{"tools": map[string]any{}},
			"serverInfo": map[string]string{"name": "pipedream-capmock", "version": "1.0.0"}}
	case "notifications/initialized":
		c.w.WriteHeader(http.StatusAccepted)
		return
	case "tools/list":
		result = map[string]any{"tools": []any{map[string]any{"name": tool, "description": "Send a message with " + app,
			"inputSchema": map[string]any{"type": "object", "properties": map[string]any{"text": map[string]string{"type": "string"}},
				"required": []string{"text"}}}}}
	case "tools/call":
		var p struct {
			Name      string         `json:"name"`
			Arguments map[string]any `json:"arguments"`
		}
		_ = json.Unmarshal(req.Params, &p)
		args, _ := json.Marshal(p.Arguments)
		text, isErr := "sent for external user "+user+" via "+app+": "+string(args), false
		if p.Name != tool {
			text, isErr = "unknown tool "+p.Name, true
		}
		result = map[string]any{"content": []any{map[string]string{"type": "text", "text": text}}, "isError": isErr}
	default:
		m.respond(c, http.StatusOK, rpcError(req.ID, -32601, "method not found: "+req.Method))
		return
	}
	m.respond(c, http.StatusOK, map[string]any{"jsonrpc": "2.0", "id": req.ID, "result": result})
}

func rpcError(id any, code int, msg string) map[string]any {
	return map[string]any{"jsonrpc": "2.0", "id": id, "error": map[string]any{"code": code, "message": msg}}
}

// --- An x402 (v2) seller: 402 with PAYMENT-REQUIRED until a request carries
// PAYMENT-SIGNATURE, then 200 with PAYMENT-RESPONSE.

func x402Seller(m *Mock, c *call) {
	sig := c.r.Header.Get("PAYMENT-SIGNATURE")
	if sig == "" {
		required := map[string]any{"x402Version": 2, "error": "PAYMENT-SIGNATURE header is required",
			"resource": map[string]string{"url": m.baseURL() + "/x402/weather", "description": "Capmock weather", "mimeType": "application/json"},
			"accepts": []any{map[string]any{"scheme": "exact", "network": "eip155:84532", "amount": "1000",
				"asset": "0x00000000000000000000000000000000000000a5", "payTo": "0x0000000000000000000000000000000000c0ffee",
				"maxTimeoutSeconds": 60, "extra": map[string]string{"name": "USDC", "version": "2"}}}}
		c.w.Header().Set("PAYMENT-REQUIRED", b64JSON(required))
		m.respond(c, http.StatusPaymentRequired, required)
		return
	}
	var payload map[string]any
	raw, err := base64.StdEncoding.DecodeString(sig)
	if err != nil || json.Unmarshal(raw, &payload) != nil || payload["x402Version"] == nil {
		writeJSON(c.w, http.StatusBadRequest, map[string]string{"error": "PAYMENT-SIGNATURE is not a base64 x402 payment payload"})
		return
	}
	c.w.Header().Set("PAYMENT-RESPONSE", b64JSON(map[string]any{"success": true, "transaction": "0xcapmock" + c.id,
		"network": "eip155:84532", "payer": "0x00000000000000000000000000000000000000b0"}))
	m.respond(c, http.StatusOK, map[string]any{"weather": "sunny", "temperature_c": 21})
}

func b64JSON(v any) string {
	raw, _ := json.Marshal(v)
	return base64.StdEncoding.EncodeToString(raw)
}
