package a2a

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

func newTestA2AServer() *Server {
	return NewServer(CardConfig{
		Name:        "test-agentcc",
		Description: "Test gateway",
		Version:     "1.0",
	}, NewRegistry(nil))
}

func postA2A(t *testing.T, s *Server, msg *mcp.Message) *httptest.ResponseRecorder {
	t.Helper()
	body, _ := json.Marshal(msg)
	req := httptest.NewRequest("POST", "/a2a", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	s.HandleMessage(w, req)
	return w
}

func decodeA2AResponse(t *testing.T, w *httptest.ResponseRecorder) *mcp.Message {
	t.Helper()
	var msg mcp.Message
	if err := json.NewDecoder(w.Body).Decode(&msg); err != nil {
		t.Fatalf("decode: %v, body: %s", err, w.Body.String())
	}
	return &msg
}

func TestAgentCardEndpoint(t *testing.T) {
	s := newTestA2AServer()

	req := httptest.NewRequest("GET", "/.well-known/agent.json", nil)
	w := httptest.NewRecorder()
	s.HandleAgentCard(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	var card AgentCard
	if err := json.NewDecoder(w.Body).Decode(&card); err != nil {
		t.Fatal(err)
	}
	if card.Name != "test-agentcc" {
		t.Fatalf("expected test-agentcc, got %s", card.Name)
	}
	if !card.Capabilities.Streaming {
		t.Fatal("expected streaming capability")
	}
}

func TestMessageSend(t *testing.T) {
	s := newTestA2AServer()

	params := MessageSendParams{
		Message: Message{
			Role: "user",
			Parts: []MessagePart{
				{Type: "text", Text: "What is the weather?"},
			},
		},
	}
	paramsData, _ := json.Marshal(params)

	msg := &mcp.Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  MethodMessageSend,
		Params:  paramsData,
	}

	w := postA2A(t, s, msg)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	resp := decodeA2AResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var task Task
	if err := json.Unmarshal(resp.Result, &task); err != nil {
		t.Fatal(err)
	}
	if task.ID == "" {
		t.Fatal("expected task ID")
	}
	if task.Status.State != TaskStatusCompleted {
		t.Fatalf("expected completed, got %s", task.Status.State)
	}
	if len(task.Artifacts) != 1 {
		t.Fatalf("expected 1 artifact, got %d", len(task.Artifacts))
	}
}

func TestMessageSendEmptyText(t *testing.T) {
	s := newTestA2AServer()

	params := MessageSendParams{
		Message: Message{
			Role:  "user",
			Parts: []MessagePart{{Type: "file", URI: "http://example.com/file.txt"}},
		},
	}
	paramsData, _ := json.Marshal(params)

	msg := &mcp.Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  MethodMessageSend,
		Params:  paramsData,
	}

	w := postA2A(t, s, msg)
	resp := decodeA2AResponse(t, w)
	if resp.Error != nil {
		t.Fatal("expected task result, not error")
	}

	var task Task
	json.Unmarshal(resp.Result, &task)
	if task.Status.State != TaskStatusFailed {
		t.Fatalf("expected failed for no text, got %s", task.Status.State)
	}
}

func TestTasksGet(t *testing.T) {
	s := newTestA2AServer()

	// First, send a message to create a task.
	sendParams := MessageSendParams{
		Message: Message{
			Role:  "user",
			Parts: []MessagePart{{Type: "text", Text: "Hello"}},
		},
	}
	sendData, _ := json.Marshal(sendParams)
	sendMsg := &mcp.Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  MethodMessageSend,
		Params:  sendData,
	}
	w := postA2A(t, s, sendMsg)
	sendResp := decodeA2AResponse(t, w)
	var createdTask Task
	json.Unmarshal(sendResp.Result, &createdTask)

	// Now get the task.
	getParams := TaskGetParams{TaskID: createdTask.ID}
	getData, _ := json.Marshal(getParams)
	getMsg := &mcp.Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`2`),
		Method:  MethodTasksGet,
		Params:  getData,
	}

	w = postA2A(t, s, getMsg)
	resp := decodeA2AResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var task Task
	json.Unmarshal(resp.Result, &task)
	if task.ID != createdTask.ID {
		t.Fatalf("expected %s, got %s", createdTask.ID, task.ID)
	}
}

func TestTasksGetNotFound(t *testing.T) {
	s := newTestA2AServer()

	params := TaskGetParams{TaskID: "nonexistent"}
	data, _ := json.Marshal(params)
	msg := &mcp.Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  MethodTasksGet,
		Params:  data,
	}

	w := postA2A(t, s, msg)
	resp := decodeA2AResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for nonexistent task")
	}
}

func TestUnknownMethod(t *testing.T) {
	s := newTestA2AServer()

	msg := &mcp.Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  "unknown/method",
	}

	w := postA2A(t, s, msg)
	resp := decodeA2AResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for unknown method")
	}
	if resp.Error.Code != mcp.ErrCodeMethodNotFound {
		t.Fatalf("expected method not found, got %d", resp.Error.Code)
	}
}

func TestInvalidJSON(t *testing.T) {
	s := newTestA2AServer()

	req := httptest.NewRequest("POST", "/a2a", bytes.NewReader([]byte("not json")))
	w := httptest.NewRecorder()
	s.HandleMessage(w, req)

	resp := decodeA2AResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected parse error")
	}
	if resp.Error.Code != mcp.ErrCodeParse {
		t.Fatalf("expected parse error code, got %d", resp.Error.Code)
	}
}

func TestListAgents(t *testing.T) {
	agents := map[string]AgentConfig{
		"travel": {URL: "http://travel.local", Description: "Travel agent"},
		"code":   {URL: "http://code.local"},
	}
	reg := NewRegistry(agents)
	s := NewServer(CardConfig{Name: "test"}, reg)

	req := httptest.NewRequest("GET", "/v1/agents", nil)
	w := httptest.NewRecorder()
	s.ListAgents(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	var result []json.RawMessage
	if err := json.NewDecoder(w.Body).Decode(&result); err != nil {
		t.Fatal(err)
	}
	if len(result) != 2 {
		t.Fatalf("expected 2 agents, got %d", len(result))
	}
}

func TestTasksCancel(t *testing.T) {
	s := newTestA2AServer()

	// Create a task.
	sendParams := MessageSendParams{
		Message: Message{
			Role:  "user",
			Parts: []MessagePart{{Type: "text", Text: "Hello"}},
		},
	}
	sendData, _ := json.Marshal(sendParams)
	w := postA2A(t, s, &mcp.Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  MethodMessageSend,
		Params:  sendData,
	})
	var created Task
	json.Unmarshal(decodeA2AResponse(t, w).Result, &created)

	// Cancel it.
	cancelData, _ := json.Marshal(TaskCancelParams{TaskID: created.ID})
	w = postA2A(t, s, &mcp.Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`2`),
		Method:  MethodTasksCancel,
		Params:  cancelData,
	})

	resp := decodeA2AResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}
	var task Task
	json.Unmarshal(resp.Result, &task)
	if task.Status.State != TaskStatusCanceled {
		t.Fatalf("expected canceled, got %s", task.Status.State)
	}
}
