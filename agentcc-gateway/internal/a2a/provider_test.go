package a2a

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestProviderID(t *testing.T) {
	p := NewProvider(NewRegistry(nil))
	if p.ID() != "a2a" {
		t.Fatalf("expected 'a2a', got %s", p.ID())
	}
}

func TestProviderListModels(t *testing.T) {
	agents := map[string]AgentConfig{
		"travel": {URL: "http://travel.local"},
		"code":   {URL: "http://code.local"},
	}
	p := NewProvider(NewRegistry(agents))

	modelList, err := p.ListModels(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(modelList) != 2 {
		t.Fatalf("expected 2 models, got %d", len(modelList))
	}

	found := make(map[string]bool)
	for _, m := range modelList {
		found[m.ID] = true
		if m.OwnedBy != "a2a" {
			t.Fatalf("expected owned_by 'a2a', got %s", m.OwnedBy)
		}
	}
	if !found["a2a/travel"] || !found["a2a/code"] {
		t.Fatalf("expected a2a/travel and a2a/code, got %v", found)
	}
}

func TestProviderListModelsEmpty(t *testing.T) {
	p := NewProvider(NewRegistry(nil))
	modelList, err := p.ListModels(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(modelList) != 0 {
		t.Fatalf("expected 0 models, got %d", len(modelList))
	}
}

func TestProviderChatCompletionBadModel(t *testing.T) {
	p := NewProvider(NewRegistry(nil))

	req := &models.ChatCompletionRequest{Model: "gpt-4"}
	_, err := p.ChatCompletion(context.Background(), req)
	if err == nil {
		t.Fatal("expected error for non-a2a model")
	}
}

func TestProviderChatCompletionAgentNotFound(t *testing.T) {
	p := NewProvider(NewRegistry(nil))

	contentJSON, _ := json.Marshal("hello")
	req := &models.ChatCompletionRequest{
		Model: "a2a/nonexistent",
		Messages: []models.Message{
			{Role: "user", Content: contentJSON},
		},
	}
	_, err := p.ChatCompletion(context.Background(), req)
	if err == nil {
		t.Fatal("expected error for nonexistent agent")
	}
}

func TestProviderChatCompletionNoText(t *testing.T) {
	agents := map[string]AgentConfig{
		"test": {URL: "http://test.local"},
	}
	p := NewProvider(NewRegistry(agents))

	req := &models.ChatCompletionRequest{
		Model:    "a2a/test",
		Messages: []models.Message{},
	}
	_, err := p.ChatCompletion(context.Background(), req)
	if err == nil {
		t.Fatal("expected error for empty messages")
	}
}

// TestProviderChatCompletionWithMockAgent tests the full flow with a mock A2A agent.
func TestProviderChatCompletionWithMockAgent(t *testing.T) {
	// Create a mock A2A agent that responds to message/send.
	mockAgent := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg struct {
			Method string          `json:"method"`
			ID     json.RawMessage `json:"id"`
		}
		json.NewDecoder(r.Body).Decode(&msg)

		task := Task{
			ID: "mock-task-1",
			Status: TaskStatus{
				State: TaskStatusCompleted,
			},
			Artifacts: []Artifact{
				{
					Name:  "response",
					Index: 0,
					Parts: []MessagePart{
						{Type: "text", Text: "Mock response to your query"},
					},
				},
			},
		}

		result, _ := json.Marshal(task)
		resp := map[string]interface{}{
			"jsonrpc": "2.0",
			"id":      json.RawMessage(`1`),
			"result":  json.RawMessage(result),
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer mockAgent.Close()

	agents := map[string]AgentConfig{
		"mock": {URL: mockAgent.URL},
	}
	p := NewProvider(NewRegistry(agents))

	contentJSON, _ := json.Marshal("What is the weather?")
	req := &models.ChatCompletionRequest{
		Model: "a2a/mock",
		Messages: []models.Message{
			{Role: "user", Content: contentJSON},
		},
	}

	resp, err := p.ChatCompletion(context.Background(), req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if resp.Object != "chat.completion" {
		t.Fatalf("expected chat.completion, got %s", resp.Object)
	}
	if resp.Model != "a2a/mock" {
		t.Fatalf("expected a2a/mock, got %s", resp.Model)
	}
	if len(resp.Choices) != 1 {
		t.Fatalf("expected 1 choice, got %d", len(resp.Choices))
	}

	var text string
	json.Unmarshal(resp.Choices[0].Message.Content, &text)
	if text != "Mock response to your query" {
		t.Fatalf("expected mock response, got %q", text)
	}
	if resp.Choices[0].FinishReason != "stop" {
		t.Fatalf("expected stop, got %s", resp.Choices[0].FinishReason)
	}
}

func TestProviderStreamChatCompletion(t *testing.T) {
	// Create a mock A2A agent.
	mockAgent := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		task := Task{
			ID:     "stream-task-1",
			Status: TaskStatus{State: TaskStatusCompleted},
			Artifacts: []Artifact{
				{Parts: []MessagePart{{Type: "text", Text: "Streamed response"}}},
			},
		}
		result, _ := json.Marshal(task)
		resp := map[string]interface{}{
			"jsonrpc": "2.0",
			"id":      json.RawMessage(`1`),
			"result":  json.RawMessage(result),
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer mockAgent.Close()

	agents := map[string]AgentConfig{"s": {URL: mockAgent.URL}}
	p := NewProvider(NewRegistry(agents))

	contentJSON, _ := json.Marshal("hello")
	req := &models.ChatCompletionRequest{
		Model:    "a2a/s",
		Stream:   true,
		Messages: []models.Message{{Role: "user", Content: contentJSON}},
	}

	chunks, errCh := p.StreamChatCompletion(context.Background(), req)

	var received []models.StreamChunk
	for chunk := range chunks {
		received = append(received, chunk)
	}

	select {
	case err := <-errCh:
		if err != nil {
			t.Fatalf("unexpected stream error: %v", err)
		}
	default:
	}

	if len(received) != 1 {
		t.Fatalf("expected 1 chunk, got %d", len(received))
	}
	if received[0].Object != "chat.completion.chunk" {
		t.Fatalf("expected chat.completion.chunk, got %s", received[0].Object)
	}
	if received[0].Choices[0].Delta.Content == nil || *received[0].Choices[0].Delta.Content != "Streamed response" {
		t.Fatal("expected streamed response content")
	}
}

func TestExtractAgentName(t *testing.T) {
	cases := []struct {
		model    string
		expected string
	}{
		{"a2a/travel", "travel"},
		{"a2a/code-helper", "code-helper"},
		{"openai/gpt-4", ""},
		{"gpt-4", ""},
		{"a2a/", ""},
	}

	for _, tc := range cases {
		got := extractAgentName(tc.model)
		if got != tc.expected {
			t.Errorf("extractAgentName(%q) = %q, want %q", tc.model, got, tc.expected)
		}
	}
}

func TestExtractTextFromMessages(t *testing.T) {
	// Simple string content.
	textJSON, _ := json.Marshal("hello world")
	msgs := []models.Message{
		{Role: "user", Content: textJSON},
	}
	text := extractTextFromMessages(msgs)
	if text != "hello world" {
		t.Fatalf("expected 'hello world', got %q", text)
	}

	// Multiple messages.
	sysJSON, _ := json.Marshal("You are helpful")
	msgs = []models.Message{
		{Role: "system", Content: sysJSON},
		{Role: "user", Content: textJSON},
	}
	text = extractTextFromMessages(msgs)
	if text != "You are helpful\nhello world" {
		t.Fatalf("expected concatenated text, got %q", text)
	}

	// Array content parts (multimodal format).
	arrayContent := []map[string]string{
		{"type": "text", "text": "describe this"},
		{"type": "image_url", "text": ""},
	}
	arrayJSON, _ := json.Marshal(arrayContent)
	msgs = []models.Message{
		{Role: "user", Content: arrayJSON},
	}
	text = extractTextFromMessages(msgs)
	if text != "describe this" {
		t.Fatalf("expected 'describe this', got %q", text)
	}

	// Assistant messages are skipped.
	assistantJSON, _ := json.Marshal("I can help")
	msgs = []models.Message{
		{Role: "assistant", Content: assistantJSON},
		{Role: "user", Content: textJSON},
	}
	text = extractTextFromMessages(msgs)
	if text != "hello world" {
		t.Fatalf("expected only user text, got %q", text)
	}
}

func TestTaskToResponse(t *testing.T) {
	task := &Task{
		ID: "test-123",
		Artifacts: []Artifact{
			{Parts: []MessagePart{
				{Type: "text", Text: "result text"},
			}},
		},
	}

	resp := taskToResponse(task, "a2a/agent")
	if resp.ID != "a2a-test-123" {
		t.Fatalf("expected a2a-test-123, got %s", resp.ID)
	}
	if resp.Model != "a2a/agent" {
		t.Fatalf("expected a2a/agent, got %s", resp.Model)
	}
	if len(resp.Choices) != 1 {
		t.Fatalf("expected 1 choice, got %d", len(resp.Choices))
	}
	if resp.Choices[0].FinishReason != "stop" {
		t.Fatalf("expected stop, got %s", resp.Choices[0].FinishReason)
	}

	var text string
	json.Unmarshal(resp.Choices[0].Message.Content, &text)
	if text != "result text" {
		t.Fatalf("expected 'result text', got %q", text)
	}
}

func TestProviderClose(t *testing.T) {
	p := NewProvider(NewRegistry(nil))
	if err := p.Close(); err != nil {
		t.Fatal(err)
	}
}
