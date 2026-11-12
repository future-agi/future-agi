package mcp

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

func TestResourcesList(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	// Register resources.
	s.Registry().RegisterResources("files", []Resource{
		{URI: "file:///readme.md", Name: "README", MIMEType: "text/markdown"},
		{URI: "file:///config.json", Name: "Config", MIMEType: "application/json"},
	})

	sessionID := initializeSession(t, s)

	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodResourcesList}
	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})

	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result ListResourcesResult
	json.Unmarshal(resp.Result, &result)
	if len(result.Resources) != 2 {
		t.Fatalf("expected 2 resources, got %d", len(result.Resources))
	}
}

func TestResourcesListEmpty(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	sessionID := initializeSession(t, s)

	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodResourcesList}
	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})

	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result ListResourcesResult
	json.Unmarshal(resp.Result, &result)
	if len(result.Resources) != 0 {
		t.Fatalf("expected 0 resources, got %d", len(result.Resources))
	}
}

func TestResourcesRead(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	s.Registry().RegisterResources("files", []Resource{
		{URI: "file:///readme.md", Name: "README"},
	})

	// Mock upstream that responds to resources/read.
	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)

		switch msg.Method {
		case MethodResourcesRead:
			result := ReadResourceResult{
				Contents: []ResourceContent{{URI: "file:///readme.md", Text: "# Hello"}},
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
	defer mockUpstream.Close()

	client := NewClient(ClientConfig{ServerID: "files", URL: mockUpstream.URL, TransportType: "http"})
	client.transport = NewHTTPTransport(mockUpstream.URL, AuthConfig{})
	client.healthy.Store(true)
	s.clientsMu.Lock()
	s.clients["files"] = client
	s.clientsMu.Unlock()

	sessionID := initializeSession(t, s)

	params := ReadResourceParams{URI: "file:///readme.md"}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodResourcesRead, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result ReadResourceResult
	json.Unmarshal(resp.Result, &result)
	if len(result.Contents) != 1 {
		t.Fatalf("expected 1 content, got %d", len(result.Contents))
	}
	if result.Contents[0].Text != "# Hello" {
		t.Fatalf("expected '# Hello', got %s", result.Contents[0].Text)
	}
}

func TestResourcesReadNotFound(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	sessionID := initializeSession(t, s)

	params := ReadResourceParams{URI: "file:///nonexistent"}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodResourcesRead, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for non-existent resource")
	}
	// A legacy request (no 2026 era) gets -32002, with the URI as data.
	if resp.Error.Code != -32002 || resp.Error.Message != "Resource not found" || string(resp.Error.Data) != `{"uri":"file:///nonexistent"}` {
		t.Fatalf("not found = %+v, want -32002 Resource not found with the uri", resp.Error)
	}
}

func TestPromptsList(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	s.Registry().RegisterPrompts("ai", []Prompt{
		{Name: "summarize", Description: "Summarize text", Arguments: []PromptArgument{{Name: "text", Required: true}}},
		{Name: "translate", Description: "Translate text"},
	})

	sessionID := initializeSession(t, s)

	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPromptsList}
	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})

	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result ListPromptsResult
	json.Unmarshal(resp.Result, &result)
	if len(result.Prompts) != 2 {
		t.Fatalf("expected 2 prompts, got %d", len(result.Prompts))
	}
}

func TestPromptsGet(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	s.Registry().RegisterPrompts("ai", []Prompt{
		{Name: "summarize", Description: "Summarize text"},
	})

	// Mock upstream that responds to prompts/get.
	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)

		switch msg.Method {
		case MethodPromptsGet:
			result := GetPromptResult{
				Description: "Summary prompt",
				Messages:    []PromptMessage{{Role: "user", Content: ContentPart{Type: "text", Text: "Summarize: {text}"}}},
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
	defer mockUpstream.Close()

	client := NewClient(ClientConfig{ServerID: "ai", URL: mockUpstream.URL, TransportType: "http"})
	client.transport = NewHTTPTransport(mockUpstream.URL, AuthConfig{})
	client.healthy.Store(true)
	s.clientsMu.Lock()
	s.clients["ai"] = client
	s.clientsMu.Unlock()

	sessionID := initializeSession(t, s)

	params := GetPromptParams{Name: "ai_summarize", Arguments: map[string]string{"text": "hello world"}}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPromptsGet, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result GetPromptResult
	json.Unmarshal(resp.Result, &result)
	if len(result.Messages) != 1 {
		t.Fatalf("expected 1 message, got %d", len(result.Messages))
	}
}

func TestPromptsGetNotFound(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	sessionID := initializeSession(t, s)

	params := GetPromptParams{Name: "nonexistent_prompt"}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPromptsGet, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for non-existent prompt")
	}
}

func TestResourcesRequireSession(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodResourcesList}
	w := postMCP(t, s, msg, nil)
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for missing session on resources/list")
	}
}

func TestPromptsRequireSession(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPromptsList}
	w := postMCP(t, s, msg, nil)
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for missing session on prompts/list")
	}
}

// recordingUpstream is an MCP server listing one resource and one prompt,
// which records the URI and prompt name each read asks it for.
type recordingUpstream struct {
	*httptest.Server
	mu    sync.Mutex
	asked []string
}

func startRecordingUpstream(t *testing.T, uri, prompt string) *recordingUpstream {
	t.Helper()
	u := &recordingUpstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		_ = json.NewDecoder(r.Body).Decode(&msg)
		if msg.IsNotification() {
			w.WriteHeader(http.StatusAccepted)
			return
		}
		var p struct {
			URI  string `json:"uri"`
			Name string `json:"name"`
		}
		_ = json.Unmarshal(msg.Params, &p)
		result := `{}`
		switch msg.Method {
		case MethodInitialize:
			result = `{"protocolVersion":"2025-11-25","capabilities":{"resources":{},"prompts":{}},"serverInfo":{"name":"rec","version":"1"}}`
		case MethodToolsList:
			result = `{"tools":[]}`
		case MethodResourcesList:
			result = `{"resources":[{"uri":"` + uri + `","name":"r","size":3}]}`
		case MethodPromptsList:
			result = `{"prompts":[{"name":"` + prompt + `","title":"T"}]}`
		case MethodResourcesRead:
			u.add("read:" + p.URI)
			result = `{"contents":[{"uri":"` + p.URI + `","text":"abc"}]}`
		case MethodPromptsGet:
			u.add("get:" + p.Name)
			result = `{"messages":[{"role":"user","content":{"type":"text","text":"hi"}}]}`
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"jsonrpc":"2.0","id":` + string(msg.ID) + `,"result":` + result + `}`))
	}))
	t.Cleanup(u.Close)
	return u
}

func (u *recordingUpstream) add(s string) {
	u.mu.Lock()
	defer u.mu.Unlock()
	u.asked = append(u.asked, s)
}

func (u *recordingUpstream) last() string {
	u.mu.Lock()
	defer u.mu.Unlock()
	if len(u.asked) == 0 {
		return ""
	}
	return u.asked[len(u.asked)-1]
}

func connectRecording(t *testing.T, s *Server, id string, u *recordingUpstream) {
	t.Helper()
	c := NewClient(ClientConfig{ServerID: id, URL: u.URL, TransportType: "http"})
	if err := c.Connect(t.Context()); err != nil {
		t.Fatalf("connect %s: %v", id, err)
	}
	s.RegisterClient(c)
}

func TestResourcesPromptsNamespaced(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["files","docs"],"access":{"default_for_org":true}}}}}`)
	files := startRecordingUpstream(t, "file:///a.txt", "greet")
	docs := startRecordingUpstream(t, "file:///a.txt", "greet")
	connectRecording(t, f.Server, "files", files)
	connectRecording(t, f.Server, "docs", docs)

	// Two servers' same URI and prompt name stay apart under the virtual
	// server's names; the legacy registry keeps the first and reports the
	// second (never overwritten).
	resp := decodeResponse(t, f.post(t, "", MethodResourcesList, struct{}{}, nil))
	if got := string(resp.Result); !strings.Contains(got, `"uri":"mcp://docs/file:///a.txt"`) || !strings.Contains(got, `"uri":"mcp://files/file:///a.txt"`) ||
		strings.Index(got, "mcp://docs/") > strings.Index(got, "mcp://files/") {
		t.Fatalf("resources/list = %s, want both, namespaced and sorted", got)
	}
	resp = decodeResponse(t, f.post(t, "", MethodPromptsList, struct{}{}, nil))
	if got := string(resp.Result); !strings.Contains(got, `"name":"docs__greet"`) || !strings.Contains(got, `"name":"files__greet"`) {
		t.Fatalf("prompts/list = %s", got)
	}
	if errs := f.Registry().ServerErrors("docs"); len(errs) != 1 || !strings.Contains(errs[0], ErrToolNameCollision) {
		t.Fatalf("legacy registry errors for docs = %v, want the resource collision", errs)
	}

	// A read and a get reach the right upstream under its own URI and name.
	resp = decodeResponse(t, f.post(t, "", MethodResourcesRead, ReadResourceParams{URI: "mcp://docs/file:///a.txt"}, nil))
	if resp.Error != nil || !strings.Contains(string(resp.Result), "abc") || docs.last() != "read:file:///a.txt" || files.last() != "" {
		t.Fatalf("read = %s %+v; docs asked %q, files asked %q", resp.Result, resp.Error, docs.last(), files.last())
	}
	resp = decodeResponse(t, f.post(t, "", MethodPromptsGet, GetPromptParams{Name: "files__greet"}, nil))
	if resp.Error != nil || files.last() != "get:greet" {
		t.Fatalf("get = %s %+v; files asked %q", resp.Result, resp.Error, files.last())
	}
	// A raw URI, or a member that is not in the virtual server, is unknown.
	for _, uri := range []string{"file:///a.txt", "mcp://other/file:///a.txt", "mcp://files"} {
		if resp := decodeResponse(t, f.post(t, "", MethodResourcesRead, ReadResourceParams{URI: uri}, nil)); resp.Error == nil || resp.Error.Code != -32002 {
			t.Fatalf("read %s = %+v, want not found", uri, resp.Error)
		}
	}
	if resp := decodeResponse(t, f.post(t, "", MethodPromptsGet, GetPromptParams{Name: "files_greet"}, nil)); resp.Error == nil || resp.Error.Code != ErrCodeInvalidParams {
		t.Fatalf("a legacy prompt name through a virtual server = %+v, want -32602", resp.Error)
	}
	// Removing a server removes only its entries.
	f.UnregisterClient("docs")
	resp = decodeResponse(t, f.post(t, "", MethodResourcesList, struct{}{}, nil))
	if got := string(resp.Result); strings.Contains(got, "mcp://docs/") || !strings.Contains(got, "mcp://files/") {
		t.Fatalf("after removing docs, resources/list = %s", got)
	}
}

func TestResourceNotFoundCodeByEra(t *testing.T) {
	f := newVSFixture(t, "")
	f.Registry().SetStaticOrg(f.principal.OrgID) // the legacy view is the static org's
	for _, view := range []string{"legacy", "virtual server"} {
		if view == "virtual server" {
			f.cfg = orgConfig(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["files"],"access":{"default_for_org":true}}}}}`)
		}
		for era, want := range map[string]int{"2026-07-28": ErrCodeInvalidParams, "2025-11-25": -32002, "": -32002} {
			f.era = era
			resp := decodeResponse(t, f.post(t, "", MethodResourcesRead, ReadResourceParams{URI: "mcp://files/nope"}, nil))
			if resp.Error == nil || resp.Error.Code != want || string(resp.Error.Data) != `{"uri":"mcp://files/nope"}` {
				t.Fatalf("%s, era %q: %+v, want %d", view, era, resp.Error, want)
			}
		}
	}
}

// handleShaper serves one spilled result to the principal and run it
// belongs to (P11's rule).
type handleShaper struct{ identityShaper }

func (handleShaper) Read(cc *CallContext, uri string) (json.RawMessage, bool) {
	if uri == "mcp://fagi/results/r-1" && cc.Principal.KeyID == "k1" && cc.Agent.RunID == "run-1" {
		return json.RawMessage(`{"contents":[{"uri":"mcp://fagi/results/r-1","text":"spilled"}]}`), true
	}
	return nil, false
}

func TestResultHandleReadThroughShaper(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["files"],"access":{"default_for_org":true}}}}}`)
	read := func() *Message {
		return decodeResponse(t, f.post(t, "", MethodResourcesRead, ReadResourceParams{URI: "mcp://fagi/results/r-1"}, nil))
	}
	// The default shaper spills nothing: every handle is unknown.
	if resp := read(); resp.Error == nil || resp.Error.Code != -32002 {
		t.Fatalf("with the identity shaper: %+v, want the unknown-resource answer", resp.Error)
	}
	f.SetDeps(Deps{ResultShaper: handleShaper{}})
	var hooks []string
	f.hookObserver = func(hook string, _ *CallContext) { hooks = append(hooks, hook) }
	f.runID = "run-1"
	if resp := read(); resp.Error != nil || !strings.Contains(string(resp.Result), "spilled") {
		t.Fatalf("same principal and run: %s %+v", resp.Result, resp.Error)
	}
	if strings.Join(hooks, ",") != "startSpan,resolvePrincipal,parseAgent,resolveTenant,resolveVirtualServer,authorizeResource,scanResource" {
		t.Fatalf("a handle read ran %v: it must not go upstream", hooks)
	}
	f.principal.KeyID = "k2"
	if resp := read(); resp.Error == nil || resp.Error.Code != -32002 {
		t.Fatalf("another principal: %s %+v, want the unknown-resource answer", resp.Result, resp.Error)
	}
	f.principal.KeyID, f.runID = "k1", "run-2"
	if resp := read(); resp.Error == nil {
		t.Fatalf("another run: %s, want the unknown-resource answer", resp.Result)
	}
}

// TestAccessRulesNarrowResourcesAndPrompts: the virtual server's access rules
// judge a member's prompts and resources as they judge its tools, by
// <member>/<name> (deny also by the exposed name), so a principal denied a
// whole server reads none of its prompts or resources, and an allow list
// keeps only what it names. A list and a read or get agree for every
// principal: what is not listed answers like an unknown name.
func TestAccessRulesNarrowResourcesAndPrompts(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["files","docs"],"access":{"default_for_org":true,"rules":[
		{"match":{"key_ids":["k1"]},"deny":["docs/*"]},
		{"match":{"agent_ids":["a1"]},"allow":["files/greet"]},
		{"match":{"user_ids":["u1"]},"deny":["files__greet","mcp://files/file:///a.txt"]}]}}}}}`)
	connectRecording(t, f.Server, "files", startRecordingUpstream(t, "file:///a.txt", "greet"))
	connectRecording(t, f.Server, "docs", startRecordingUpstream(t, "file:///a.txt", "greet"))

	prompts := []string{"docs__greet", "files__greet"}
	resources := []string{"mcp://docs/file:///a.txt", "mcp://files/file:///a.txt"}
	reached := false
	f.hookObserver = func(hook string, _ *CallContext) {
		if hook == hookReadResource || hook == hookGetPrompt {
			reached = true
		}
	}
	listed := func(method, key, field string) map[string]bool {
		resp := decodeResponse(t, f.post(t, "", method, struct{}{}, nil))
		if resp.Error != nil {
			t.Fatalf("%s: %+v", method, resp.Error)
		}
		var res map[string][]map[string]any
		if err := json.Unmarshal(resp.Result, &res); err != nil {
			t.Fatal(err)
		}
		out := map[string]bool{}
		for _, item := range res[key] {
			out[item[field].(string)] = true
		}
		return out
	}
	for _, tc := range []struct {
		who                string
		key, agent, user   string
		prompts, resources string
	}{
		{"no rule matches", "k2", "", "", "docs__greet,files__greet", "mcp://docs/file:///a.txt,mcp://files/file:///a.txt"},
		{"a key denied docs/*", "k1", "", "", "files__greet", "mcp://files/file:///a.txt"},
		{"an agent allowed files/greet only", "k2", "a1", "", "files__greet", ""},
		{"a user denied by exposed names", "k2", "", "u1", "docs__greet", "mcp://docs/file:///a.txt"},
	} {
		f.principal.KeyID, f.agentID, f.principal.UserID = tc.key, tc.agent, tc.user
		gotPrompts, gotResources := listed(MethodPromptsList, "prompts", "name"), listed(MethodResourcesList, "resources", "uri")
		var p, r []string
		for _, name := range prompts {
			if gotPrompts[name] {
				p = append(p, name)
			}
			reached = false
			resp := decodeResponse(t, f.post(t, "", MethodPromptsGet, GetPromptParams{Name: name}, nil))
			if reached != gotPrompts[name] {
				t.Errorf("%s: prompt %s listed=%v but get passed access=%v", tc.who, name, gotPrompts[name], reached)
			}
			if !gotPrompts[name] && (resp.Error == nil || resp.Error.Code != ErrCodeInvalidParams || resp.Error.Message != "Unknown prompt: "+name) {
				t.Errorf("%s: get %s = %s %+v, want -32602 Unknown prompt", tc.who, name, resp.Result, resp.Error)
			}
		}
		for _, uri := range resources {
			if gotResources[uri] {
				r = append(r, uri)
			}
			reached = false
			resp := decodeResponse(t, f.post(t, "", MethodResourcesRead, ReadResourceParams{URI: uri}, nil))
			if reached != gotResources[uri] {
				t.Errorf("%s: resource %s listed=%v but read passed access=%v", tc.who, uri, gotResources[uri], reached)
			}
			if !gotResources[uri] && (resp.Error == nil || resp.Error.Code != -32002) {
				t.Errorf("%s: read %s = %s %+v, want the unknown-resource answer", tc.who, uri, resp.Result, resp.Error)
			}
		}
		if strings.Join(p, ",") != tc.prompts || strings.Join(r, ",") != tc.resources {
			t.Errorf("%s: lists prompts %v and resources %v, want %q and %q", tc.who, p, r, tc.prompts, tc.resources)
		}
	}
}
