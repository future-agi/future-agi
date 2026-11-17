package capmock

import (
	"encoding/json"
	"net/http"
)

type partnerSession struct {
	User, Project, Toolkit, Account string
	Tools                           []string
}

func composioPartnerFake() *fake {
	return &fake{name: "composio", routes: []route{
		{"POST /composio/api/v3.1/tool_router/session", composioCreate},
		{"DELETE /composio/api/v3.1/tool_router/session/{session}", composioDelete},
		{"PATCH /composio/api/v3.1/tool_router/session/{session}", composioPin},
		{"GET /composio/api/v3.1/tool_router/session/{session}/tools", composioTools},
		{"GET /composio/api/v3.1/tool_router/session/{session}/toolkits", composioToolkits},
		{"POST /composio/api/v3.1/tool_router/session/{session}/link", composioLink},
		{"POST /composio/api/v3.1/connected_accounts/{account}/revoke", composioRevoke},
		{"DELETE /composio/api/v3.1/connected_accounts/{account}", composioAccountDelete},
		{"POST /composio/mcp/{session}", composioMCP},
		{"POST /composio/__connect/{session}", composioFixtureConnect},
	}}
}
func composioProject(c *call) string { return c.r.Header.Get("x-api-key") }
func composioCreate(m *Mock, c *call) {
	project := composioProject(c)
	user, _ := c.json()["user_id"].(string)
	if project == "" || user == "" {
		writeJSON(c.w, 401, map[string]string{"error": "missing_identity"})
		return
	}
	session := partnerSession{Project: project, User: user, Toolkit: "gmail", Tools: []string{"GMAIL_FETCH_EMAILS"}}
	if tools, ok := c.json()["tools"].(map[string]any); ok {
		session.Tools = nil
		for kit, value := range tools {
			session.Toolkit = kit
			if config, ok := value.(map[string]any); ok {
				if enabled, ok := config["enable"].([]any); ok {
					for _, name := range enabled {
						if slug, ok := name.(string); ok {
							session.Tools = append(session.Tools, slug)
						}
					}
				}
			}
		}
	}
	id := "trs_fixture_" + c.id
	m.mu.Lock()
	m.partners.sessions[id] = session
	m.mu.Unlock()
	m.respond(c, 201, map[string]any{"session_id": id, "mcp": map[string]string{"type": "http", "url": m.baseURL() + "/composio/mcp/" + id}})
}
func composioSession(m *Mock, c *call) (partnerSession, bool) {
	m.mu.Lock()
	session, ok := m.partners.sessions[c.r.PathValue("session")]
	m.mu.Unlock()
	if !ok || session.Project != composioProject(c) {
		writeJSON(c.w, 404, map[string]string{"error": "session_not_found"})
		return partnerSession{}, false
	}
	return session, true
}
func composioDelete(m *Mock, c *call) {
	if _, ok := composioSession(m, c); !ok {
		return
	}
	id := c.r.PathValue("session")
	m.mu.Lock()
	delete(m.partners.sessions, id)
	m.mu.Unlock()
	m.respond(c, 200, map[string]any{"session_id": id, "deleted": true})
}
func composioTools(m *Mock, c *call) {
	session, ok := composioSession(m, c)
	if !ok {
		return
	}
	items := []any{}
	for _, name := range session.Tools {
		items = append(items, map[string]any{"slug": name, "description": "Synthetic direct tool", "input_parameters": map[string]string{"type": "object"}})
	}
	m.respond(c, 200, map[string]any{"items": items, "next_cursor": nil})
}
func composioToolkits(m *Mock, c *call) {
	session, ok := composioSession(m, c)
	if !ok {
		return
	}
	items := []any{}
	var account any
	m.mu.Lock()
	a, exists := m.partners.accounts[session.Account]
	m.mu.Unlock()
	if exists && !a.Revoked {
		account = map[string]string{"id": session.Account, "status": "ACTIVE"}
	}
	if account != nil || c.r.URL.Query().Get("is_connected") != "true" {
		items = append(items, map[string]any{"slug": session.Toolkit, "enabled": true, "is_no_auth": false, "connected_account": account})
	}
	m.respond(c, 200, map[string]any{"items": items, "next_cursor": nil})
}
func composioLink(m *Mock, c *call) {
	if _, ok := composioSession(m, c); !ok {
		return
	}
	m.respond(c, 201, map[string]string{"redirect_url": "https://connect.composio.dev/link/fixture-not-real", "connected_account_id": "ca_fixture_pending", "link_token": "fixture-link-not-real"})
}
func composioFixtureConnect(m *Mock, c *call) {
	session, ok := composioSession(m, c)
	if !ok {
		return
	}
	id := "ca_fixture_" + c.id
	session.Account = id
	m.mu.Lock()
	m.partners.sessions[c.r.PathValue("session")] = session
	m.partners.accounts[id] = partnerAccount{User: session.User, App: session.Toolkit, Project: session.Project}
	m.mu.Unlock()
	m.respond(c, 201, map[string]string{"id": id})
}
func composioPin(m *Mock, c *call) {
	session, ok := composioSession(m, c)
	if !ok {
		return
	}
	config, ok := c.json()["connected_accounts"].(map[string]any)
	if !ok {
		writeJSON(c.w, 400, map[string]string{"error": "invalid_binding"})
		return
	}
	refs, ok := config[session.Toolkit].([]any)
	if !ok || len(refs) != 1 {
		writeJSON(c.w, 400, map[string]string{"error": "invalid_binding"})
		return
	}
	id, _ := refs[0].(string)
	m.mu.Lock()
	account, exists := m.partners.accounts[id]
	valid := exists && !account.Revoked && account.User == session.User && account.Project == session.Project && account.App == session.Toolkit
	if valid {
		session.Account = id
		m.partners.sessions[c.r.PathValue("session")] = session
	}
	m.mu.Unlock()
	if !valid {
		writeJSON(c.w, 400, map[string]string{"error": "invalid_binding"})
		return
	}
	m.respond(c, 200, map[string]bool{"updated": true})
}
func composioRevoke(m *Mock, c *call) {
	id := c.r.PathValue("account")
	m.mu.Lock()
	a, ok := m.partners.accounts[id]
	valid := ok && a.Project == composioProject(c)
	if valid {
		a.Revoked = true
		m.partners.accounts[id] = a
	}
	m.mu.Unlock()
	if !valid {
		writeJSON(c.w, 404, map[string]string{"error": "not_found"})
		return
	}
	m.respond(c, 200, map[string]any{"revoked_tokens": []string{"credential"}, "connected_account": map[string]string{"id": id, "status": "REVOKED"}})
}
func composioAccountDelete(m *Mock, c *call) {
	id := c.r.PathValue("account")
	m.mu.Lock()
	a, ok := m.partners.accounts[id]
	if ok && a.Project == composioProject(c) {
		delete(m.partners.accounts, id)
	}
	m.mu.Unlock()
	c.w.WriteHeader(http.StatusNoContent)
}
func composioMCP(m *Mock, c *call) {
	session, ok := composioSession(m, c)
	if !ok {
		return
	}
	var req struct {
		ID     any    `json:"id"`
		Method string `json:"method"`
	}
	if json.Unmarshal(c.body, &req) != nil {
		writeJSON(c.w, 400, rpcError(nil, -32700, "parse error"))
		return
	}
	result := map[string]any{}
	switch req.Method {
	case "initialize":
		result = map[string]any{"protocolVersion": "2025-06-18", "capabilities": map[string]any{"tools": map[string]any{}}, "serverInfo": map[string]string{"name": "composio-capmock", "version": "1.0.0"}}
	case "tools/list":
		tools := []any{}
		for _, name := range session.Tools {
			tools = append(tools, map[string]any{"name": name, "inputSchema": map[string]string{"type": "object"}})
		}
		result["tools"] = tools
	case "tools/call":
		m.mu.Lock()
		account, exists := m.partners.accounts[session.Account]
		m.mu.Unlock()
		result["isError"] = !exists || account.Revoked
		result["content"] = []any{map[string]string{"type": "text", "text": "synthetic call for " + session.User}}
	case "notifications/initialized":
		c.w.WriteHeader(202)
		return
	default:
		m.respond(c, 200, rpcError(req.ID, -32601, "method not found"))
		return
	}
	m.respond(c, 200, map[string]any{"jsonrpc": "2.0", "id": req.ID, "result": result})
}
