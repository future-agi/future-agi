package mcp

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestServerToolsList(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	// Register some tools directly in registry.
	s.Registry().RegisterServer("github", []Tool{
		{Name: "create_issue", Description: "Create an issue", InputSchema: json.RawMessage(`{"type":"object"}`)},
		{Name: "list_repos", Description: "List repos", InputSchema: json.RawMessage(`{"type":"object"}`)},
	})

	// First initialize to get session.
	sessionID := initializeSession(t, s)

	// List tools.
	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`2`),
		Method:  MethodToolsList,
	}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result ListToolsResult
	if err := json.Unmarshal(resp.Result, &result); err != nil {
		t.Fatal(err)
	}
	if len(result.Tools) != 2 {
		t.Fatalf("expected 2 tools, got %d", len(result.Tools))
	}
}

func TestServerToolsListNoSession(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  MethodToolsList,
	}

	w := postMCP(t, s, msg, nil)
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for missing session")
	}
}

func TestServerToolCallToolNotFound(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	sessionID := initializeSession(t, s)

	params := ToolCallParams{Name: "nonexistent_tool", Arguments: map[string]interface{}{"key": "value"}}
	paramsData, _ := json.Marshal(params)

	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`3`),
		Method:  MethodToolsCall,
		Params:  paramsData,
	}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for non-existent tool")
	}
	if resp.Error.Code != ErrCodeMethodNotFound {
		t.Fatalf("expected code %d, got %d", ErrCodeMethodNotFound, resp.Error.Code)
	}
}

func TestServerDepthExceeded(t *testing.T) {
	s := NewServer(ServerConfig{
		MaxAgentDepth:   2,
		ToolCallTimeout: 5 * time.Second,
		SessionTTL:      5 * time.Minute,
	})
	defer s.Close()

	// Register a tool so we get past the "tool not found" check.
	s.Registry().RegisterServer("test", []Tool{
		{Name: "action", InputSchema: json.RawMessage(`{}`)},
	})

	sessionID := initializeSession(t, s)

	params := ToolCallParams{Name: "test_action"}
	paramsData, _ := json.Marshal(params)

	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`4`),
		Method:  MethodToolsCall,
		Params:  paramsData,
	}

	w := postMCP(t, s, msg, map[string]string{
		"MCP-Session-Id": sessionID,
		HeaderAgentDepth: "5",
	})

	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for depth exceeded")
	}
	if resp.Error.Code != ErrCodeInvalidRequest {
		t.Fatalf("expected code %d, got %d", ErrCodeInvalidRequest, resp.Error.Code)
	}
}

func TestServerToolCallWithMockUpstream(t *testing.T) {
	// Create a mock MCP server.
	mockServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var msg Message
		json.Unmarshal(body, &msg)

		switch msg.Method {
		case MethodInitialize:
			result := InitializeResult{
				ProtocolVersion: ProtocolVersion,
				Capabilities:    ServerCapabilities{Tools: &ToolsCapability{}},
				ServerInfo:      Implementation{Name: "mock", Version: "1.0"},
			}
			resp, _ := NewResponse(msg.ID, result)
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(resp)

		case MethodToolsList:
			result := ListToolsResult{
				Tools: []Tool{
					{Name: "echo", Description: "Echo back", InputSchema: json.RawMessage(`{"type":"object"}`)},
				},
			}
			resp, _ := NewResponse(msg.ID, result)
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(resp)

		case MethodToolsCall:
			var params ToolCallParams
			json.Unmarshal(msg.Params, &params)
			result := ToolCallResult{
				Content: []ContentPart{{Type: "text", Text: "echoed: " + params.Name}},
			}
			resp, _ := NewResponse(msg.ID, result)
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(resp)

		default:
			resp, _ := NewResponse(msg.ID, struct{}{})
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(resp)
		}
	}))
	defer mockServer.Close()

	// Create gateway server.
	s := newTestServer(t)
	defer s.Close()

	// Create client to mock upstream (URL points to mock/mcp, but mock handles at root).
	// The HTTPTransport appends /mcp to the base URL, so use mock base.
	client := NewClient(ClientConfig{
		ServerID:      "mock",
		URL:           mockServer.URL, // HTTPTransport appends /mcp
		TransportType: "http",
	})

	// The mock handles all paths, so we need the transport to hit the right URL.
	// Override: create transport directly for the test.
	transport := NewHTTPTransport(mockServer.URL, AuthConfig{})
	// Monkey-patch: the base URL already has /mcp suffix from transport, but mock serves at /.
	// For the test, let's just set the base URL without /mcp suffix.
	client.transport = transport
	client.healthy.Store(true)

	// Manually discover tools.
	toolsList := []Tool{
		{Name: "echo", Description: "Echo back", InputSchema: json.RawMessage(`{"type":"object"}`)},
	}
	client.tools.Store(&toolsList)

	s.RegisterClient(client)

	// Verify tools registered.
	if s.Registry().ToolCount() != 1 {
		t.Fatalf("expected 1 tool, got %d", s.Registry().ToolCount())
	}

	// Initialize session and call tool.
	sessionID := initializeSession(t, s)

	params := ToolCallParams{Name: "mock_echo", Arguments: map[string]interface{}{"input": "hello"}}
	paramsData, _ := json.Marshal(params)

	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`10`),
		Method:  MethodToolsCall,
		Params:  paramsData,
	}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result ToolCallResult
	if err := json.Unmarshal(resp.Result, &result); err != nil {
		t.Fatal(err)
	}
	if len(result.Content) != 1 {
		t.Fatalf("expected 1 content part, got %d", len(result.Content))
	}
	if result.Content[0].Text != "echoed: echo" {
		t.Fatalf("expected 'echoed: echo', got %s", result.Content[0].Text)
	}
}

func TestToolCallDeprecationWarning(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	tools := []Tool{{Name: "old_api", Description: "Old API", InputSchema: json.RawMessage(`{}`)}}
	s.Registry().RegisterServer("mock", tools)

	// Set version info marking the tool as deprecated.
	s.Registry().SetToolVersions(map[string]ToolVersionInfo{
		"mock_old_api": {
			Version:            "1.0",
			Deprecated:         true,
			DeprecationMessage: "old_api is deprecated",
			ReplacedBy:         "mock_new_api",
		},
	})

	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)
		result := ToolCallResult{Content: []ContentPart{{Type: "text", Text: "result"}}}
		resp, _ := NewResponse(msg.ID, result)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer mockUpstream.Close()

	client := NewClient(ClientConfig{ServerID: "mock", URL: mockUpstream.URL, TransportType: "http"})
	client.transport = NewHTTPTransport(mockUpstream.URL, AuthConfig{})
	client.healthy.Store(true)
	client.tools.Store(&tools)
	s.clientsMu.Lock()
	s.clients["mock"] = client
	s.clientsMu.Unlock()

	sessionID := initializeSession(t, s)

	params := ToolCallParams{Name: "mock_old_api", Arguments: map[string]interface{}{}}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodToolsCall, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("deprecated tool should still work, got error: %s", resp.Error.Message)
	}

	var result ToolCallResult
	json.Unmarshal(resp.Result, &result)

	// Should have the original content plus a deprecation warning.
	if len(result.Content) < 2 {
		t.Fatalf("expected at least 2 content parts (result + deprecation), got %d", len(result.Content))
	}

	lastPart := result.Content[len(result.Content)-1]
	if lastPart.Type != "text" {
		t.Fatalf("expected text type for deprecation warning, got %s", lastPart.Type)
	}
	if !contains(lastPart.Text, "DEPRECATED") {
		t.Fatalf("expected deprecation warning, got: %s", lastPart.Text)
	}
	if !contains(lastPart.Text, "mock_new_api") {
		t.Fatalf("expected replacement tool in warning, got: %s", lastPart.Text)
	}
}

func TestToolCallNoDeprecationWarningForNonDeprecated(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	tools := []Tool{{Name: "active_api", Description: "Active API", InputSchema: json.RawMessage(`{}`)}}
	s.Registry().RegisterServer("mock", tools)

	// Not deprecated.
	s.Registry().SetToolVersions(map[string]ToolVersionInfo{
		"mock_active_api": {Version: "2.0", Deprecated: false},
	})

	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)
		result := ToolCallResult{Content: []ContentPart{{Type: "text", Text: "result"}}}
		resp, _ := NewResponse(msg.ID, result)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer mockUpstream.Close()

	client := NewClient(ClientConfig{ServerID: "mock", URL: mockUpstream.URL, TransportType: "http"})
	client.transport = NewHTTPTransport(mockUpstream.URL, AuthConfig{})
	client.healthy.Store(true)
	client.tools.Store(&tools)
	s.clientsMu.Lock()
	s.clients["mock"] = client
	s.clientsMu.Unlock()

	sessionID := initializeSession(t, s)

	params := ToolCallParams{Name: "mock_active_api", Arguments: map[string]interface{}{}}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodToolsCall, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result ToolCallResult
	json.Unmarshal(resp.Result, &result)

	// Should have exactly 1 content part (no deprecation warning).
	if len(result.Content) != 1 {
		t.Fatalf("expected 1 content part (no deprecation), got %d", len(result.Content))
	}
}

// TestListsSortedStable is bug 14: tools/list came out in map order, so a
// client's prompt cache missed on every turn. Twenty lists must be byte for
// byte the same, sorted by name.
func TestListsSortedStable(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	for _, srv := range []string{"zeta", "alpha", "mid"} {
		var tools []Tool
		for _, n := range []string{"q", "b", "x", "a", "m"} {
			tools = append(tools, Tool{Name: n, InputSchema: json.RawMessage(`{"type":"object"}`)})
		}
		s.Registry().RegisterServer(srv, tools)
	}
	sessionID := initializeSession(t, s)

	var first string
	for i := 0; i < 20; i++ {
		w := postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`2`), Method: MethodToolsList},
			map[string]string{"MCP-Session-Id": sessionID})
		body := w.Body.String()
		if i == 0 {
			first = body
			var resp struct {
				Result ListToolsResult `json:"result"`
			}
			if err := json.Unmarshal([]byte(body), &resp); err != nil {
				t.Fatal(err)
			}
			for j := 1; j < len(resp.Result.Tools); j++ {
				if resp.Result.Tools[j-1].Name >= resp.Result.Tools[j].Name {
					t.Fatalf("tools/list is not sorted by name: %q before %q", resp.Result.Tools[j-1].Name, resp.Result.Tools[j].Name)
				}
			}
			if len(resp.Result.Tools) != 15 {
				t.Fatalf("tools/list has %d tools, want 15", len(resp.Result.Tools))
			}
			continue
		}
		if body != first {
			t.Fatalf("tools/list #%d differs from the first:\n%s\nvs\n%s", i+1, body, first)
		}
	}
}

func TestListsSortedStableThroughAVirtualServer(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["zeta","alpha","mid"],"access":{"default_for_org":true}}}}}`)
	for _, srv := range []string{"zeta", "alpha", "mid"} {
		registerTools(f.Server, srv, "q", "b", "x", "a", "m")
	}
	first := f.post(t, "", MethodToolsList, struct{}{}, nil).Body.String()
	for i := 0; i < 20; i++ {
		if body := f.post(t, "", MethodToolsList, struct{}{}, nil).Body.String(); body != first {
			t.Fatalf("tools/list #%d differs:\n%s\nvs\n%s", i+2, body, first)
		}
	}
	names := f.toolNames(t, "", nil)
	if len(names) != 15 || names[0] != "alpha__a" || names[14] != "zeta__x" {
		t.Fatalf("names = %v", names)
	}
	for j := 1; j < len(names); j++ {
		if names[j-1] >= names[j] {
			t.Fatalf("not sorted: %q before %q", names[j-1], names[j])
		}
	}
}

const (
	vsOps   = "vs_01M4ZEB9E1A6Y8ATM7S5TJZ9YY"
	vsOther = "vs_01M4ZEB9E2QWERTYASDFGHJKZX"
	twoVS   = `{"mcp":{"access":{"default":"allow"},"servers":{"git_hub":{"url":"https://a.example/mcp"},"git":{"url":"https://b.example/mcp"}},
		"virtual_servers":{"eng":{"id":"` + vsEng + `","members":["git_hub","git"],"access":{"keys":["k1"]}},
		"ops":{"id":"` + vsOps + `","members":["git"]}}}}`
)

func TestVirtualServerRouting(t *testing.T) {
	f := newVSFixture(t, twoVS)
	registerTools(f.Server, "git_hub", "search")
	registerTools(f.Server, "git", "hub_search")

	// /mcp serves the key's virtual server, /mcp/v/{vs_id} the one it names,
	// by id or slug.
	if got := strings.Join(f.toolNames(t, "", nil), ","); got != "git__hub_search,git_hub__search" {
		t.Fatalf("/mcp for k1 = %s, want vs eng", got)
	}
	for _, addr := range []string{vsOps, "ops"} {
		if got := strings.Join(f.toolNames(t, addr, nil), ","); got != "git__hub_search" {
			t.Fatalf("/mcp/v/%s = %s, want vs ops", addr, got)
		}
	}
	var vsSeen string
	f.hookObserver = func(hook string, cc *CallContext) {
		if hook == hookTranslateRetry {
			vsSeen = cc.VirtualServer
		}
	}
	f.callResult(t, vsOps, "git__hub_search", nil)
	if vsSeen != vsOps {
		t.Fatalf("cc.VirtualServer = %q, want %s", vsSeen, vsOps)
	}
	// A key bound to none gets every org server, as access.default=allow says.
	f.principal.KeyID = "k2"
	if got := strings.Join(f.toolNames(t, "", nil), ","); got != "git__hub_search,git_hub__search" {
		t.Fatalf("/mcp for k2 = %s, want the compatibility view", got)
	}
	// A vs_id query parameter on /mcp selects nothing: only the route does.
	f.principal.KeyID = "k1"
	body, _ := json.Marshal(&Message{JSONRPC: "2.0", ID: json.RawMessage(`9`), Method: MethodToolsList})
	req := httptest.NewRequest("POST", "/mcp?vs_id="+vsOps, strings.NewReader(string(body)))
	req.Header.Set("Content-Type", "application/json") // the protocol handler answers 415 without it
	req.Header.Set("MCP-Session-Id", f.session)
	w := httptest.NewRecorder()
	f.HandlePost(w, req)
	if !strings.Contains(w.Body.String(), "git_hub__search") {
		t.Fatalf("/mcp?vs_id= switched virtual server: %s", w.Body.String())
	}
}

func TestUnknownVirtualServerSame404(t *testing.T) {
	f := newVSFixture(t, twoVS)
	registerTools(f.Server, "git_hub", "search")
	answer := func(vsID, method string, params any) (int, string) {
		w := f.post(t, vsID, method, params, nil)
		return w.Code, w.Body.String()
	}
	call := ToolCallParams{Name: "git_hub__search"}
	unknownCode, unknownBody := answer(vsOther, MethodToolsList, struct{}{})
	if unknownCode != http.StatusNotFound || !strings.Contains(unknownBody, `"virtual server not found"`) {
		t.Fatalf("unknown vs = %d %s, want 404", unknownCode, unknownBody)
	}
	// k2 may not use eng (its access names k1 only): the same answer.
	f.principal.KeyID = "k2"
	for _, vs := range []string{vsEng, "eng", vsOther, "vs_nope"} {
		for _, m := range []struct {
			method string
			params any
		}{{MethodToolsList, struct{}{}}, {MethodToolsCall, call}, {MethodResourcesList, struct{}{}}, {MethodPromptsList, struct{}{}}} {
			code, body := answer(vs, m.method, m.params)
			if code != http.StatusNotFound || body != unknownBody {
				t.Fatalf("%s on %s = %d %s, want exactly the unknown answer %s", m.method, vs, code, body, unknownBody)
			}
		}
	}
	// A virtual server of another org is unknown in this one.
	f.cfg = orgConfig(t, `{"mcp":{"servers":{"x":{"url":"https://c.example/mcp"}}}}`)
	if code, body := answer(vsEng, MethodToolsList, struct{}{}); code != http.StatusNotFound || body != unknownBody {
		t.Fatalf("a foreign vs = %d %s", code, body)
	}
	// With no org config at all, /mcp/v/{vs_id} serves nothing.
	f.cfg = nil
	if code, body := answer(vsEng, MethodToolsList, struct{}{}); code != http.StatusNotFound || body != unknownBody {
		t.Fatalf("no org config = %d %s", code, body)
	}
}

func TestVirtualServerResolvedInPrincipalOrgOnly(t *testing.T) {
	f := newVSFixture(t, "")
	registerTools(f.Server, "b", "search")
	// Both orgs have a virtual server with the same slug and id: each is
	// looked up in its own org's config, never through a global index.
	orgA := orgConfig(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["a","b"],"naming":"flat"}}}}`)
	orgB := orgConfig(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["a"],"naming":"flat"}},"servers":{"z":{"url":"https://z.example/mcp"}}}}`)
	var server string
	f.hookObserver = func(hook string, cc *CallContext) {
		if hook == hookTranslateRetry {
			server = cc.Upstream.ServerID
		}
	}
	asA := func() { f.principal, f.cfg = Principal{Kind: PrincipalKey, KeyID: "ka", OrgID: "org-a"}, orgA }
	asB := func() { f.principal, f.cfg = Principal{Kind: PrincipalKey, KeyID: "kb", OrgID: "org-b"}, orgB }

	asA()
	f.callResult(t, vsEng, "search", nil)
	if server != "b" {
		t.Fatalf("org A's %s resolved server %q, want b", vsEng, server)
	}
	asB()
	server = ""
	if resp := f.callResult(t, vsEng, "search", nil); resp.Error == nil || server != "" {
		t.Fatalf("org B's %s (members [a]) served b's search: %s %+v", vsEng, resp.Result, resp.Error)
	}
	if code := f.post(t, "vs_01M4ZEB9E3NOTINORGBATALL00", MethodToolsList, struct{}{}, nil).Code; code != http.StatusNotFound {
		t.Fatalf("an id from no org = %d, want 404", code)
	}

	// Name holders are kept per org: a refresh brings a/search; org A keeps
	// its incumbent b, while org B, composing for the first time, gives the
	// name to a.
	registerTools(f.Server, "a", "search")
	asA()
	f.callResult(t, vsEng, "search", nil)
	if server != "b" {
		t.Fatalf("org A after the refresh resolved %q, want the incumbent b", server)
	}
	asB()
	f.callResult(t, vsEng, "search", nil)
	if server != "a" {
		t.Fatalf("org B resolved %q, want a: org A's name holders leaked into org B", server)
	}
}

func mustVS(t *testing.T, cfg *tenant.OrgConfig, id string) tenant.MCPVirtualServerSettings {
	t.Helper()
	vs, ok := cfg.MCPVirtualServer(id)
	if !ok {
		t.Fatalf("no virtual server %s", id)
	}
	return vs
}

func TestRefreshCollisionQuarantinesNewcomer(t *testing.T) {
	cfg := `{"mcp":{"virtual_servers":{"eng":{"id":"` + vsEng + `","members":["zz","aa"],"naming":"flat","access":{"default_for_org":true}}}}}`
	f := newVSFixture(t, cfg)
	registerTools(f.Server, "zz", "search")
	if got := strings.Join(f.toolNames(t, "", nil), ","); got != "search" {
		t.Fatalf("first list = %s", got)
	}
	// A refresh brings aa/search, which sorts first: the incumbent keeps the
	// name, the newcomer is hidden and refused by name.
	registerTools(f.Server, "aa", "search", "other")
	if got := strings.Join(f.toolNames(t, "", nil), ","); got != "other,search" {
		t.Fatalf("after the refresh = %s", got)
	}
	var server string
	f.hookObserver = func(hook string, cc *CallContext) {
		if hook == hookTranslateRetry {
			server = cc.Upstream.ServerID
		}
	}
	f.callResult(t, "", "search", nil)
	if server != "zz" {
		t.Fatalf("search went to %q, want the incumbent zz", server)
	}
	tools, _ := f.Registry().Rejections("org-a", mustVS(t, f.cfg, "eng"), true)
	if len(tools) != 1 || tools[0].Ref != "aa/search" || !tools[0].Quarantined || tools[0].CollidesWith != "zz/search" || tools[0].Reason != "collision" {
		t.Fatalf("rejections = %+v, want aa/search quarantined behind zz/search", tools)
	}
}

// TestOutageNeverHandsANameToANewcomer: while the incumbent's upstream is
// gone, another member's tool of the same name is quarantined, never served
// under it, and the incumbent gets the name back when it reconnects.
func TestOutageNeverHandsANameToANewcomer(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["zz","aa"],"naming":"flat","access":{"default_for_org":true}}}}}`)
	registerTools(f.Server, "zz", "deploy")
	if got := strings.Join(f.toolNames(t, "", nil), ","); got != "deploy" {
		t.Fatalf("first list = %s", got)
	}
	f.Registry().UnregisterServer("zz") // the incumbent's upstream goes away
	registerTools(f.Server, "aa", "deploy")
	if got := f.toolNames(t, "", nil); len(got) != 0 {
		t.Fatalf("during the outage the list is %v, want aa/deploy quarantined", got)
	}
	requireQuarantined(t, f.callResult(t, "", "deploy", nil))
	if tools, _ := f.Registry().Rejections("org-a", mustVS(t, f.cfg, "eng"), true); len(tools) != 1 || tools[0].Ref != "aa/deploy" ||
		!tools[0].Quarantined || tools[0].CollidesWith != "zz/deploy" {
		t.Fatalf("rejections = %+v", tools)
	}
	registerTools(f.Server, "zz", "deploy")
	var server string
	f.hookObserver = func(hook string, cc *CallContext) {
		if hook == hookTranslateRetry {
			server = cc.Upstream.ServerID
		}
	}
	f.callResult(t, "", "deploy", nil)
	if server != "zz" {
		t.Fatalf("after the reconnect deploy went to %q, want the incumbent zz", server)
	}
}

func TestOverridesSurviveRefresh(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github"],"access":{"default_for_org":true},
		"overrides":{"github/create_issue":{"name":"open_issue","title":"Open issue","description":"Open a GitHub issue"}}}}}}`)
	upstream := func(desc string) []Tool {
		var tl Tool
		if err := json.Unmarshal([]byte(`{"name":"create_issue","title":"Create","description":"`+desc+`","inputSchema":{"type":"object"},"_meta":{"x":1},"icons":[{"src":"i.png"}]}`), &tl); err != nil {
			t.Fatal(err)
		}
		return []Tool{tl}
	}
	f.Registry().RegisterServer("github", upstream("v1"))
	check := func(when string) {
		t.Helper()
		resp := decodeResponse(t, f.post(t, "", MethodToolsList, struct{}{}, nil))
		var res ListToolsResult
		if err := json.Unmarshal(resp.Result, &res); err != nil || len(res.Tools) != 1 {
			t.Fatalf("%s: list %s %v", when, resp.Result, err)
		}
		tl := res.Tools[0]
		if tl.Name != "open_issue" || tl.Description != "Open a GitHub issue" {
			t.Fatalf("%s: tool = %+v", when, tl)
		}
		// The raw definition, which a pass-through writer sends, has only the
		// gateway-owned fields edited.
		v := callViewFor(t, f)
		e, _ := v.tools.Lookup("open_issue")
		raw := string(exposedTool(v.byRef[e.Ref()], e).Raw)
		for _, want := range []string{`"name":"open_issue"`, `"title":"Open issue"`, `"description":"Open a GitHub issue"`, `"_meta":{"x":1}`, `"icons":[{"src":"i.png"}]`} {
			if !strings.Contains(raw, want) {
				t.Fatalf("%s: raw %s lacks %s", when, raw, want)
			}
		}
	}
	check("first")
	f.Registry().RegisterServer("github", upstream("v2 upstream text"))
	check("after the refresh")
	v := callViewFor(t, f)
	if e, _ := v.tools.Lookup("open_issue"); e.OriginalName != "create_issue" || e.OriginalDescription != "v2 upstream text" {
		t.Fatalf("original fields = %q, %q", e.OriginalName, e.OriginalDescription)
	}
}

// callViewFor composes the fixture principal's /mcp view as step 4 does.
func callViewFor(t *testing.T, f *vsFixture) *callView {
	t.Helper()
	vs, configured := f.cfg.MCPDefaultVirtualServer(f.principal.KeyID)
	return f.Registry().compose(f.principal.OrgID, vs, configured)
}

// TestVirtualServerRefreshConcurrentWithCalls refreshes a member's tools
// while lists and calls compose the virtual server, under -race: a refresh
// never writes a RegisteredTool a call may hold.
func TestVirtualServerRefreshConcurrentWithCalls(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github"],"access":{"default_for_org":true}}}}}`)
	registerTools(f.Server, "github", "a", "b")
	done := make(chan struct{})
	go func() {
		defer close(done)
		for i := 0; i < 200; i++ {
			registerTools(f.Server, "github", "a", "b")
		}
	}()
	for i := 0; i < 50; i++ {
		if names := f.toolNames(t, "", nil); len(names) != 2 {
			t.Fatalf("list %d = %v", i, names)
		}
		f.callResult(t, "", "github__a", nil)
	}
	<-done
}

// TestOrgWithoutMCPConfigSeesNoStaticTools: the legacy view (the operator's
// static registry under today's names) is served only to a principal of
// mcp.static.org_id, and, while it is unset, to a principal with no org. A
// principal of any other org whose config has no mcp section, or that has no
// config at all, gets its org's empty compatibility view: access.default
// deny, nothing listed, and a static tool's name answers like an unknown
// tool. So does a principal with no org while static.org_id is set (the
// stricter reading of tenancy: no org is not the static org).
func TestOrgWithoutMCPConfigSeesNoStaticTools(t *testing.T) {
	f := newVSFixture(t, "")
	f.Registry().SetStaticOrg("org-static")
	f.Registry().RegisterServer("ops", []Tool{{Name: "restart", InputSchema: json.RawMessage(`{"type":"object"}`)}})
	f.Registry().RegisterResources("ops", []Resource{{URI: "file:///ops/runbook", Name: "runbook"}})
	f.Registry().RegisterPrompts("ops", []Prompt{{Name: "triage"}})
	var server string
	f.hookObserver = func(hook string, cc *CallContext) {
		if hook == hookTranslateRetry {
			server = cc.Upstream.ServerID
		}
	}
	lists := func() (tools, resources, prompts int) {
		t.Helper()
		var tl ListToolsResult
		var rl ListResourcesResult
		var pl ListPromptsResult
		for _, c := range []struct {
			method string
			out    any
		}{{MethodToolsList, &tl}, {MethodResourcesList, &rl}, {MethodPromptsList, &pl}} {
			resp := decodeResponse(t, f.post(t, "", c.method, struct{}{}, nil))
			if resp.Error != nil {
				t.Fatalf("%s: %+v", c.method, resp.Error)
			}
			if err := json.Unmarshal(resp.Result, c.out); err != nil {
				t.Fatal(err)
			}
		}
		return len(tl.Tools), len(rl.Resources), len(pl.Prompts)
	}

	// seesNone: the principal set on f lists none of the static servers'
	// tools, resources or prompts, and none of them answers it.
	seesNone := func(who string) {
		t.Helper()
		if nt, nr, np := lists(); nt+nr+np != 0 {
			t.Fatalf("%s lists %d tools, %d resources, %d prompts of the operator's static servers, want none", who, nt, nr, np)
		}
		server = ""
		requireUnknownTool(t, f.callResult(t, "", "ops_restart", nil), "ops_restart")
		if server != "" {
			t.Fatalf("%s: the call reached server %q", who, server)
		}
		resp := decodeResponse(t, f.post(t, "", MethodResourcesRead, ReadResourceParams{URI: "file:///ops/runbook"}, nil))
		if resp.Error == nil || resp.Error.Code != -32002 {
			t.Fatalf("%s read the static runbook: %s %+v", who, resp.Result, resp.Error)
		}
		resp = decodeResponse(t, f.post(t, "", MethodPromptsGet, GetPromptParams{Name: "ops_triage"}, nil))
		if resp.Error == nil || resp.Error.Code != ErrCodeInvalidParams {
			t.Fatalf("%s got the static prompt: %s %+v", who, resp.Result, resp.Error)
		}
	}
	// seesLegacy: the principal set on f gets the legacy view.
	seesLegacy := func(who string) {
		t.Helper()
		if nt, nr, np := lists(); nt != 1 || nr != 1 || np != 1 {
			t.Fatalf("%s lists %d tools, %d resources, %d prompts, want the legacy view's 1, 1, 1", who, nt, nr, np)
		}
		server = ""
		f.callResult(t, "", "ops_restart", nil)
		if server != "ops" {
			t.Fatalf("%s: ops_restart reached %q, want ops", who, server)
		}
	}

	for _, tc := range []struct {
		name string
		cfg  string
	}{{"no org config", ""}, {"an org config with no mcp section", `{"routing":{}}`}} {
		f.principal = Principal{Kind: PrincipalKey, KeyID: "k-org-x", OrgID: "org-x"}
		f.cfg = nil
		if tc.cfg != "" {
			f.cfg = orgConfig(t, tc.cfg)
		}
		seesNone("org-x, " + tc.name)
	}

	// The static org keeps the legacy view.
	f.principal, f.cfg = Principal{Kind: PrincipalKey, KeyID: "k-static", OrgID: "org-static"}, nil
	seesLegacy("the static org")

	// With static.org_id set, a principal with no org gets an empty pool.
	f.principal, f.cfg = Principal{}, nil
	seesNone("a principal with no org, static.org_id set")

	// With static.org_id unset (a YAML-only deployment), a principal with no
	// org keeps the legacy view, as before.
	f.Registry().SetStaticOrg("")
	seesLegacy("a principal with no org, static.org_id unset")
}

// TestLegacyListsKeepUpstreamRaw: on the legacy view a listed tool and prompt
// carry the upstream's raw definition with only the gateway-owned name
// edited, so a writer that passes Raw through keeps title, icons, _meta
// and extension fields, as the virtual-server view does.
func TestLegacyListsKeepUpstreamRaw(t *testing.T) {
	f := newVSFixture(t, "")
	f.principal = Principal{} // no org: the legacy view
	var tl Tool
	if err := json.Unmarshal([]byte(`{"name":"create_issue","title":"Create","description":"d","inputSchema":{"type":"object"},"_meta":{"x":1},"icons":[{"src":"i.png"}],"x-ext":true}`), &tl); err != nil {
		t.Fatal(err)
	}
	var pr Prompt
	if err := json.Unmarshal([]byte(`{"name":"triage","title":"Triage","description":"p","_meta":{"y":2},"icons":[{"src":"p.png"}]}`), &pr); err != nil {
		t.Fatal(err)
	}
	f.Registry().RegisterServer("github", []Tool{tl})
	f.Registry().RegisterPrompts("github", []Prompt{pr})
	cc := &CallContext{ctx: context.Background()}
	if d := assembleList(f.Server, cc); d != nil || len(cc.ToolList) != 1 {
		t.Fatalf("assembleList: %+v %v", d, cc.ToolList)
	}
	raw := string(cc.ToolList[0].Raw)
	for _, want := range []string{`"name":"github_create_issue"`, `"title":"Create"`, `"_meta":{"x":1}`, `"icons":[{"src":"i.png"}]`, `"x-ext":true`} {
		if !strings.Contains(raw, want) {
			t.Fatalf("legacy tool raw %s lacks %s", raw, want)
		}
	}
	if d := filterPrompts(f.Server, cc); d != nil || len(cc.PromptList) != 1 {
		t.Fatalf("filterPrompts: %+v %v", d, cc.PromptList)
	}
	raw = string(cc.PromptList[0].Raw)
	for _, want := range []string{`"name":"github_triage"`, `"title":"Triage"`, `"_meta":{"y":2}`, `"icons":[{"src":"p.png"}]`} {
		if !strings.Contains(raw, want) {
			t.Fatalf("legacy prompt raw %s lacks %s", raw, want)
		}
	}
}

// requireQuarantined checks an in-band tool_quarantined answer with
// metadata {reason: collision} and nothing else (mcp-denial-v1).
func requireQuarantined(t *testing.T, resp *Message) {
	t.Helper()
	obj := denial(t, resp)
	if obj == nil || obj.Code != "tool_quarantined" {
		t.Fatalf("answer %s %+v, want an in-band tool_quarantined", resp.Result, obj)
	}
	if meta, _ := obj.Metadata.(map[string]any); len(meta) != 1 || meta["reason"] != "collision" {
		t.Fatalf("tool_quarantined metadata = %v, want exactly {reason: collision}", obj.Metadata)
	}
	requireDenialSchema(t, resp)
}

// TestQuarantinedNewcomerRefusedByName: a call that names a quarantined
// newcomer's exposed name, which no tool holds (a case-folded collision, or
// an incumbent away), answers tool_quarantined {reason: collision} and never
// reaches an upstream; a caller an access rule refuses the newcomer, or the
// access.default deny of a compatibility view, gets the unknown-tool answer
// instead, so the code tells nothing to a caller who could not use the tool.
func TestQuarantinedNewcomerRefusedByName(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["zz","aa"],"naming":"flat","access":{"default_for_org":true,
		"rules":[{"match":{"key_ids":["k2"]},"deny":["aa/*"]}]}}}}}`)
	registerTools(f.Server, "zz", "Deploy")
	if got := strings.Join(f.toolNames(t, "", nil), ","); got != "Deploy" {
		t.Fatalf("first list = %s", got)
	}
	registerTools(f.Server, "aa", "deploy") // case-folded onto the incumbent's name
	var server string
	f.hookObserver = func(hook string, cc *CallContext) {
		if hook == hookTranslateRetry {
			server = cc.Upstream.ServerID
		}
	}
	requireQuarantined(t, f.callResult(t, "", "deploy", nil))
	if server != "" {
		t.Fatalf("the quarantined newcomer's call reached %q", server)
	}
	f.callResult(t, "", "Deploy", nil)
	if server != "zz" {
		t.Fatalf("the incumbent's name reached %q, want zz", server)
	}
	// k2 may not use aa's tools: the newcomer is unknown to it.
	f.principal.KeyID = "k2"
	requireUnknownTool(t, f.callResult(t, "", "deploy", nil), "deploy")
	// A name no tool ever had stays unknown.
	f.principal.KeyID = "k1"
	requireUnknownTool(t, f.callResult(t, "", "DEPLOY", nil), "DEPLOY")
}
