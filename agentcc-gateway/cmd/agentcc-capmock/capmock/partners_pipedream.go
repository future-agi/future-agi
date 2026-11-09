package capmock

import (
	"encoding/json"
	"net/http"
	"strings"
)

// Synthetic projections of the public partner docs, never live captures.
// __connect is a fixture-only consent action, not a claimed vendor API.
type partnerAccount struct {
	User, App, Project string
	Revoked            bool
}
type partnerState struct {
	bearer   map[string]bool
	accounts map[string]partnerAccount
	sessions map[string]partnerSession
}

func newPartnerState() *partnerState {
	return &partnerState{bearer: map[string]bool{}, accounts: map[string]partnerAccount{}, sessions: map[string]partnerSession{}}
}
func pipedreamPartnerFake() *fake {
	return &fake{name: "pipedream", routes: []route{
		{"POST /pipedream/mcp", pipedreamMCP}, // retained V4 fixture
		{"POST /pipedream/v3", pipedreamAuthorizedMCP},
		{"POST /pipedream/v1/oauth/token", pipedreamToken},
		{"POST /pipedream/v1/connect/{project}/tokens", pipedreamConnectToken},
		{"GET /pipedream/v1/connect/{project}/accounts", pipedreamAccounts},
		{"DELETE /pipedream/v1/connect/{project}/accounts/{account}", pipedreamAccountDelete},
		{"DELETE /pipedream/v1/connect/{project}/users/{user}", pipedreamUserDelete},
		{"POST /pipedream/__connect", pipedreamFixtureConnect},
	}}
}
func pipedreamToken(m *Mock, c *call) {
	body := c.json()
	if body["client_id"] == nil || body["client_secret"] == nil {
		writeJSON(c.w, 400, map[string]string{"error": "invalid_client"})
		return
	}
	bearer := "fixture-bearer-" + c.id
	m.mu.Lock()
	m.partners.bearer[bearer] = true
	m.mu.Unlock()
	m.respond(c, 200, map[string]any{"access_token": bearer, "token_type": "Bearer", "expires_in": 3600})
}
func pipedreamAuthorized(m *Mock, c *call) bool {
	bearer, hasScheme := strings.CutPrefix(c.r.Header.Get("Authorization"), "Bearer ")
	m.mu.Lock()
	valid := hasScheme && m.partners.bearer[bearer]
	m.mu.Unlock()
	if !valid {
		writeJSON(c.w, 401, map[string]string{"error": "invalid_token"})
	}
	return valid
}
func pipedreamConnectToken(m *Mock, c *call) {
	if !pipedreamAuthorized(m, c) {
		return
	}
	if c.json()["external_user_id"] == nil {
		writeJSON(c.w, 400, map[string]string{"error": "missing_user"})
		return
	}
	m.respond(c, 200, map[string]any{"token": "ctok_fixture_not_real", "expires_at": "2099-01-01T00:00:00Z", "connect_link_url": "https://pipedream.com/connect.html?token=ctok_fixture_not_real"})
}
func pipedreamFixtureConnect(m *Mock, c *call) {
	body := c.json()
	user, _ := body["external_user_id"].(string)
	app, _ := body["app"].(string)
	project, _ := body["project_id"].(string)
	if user == "" || app == "" || project == "" {
		writeJSON(c.w, 400, map[string]string{"error": "missing_fixture_binding"})
		return
	}
	id := "apn_fixture_" + c.id
	m.mu.Lock()
	m.partners.accounts[id] = partnerAccount{User: user, App: app, Project: project}
	m.mu.Unlock()
	m.respond(c, 201, map[string]string{"id": id})
}
func pipedreamAccounts(m *Mock, c *call) {
	if !pipedreamAuthorized(m, c) {
		return
	}
	rows := []any{}
	m.mu.Lock()
	for id, a := range m.partners.accounts {
		if a.Project == c.r.PathValue("project") && a.User == c.r.URL.Query().Get("external_user_id") && a.App == c.r.URL.Query().Get("app") {
			rows = append(rows, map[string]any{"id": id, "external_id": a.User, "healthy": !a.Revoked, "dead": a.Revoked, "app": map[string]string{"name_slug": a.App}})
		}
	}
	m.mu.Unlock()
	m.respond(c, 200, map[string]any{"data": rows, "page_info": map[string]any{"count": len(rows), "total_count": len(rows)}})
}
func pipedreamAccountDelete(m *Mock, c *call) {
	if !pipedreamAuthorized(m, c) {
		return
	}
	m.mu.Lock()
	id := c.r.PathValue("account")
	a, ok := m.partners.accounts[id]
	if ok && a.Project == c.r.PathValue("project") {
		delete(m.partners.accounts, id)
	}
	m.mu.Unlock()
	c.w.WriteHeader(http.StatusNoContent)
}
func pipedreamUserDelete(m *Mock, c *call) {
	if !pipedreamAuthorized(m, c) {
		return
	}
	m.mu.Lock()
	for id, a := range m.partners.accounts {
		if a.Project == c.r.PathValue("project") && a.User == c.r.PathValue("user") {
			delete(m.partners.accounts, id)
		}
	}
	m.mu.Unlock()
	c.w.WriteHeader(http.StatusNoContent)
}
func pipedreamAuthorizedMCP(m *Mock, c *call) {
	if !pipedreamAuthorized(m, c) {
		return
	}
	connected := false
	m.mu.Lock()
	for _, a := range m.partners.accounts {
		if !a.Revoked && a.User == c.r.Header.Get("x-pd-external-user-id") && a.App == c.r.Header.Get("x-pd-app-slug") && a.Project == c.r.Header.Get("x-pd-project-id") {
			connected = true
			break
		}
	}
	m.mu.Unlock()
	var req struct {
		ID     any    `json:"id"`
		Method string `json:"method"`
	}
	_ = json.Unmarshal(c.body, &req)
	if req.Method == "tools/call" && !connected {
		m.respond(c, 200, map[string]any{"jsonrpc": "2.0", "id": req.ID, "result": map[string]any{"isError": true, "content": []any{map[string]string{"type": "text", "text": "Connect your account: https://pipedream.com/connect.html?token=ctok_fixture_not_real"}}}})
		return
	}
	pipedreamMCP(m, c)
}
