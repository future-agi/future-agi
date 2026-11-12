package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestWriteMCPAdminUnknownVirtualServerCopy(t *testing.T) {
	w := httptest.NewRecorder()
	writeMCPAdmin(w, nil, mcp.ErrUnknownVirtualServer)
	if w.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", w.Code)
	}
	var body models.ErrorResponse
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body.Error.Code != "virtual_server_not_found" || body.Error.Message != "virtual server not found" {
		t.Fatalf("unexpected virtual-server error: %#v", body.Error)
	}
}

// adminUpstream is a fake MCP upstream reached through a fake client
// factory, keyed by URL.
type adminUpstream struct {
	tools []string
	mu    sync.Mutex
	calls []string
}

type adminClient struct{ up *adminUpstream }

func (c adminClient) ListTools(context.Context) ([]json.RawMessage, error) {
	out := []json.RawMessage{}
	for _, name := range c.up.tools {
		out = append(out, json.RawMessage(fmt.Sprintf(`{"name":%q,"inputSchema":{"type":"object"}}`, name)))
	}
	return out, nil
}
func (c adminClient) CallTool(_ context.Context, name string, _ json.RawMessage, _ mcp.CallOptions) (json.RawMessage, error) {
	c.up.mu.Lock()
	c.up.calls = append(c.up.calls, name)
	c.up.mu.Unlock()
	return json.RawMessage(`{"content":[{"type":"text","text":"ok"}]}`), nil
}
func (c adminClient) ListResources(context.Context) ([]json.RawMessage, error) {
	return []json.RawMessage{json.RawMessage(`{"uri":"file:///readme","name":"readme"}`)}, nil
}
func (c adminClient) ReadResource(context.Context, string, mcp.CallOptions) (json.RawMessage, error) {
	return nil, errors.New("none")
}
func (c adminClient) ListPrompts(context.Context) ([]json.RawMessage, error) {
	return []json.RawMessage{json.RawMessage(`{"name":"triage"}`)}, nil
}
func (c adminClient) GetPrompt(context.Context, string, json.RawMessage, mcp.CallOptions) (json.RawMessage, error) {
	return nil, errors.New("none")
}
func (c adminClient) Healthy() bool { return true }
func (c adminClient) Close() error  { return nil }

// newTenancyServer is a gateway with MCP on, a tenant store, a tenancy as
// the wiring installs it, and a fake client factory over upstreams.
func newTenancyServer(t *testing.T, upstreams map[string]*adminUpstream) *Server {
	t.Helper()
	t.Setenv("AGENTCC_MCP_STATE_KEYS", mcpTestStateKey)
	cfg := config.DefaultConfig()
	cfg.Admin.Token = "mcp-admin"
	cfg.MCP.Enabled = true
	cfg.MCP.VirtualServersEnabled = true
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenant.NewStore(), nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	m := srv.MCP()
	m.SetDeps(mcp.Deps{ClientFactory: func(cc mcp.ClientConfig) (mcp.UpstreamClient, error) {
		if up := upstreams[cc.URL]; up != nil {
			return adminClient{up: up}, nil
		}
		return nil, fmt.Errorf("connect %s: connection refused", cc.URL)
	}})
	mgr := tenancy.New(tenancy.Options{Store: srv.TenantStore, MCP: cfg.MCP, Deps: m.Deps,
		Adopt: m.StaticClients, Publish: m.SetStaticClient, RetryInitial: 10 * time.Millisecond})
	m.SetTenancy(mgr)
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = mgr.Close(ctx)
		m.Close()
	})
	return srv
}

// The tenancy/approval scenarios exercise named virtual servers explicitly;
// keep their fixture opted in without changing production defaults.
func TestTenancyFixtureMountsNamedVirtualRoutes(t *testing.T) {
	srv := newTenancyServer(t, nil)
	for _, method := range []string{"POST", "GET", "DELETE"} {
		w := serve(srv, method, "/mcp/v/vs_fixture", "", "")
		if w.Code == http.StatusNotFound {
			t.Fatalf("%s named route is not mounted", method)
		}
	}
}

// putOrg pushes an org config through PUT /-/orgs/{org_id}/config.
func putOrg(t *testing.T, srv *Server, org, body string) {
	t.Helper()
	if w := serve(srv, "PUT", "/-/orgs/"+org+"/config", body, "Bearer mcp-admin"); w.Code != http.StatusOK {
		t.Fatalf("PUT %s config = %d %s", org, w.Code, w.Body.String())
	}
}

func orgWithGithub(url, extra string) string {
	return `{"mcp":{"enabled":true,"servers":{"github":{"url":"` + url + `"` + extra + `}}}}`
}

// Every /-/mcp/* admin call names its org and is answered from that
// org's pool only; no org_id is 400 and an unknown one 404.
func TestAdminEndpointsRequireOrg(t *testing.T) {
	a := &adminUpstream{tools: []string{"search"}}
	b := &adminUpstream{tools: []string{"delete_repo"}}
	srv := newTenancyServer(t, map[string]*adminUpstream{"http://a.example": a, "http://b.example": b})
	putOrg(t, srv, "org-a", orgWithGithub("http://a.example", ""))
	putOrg(t, srv, "org-b", orgWithGithub("http://b.example", ""))

	gets := []string{"/-/mcp/status", "/-/mcp/tools", "/-/mcp/served-tools", "/-/mcp/resources", "/-/mcp/prompts"}
	for _, path := range gets {
		if w := serve(srv, "GET", path, "", "Bearer mcp-admin"); w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "missing_org_id") {
			t.Errorf("GET %s without org_id = %d %s, want 400 missing_org_id", path, w.Code, w.Body.String())
		}
		if w := serve(srv, "GET", path+"?org_id=org-zzz", "", "Bearer mcp-admin"); w.Code != http.StatusNotFound {
			t.Errorf("GET %s for an unknown org = %d, want 404", path, w.Code)
		}
		if w := serve(srv, "GET", path+"?org_id=org-a", "", ""); w.Code != http.StatusUnauthorized {
			t.Errorf("GET %s without the admin token = %d, want 401", path, w.Code)
		}
	}
	if w := serve(srv, "POST", "/-/mcp/test", `{"name":"github_search"}`, "Bearer mcp-admin"); w.Code != http.StatusBadRequest {
		t.Errorf("POST /-/mcp/test without org_id = %d", w.Code)
	}
	if w := serve(srv, "POST", "/-/mcp/test", `{"org_id":"org-zzz","name":"github_search"}`, "Bearer mcp-admin"); w.Code != http.StatusNotFound {
		t.Errorf("POST /-/mcp/test for an unknown org = %d", w.Code)
	}

	var status struct {
		Enabled, OrgEnabled bool `json:"-"`
		Servers             []struct {
			ServerID  string `json:"server_id"`
			Healthy   bool   `json:"healthy"`
			ToolCount int    `json:"tool_count"`
		} `json:"servers"`
		Tools int `json:"tools"`
	}
	w := serve(srv, "GET", "/-/mcp/status?org_id=org-a", "", "Bearer mcp-admin")
	if err := json.Unmarshal(w.Body.Bytes(), &status); err != nil || w.Code != 200 {
		t.Fatalf("status = %d %s", w.Code, w.Body.String())
	}
	if len(status.Servers) != 1 || status.Servers[0].ServerID != "github" || !status.Servers[0].Healthy || status.Tools != 1 {
		t.Fatalf("org-a status = %s", w.Body.String())
	}
	for _, field := range []string{`"enabled":true`, `"org_enabled":true`, `"sessions":`, `"resources":1`, `"prompts":1`} {
		if !strings.Contains(w.Body.String(), field) {
			t.Errorf("status lacks %s: %s", field, w.Body.String())
		}
	}

	names := func(org string) []string {
		var body struct {
			Tools []struct {
				Name     string `json:"name"`
				ServerID string `json:"server_id"`
			} `json:"tools"`
		}
		w := serve(srv, "GET", "/-/mcp/tools?org_id="+org, "", "Bearer mcp-admin")
		if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
			t.Fatalf("tools: %s", w.Body.String())
		}
		var out []string
		for _, tool := range body.Tools {
			out = append(out, tool.ServerID+":"+tool.Name)
		}
		return out
	}
	if got := names("org-a"); !slices.Equal(got, []string{"github:github__search"}) {
		t.Fatalf("org-a tools = %v", got)
	}
	if got := names("org-b"); !slices.Equal(got, []string{"github:github__delete_repo"}) {
		t.Fatalf("org-b tools = %v", got)
	}
	for _, path := range []string{"/-/mcp/resources", "/-/mcp/prompts"} {
		w := serve(srv, "GET", path+"?org_id=org-b", "", "Bearer mcp-admin")
		if !strings.Contains(w.Body.String(), `"server_id":"github"`) {
			t.Errorf("GET %s items carry no server_id: %s", path, w.Body.String())
		}
	}

	// test answers from the org's pool: org-b's tool is not found in org-a.
	w = serve(srv, "POST", "/-/mcp/test", `{"org_id":"org-a","name":"github__delete_repo","arguments":{}}`, "Bearer mcp-admin")
	if w.Code != 200 || !strings.Contains(w.Body.String(), "tool not found: github__delete_repo") {
		t.Fatalf("org-b's tool from org-a = %d %s", w.Code, w.Body.String())
	}
	b.mu.Lock()
	calls := len(b.calls)
	b.mu.Unlock()
	if calls != 0 {
		t.Fatal("org-a's test reached org-b's upstream")
	}
}

// No /-/mcp/* answer carries a server's auth.token or pushed secret.
func TestAdminAnswersNeverCarryAuthToken(t *testing.T) {
	const token, secret = "tok-SECRET-0123456789abcdef", "sec-SECRET-0123456789abcdef"
	a := &adminUpstream{tools: []string{"search"}}
	srv := newTenancyServer(t, map[string]*adminUpstream{"http://a.example": a, "http://c.example": a})
	putOrg(t, srv, "org-a", `{"mcp":{"enabled":true,"servers":{
		"github":{"url":"http://a.example","auth":{"type":"bearer","token":"`+token+`"}},
		"cred":{"url":"http://c.example","credential":{"mode":"shared","secret":{"type":"bearer","value":"`+secret+`"}}}}}}`)
	for _, call := range [][3]string{
		{"GET", "/-/mcp/status?org_id=org-a", ""},
		{"GET", "/-/mcp/tools?org_id=org-a", ""},
		{"GET", "/-/mcp/resources?org_id=org-a", ""},
		{"GET", "/-/mcp/prompts?org_id=org-a", ""},
		{"POST", "/-/mcp/test", `{"org_id":"org-a","name":"github__search","arguments":{}}`},
		{"GET", "/-/orgs/org-a/config", ""},
		{"GET", "/-/orgs/configs", ""},
	} {
		w := serve(srv, call[0], call[1], call[2], "Bearer mcp-admin")
		if w.Code != http.StatusOK {
			t.Fatalf("%s %s = %d %s", call[0], call[1], w.Code, w.Body.String())
		}
		if body := w.Body.String(); strings.Contains(body, token) || strings.Contains(body, secret) {
			t.Fatalf("%s %s carries a secret: %s", call[0], call[1], body)
		}
	}
}

// POST /-/reload rebuilds the static YAML pool: a server added to the YAML
// joins the static org's pool through the client factory, and a failed
// reload changes nothing.
// reloadAdminToken is TestReloadRebuildsStaticPool's admin token: config.Load
// refuses one under 32 characters.
const reloadAdminToken = "w3b-reload-4c1f9e2a7b6d5c3e8f0a1b2c3d4e5f6a"

func TestReloadRebuildsStaticPool(t *testing.T) {
	t.Setenv("AGENTCC_MCP_STATE_KEYS", mcpTestStateKey)
	path := t.TempDir() + "/gateway.yaml"
	write := func(servers string) {
		t.Helper()
		yaml := "admin:\n  token: " + reloadAdminToken + "\nmcp:\n  enabled: true\n  static:\n    org_id: org-ops\n" + servers
		if err := os.WriteFile(path, []byte(yaml), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	write("")
	cfg, err := config.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	srv := New(cfg, path, registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenant.NewStore(), nil)
	srv.ready.Store(true)
	ops := &adminUpstream{tools: []string{"restart"}}
	m := srv.MCP()
	m.SetDeps(mcp.Deps{ClientFactory: func(cc mcp.ClientConfig) (mcp.UpstreamClient, error) {
		if cc.URL == "http://ops.example" {
			return adminClient{up: ops}, nil
		}
		return nil, fmt.Errorf("connect %s: refused", cc.URL)
	}})
	mgr := tenancy.New(tenancy.Options{Store: srv.TenantStore, MCP: cfg.MCP, Deps: m.Deps, Adopt: m.StaticClients, Publish: m.SetStaticClient})
	m.SetTenancy(mgr)
	t.Cleanup(func() { _ = mgr.Close(context.Background()); m.Close() })

	status := func() string {
		return serve(srv, "GET", "/-/mcp/status?org_id=org-ops", "", "Bearer "+reloadAdminToken).Body.String()
	}
	if s := status(); !strings.Contains(s, `"servers":[]`) {
		t.Fatalf("before reload: %s", s)
	}
	write("  servers:\n    ops:\n      url: http://ops.example\n      transport: http\n")
	if w := serve(srv, "POST", "/-/reload", "", "Bearer "+reloadAdminToken); w.Code != http.StatusOK || !strings.Contains(w.Body.String(), `"reloaded"`) {
		t.Fatalf("reload = %d %s", w.Code, w.Body.String())
	}
	deadline := time.Now().Add(3 * time.Second)
	for !strings.Contains(status(), `"server_id":"ops","era":"auto","transport":"http","healthy":true,"tool_count":1`) {
		if time.Now().After(deadline) {
			t.Fatalf("after reload: %s", status())
		}
		time.Sleep(10 * time.Millisecond)
	}
	// A reload that fails leaves the pool as it was.
	if err := os.WriteFile(path, []byte("mcp: [not a map"), 0o600); err != nil {
		t.Fatal(err)
	}
	if w := serve(srv, "POST", "/-/reload", "", "Bearer "+reloadAdminToken); w.Code == http.StatusOK {
		t.Fatalf("a bad config reloaded: %s", w.Body.String())
	}
	if s := status(); !strings.Contains(s, `"server_id":"ops"`) {
		t.Fatalf("a failed reload changed the pool: %s", s)
	}
	if w := serve(srv, "POST", "/-/reload", "", ""); w.Code != http.StatusUnauthorized {
		t.Fatalf("reload without the admin token = %d", w.Code)
	}
}

// POST /-/reload binds the operator's servers to the org mcp.static.org_id
// names, and unbinds them, without a restart, for a caller with no key too.
// With request auth off, a keyless /mcp request is served the operator's
// servers only while they are bound to no org;
// after a reload that sets the org id it is 401, as after a restart with that
// config, and after a reload that clears it, it is served again. The MCP
// registry and the tenancy read the same org id after each reload.
func TestReloadMovesKeylessMCPWithTheStaticOrg(t *testing.T) {
	t.Setenv("AGENTCC_MCP_STATE_KEYS", mcpTestStateKey)
	path := t.TempDir() + "/gateway.yaml"
	write := func(staticOrg string) {
		t.Helper()
		yaml := "admin:\n  token: " + reloadAdminToken + "\nauth:\n  enabled: false\nmcp:\n  enabled: true\n"
		if staticOrg != "" {
			yaml += "  static:\n    org_id: " + staticOrg + "\n"
		}
		if err := os.WriteFile(path, []byte(yaml), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	write("")
	cfg, err := config.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	srv := New(cfg, path, registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenant.NewStore(), nil)
	srv.ready.Store(true)
	m := srv.MCP()
	mgr := tenancy.New(tenancy.Options{Store: srv.TenantStore, MCP: cfg.MCP, Deps: m.Deps, Adopt: m.StaticClients, Publish: m.SetStaticClient})
	m.SetTenancy(mgr)
	t.Cleanup(func() { _ = mgr.Close(context.Background()); m.Close() })
	// As the gateway's wiring installs them (cmd/agentcc initMCPIdentity and
	// initMCPVServer): the key store, request auth off, the static org id.
	m.SetIdentity(mcp.Identity{Keys: auth.NewKeyStore(cfg.Auth), KeylessStatic: !cfg.Auth.Enabled})
	m.Registry().SetStaticOrg(cfg.MCP.Static.OrgID)

	keyless := func() (int, string) {
		t.Helper()
		init := `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}`
		w := serve(srv, "POST", "/mcp", init, "")
		var msg struct {
			Error *struct {
				Data struct {
					Code string `json:"code"`
				} `json:"data"`
			} `json:"error"`
		}
		_ = json.Unmarshal(w.Body.Bytes(), &msg)
		if msg.Error == nil {
			return w.Code, ""
		}
		return w.Code, msg.Error.Data.Code
	}
	reload := func(staticOrg string) {
		t.Helper()
		write(staticOrg)
		if w := serve(srv, "POST", "/-/reload", "", "Bearer "+reloadAdminToken); w.Code != http.StatusOK || !strings.Contains(w.Body.String(), `"reloaded"`) {
			t.Fatalf("reload = %d %s", w.Code, w.Body.String())
		}
	}
	sameStaticOrg := func(staticOrg string) {
		t.Helper()
		if reg, ten := m.Registry().StaticOrg(), mgr.StaticOrgID(); reg != staticOrg || ten != staticOrg {
			t.Fatalf("after a reload to static org %q: the registry has %q, the tenancy %q", staticOrg, reg, ten)
		}
	}

	if code, reason := keyless(); code != http.StatusOK {
		t.Fatalf("keyless with no static org: %d %q, want 200 as on the release line", code, reason)
	}
	reload("org-ops")
	if code, reason := keyless(); code != http.StatusUnauthorized || reason != mcp.AuthMissingCredentials {
		t.Fatalf("keyless after a reload bound the operator's servers to org-ops: %d %q, want 401 missing_credentials", code, reason)
	}
	sameStaticOrg("org-ops")
	reload("")
	if code, reason := keyless(); code != http.StatusOK {
		t.Fatalf("keyless after a reload cleared the static org: %d %q, want 200", code, reason)
	}
	sameStaticOrg("")
}
