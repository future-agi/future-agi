package a2a

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A direct caller can reuse the top-level request after StreamChatCompletion
// returns. Hold the registry lock so the worker cannot consume Messages until
// after that reuse. Nested payloads themselves stay read-only, as in the other
// asynchronous providers' request-snapshot contract.
func TestG1A2ARequestSnapshot(t *testing.T) {
	for _, tc := range []struct {
		name            string
		changeModel     bool
		replaceMessages bool
	}{
		{name: "model", changeModel: true},
		{name: "messages", replaceMessages: true},
		{name: "request_reuse", changeModel: true, replaceMessages: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			type wireRequest struct {
				path   string
				method string
				params MessageSendParams
			}
			wire := make(chan wireRequest, 1)
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var rpc struct {
					Method string            `json:"method"`
					Params MessageSendParams `json:"params"`
				}
				if err := json.NewDecoder(r.Body).Decode(&rpc); err != nil {
					t.Errorf("decode A2A request: %v", err)
					w.WriteHeader(http.StatusBadRequest)
					return
				}
				wire <- wireRequest{path: r.URL.Path, method: rpc.Method, params: rpc.Params}
				w.Header().Set("Content-Type", "application/json")
				if err := json.NewEncoder(w).Encode(map[string]any{
					"jsonrpc": "2.0",
					"id":      1,
					"result": Task{
						ID:     "snapshot-task",
						Status: TaskStatus{State: TaskStatusCompleted},
						Artifacts: []Artifact{
							{Parts: []MessagePart{{Type: "text", Text: "snapshot response"}}},
						},
					},
				}); err != nil {
					t.Errorf("encode A2A response: %v", err)
				}
			}))
			defer upstream.Close()

			registry := NewRegistry(map[string]AgentConfig{
				"original":    {URL: upstream.URL + "/original"},
				"replacement": {URL: upstream.URL + "/replacement"},
			})
			provider := NewProvider(registry)
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			req := &models.ChatCompletionRequest{
				Model:    "a2a/original",
				Stream:   true,
				Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"original text"`)}},
			}

			registry.mu.Lock()
			chunks, errs := provider.StreamChatCompletion(ctx, req)
			if tc.changeModel {
				req.Model = "a2a/replacement"
			}
			if tc.replaceMessages {
				req.Messages = []models.Message{{Role: "user", Content: json.RawMessage(`"replacement text"`)}}
			}
			registry.mu.Unlock()

			var received []models.StreamChunk
			for chunks != nil || errs != nil {
				select {
				case chunk, ok := <-chunks:
					if !ok {
						chunks = nil
					} else {
						received = append(received, chunk)
					}
				case err, ok := <-errs:
					if !ok {
						errs = nil
					} else if err != nil {
						t.Errorf("unexpected stream error: %v", err)
					}
				case <-ctx.Done():
					t.Fatal("A2A stream did not terminate")
				}
			}

			select {
			case got := <-wire:
				if got.path != "/original" {
					t.Errorf("sent to agent path %q, want call-time path /original", got.path)
				}
				if got.method != MethodMessageSend || got.params.Message.Role != "user" {
					t.Errorf("unexpected A2A message: %+v", got)
				}
				parts := got.params.Message.Parts
				if len(parts) != 1 || parts[0].Type != "text" || parts[0].Text != "original text" {
					t.Errorf("sent message parts %+v, want call-time text %q", parts, "original text")
				}
			default:
				t.Fatal("no loopback A2A request")
			}
			if len(received) != 1 {
				t.Fatalf("received %d chunks, want 1", len(received))
			}
			chunk := received[0]
			if chunk.Model != "a2a/original" {
				t.Errorf("response model %q, want call-time model a2a/original", chunk.Model)
			}
			if chunk.ID != "a2a-snapshot-task" || chunk.Object != "chat.completion.chunk" || len(chunk.Choices) != 1 {
				t.Fatalf("unexpected response chunk: %+v", chunk)
			}
			choice := chunk.Choices[0]
			if choice.Delta.Content == nil || *choice.Delta.Content != "snapshot response" || choice.FinishReason == nil || *choice.FinishReason != "stop" {
				t.Errorf("unexpected response choice: %+v", choice)
			}
		})
	}
}
