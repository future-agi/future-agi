package mcp

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
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
	if resp.Error.Code != ErrCodeMethodNotFound {
		t.Fatalf("expected code %d, got %d", ErrCodeMethodNotFound, resp.Error.Code)
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
