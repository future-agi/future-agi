package openai

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

type w2B3Transport func(*http.Request) (*http.Response, error)

func (f w2B3Transport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func newW2B3OpenRouterProvider(t *testing.T) *Provider {
	t.Helper()
	p, err := New("test-openrouter", config.ProviderConfig{
		Type: "openrouter", BaseURL: "https://openrouter.invalid/api",
	})
	if err != nil {
		t.Fatal(err)
	}
	return p
}

// J31-01: replay exactly the assistant message a client receives, with its tool
// result. The synthetic transport checks the next upstream request; no network
// listener, provider credentials, or live model is used.
func TestW2B3OpenRouterToolRoundTrip(t *testing.T) {
	for _, representation := range []string{"extra_content", "reasoning_details", "both"} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%t", representation, stream), func(t *testing.T) {
				call := map[string]any{
					"id": "call_weather", "type": "function",
					"function": map[string]any{"name": "weather", "arguments": `{"city":"Paris"}`},
				}
				want := map[string]any{
					"role": "assistant", "content": nil, "tool_calls": []any{call},
				}
				if representation != "reasoning_details" {
					call["extra_content"] = map[string]any{
						"google": map[string]any{"thought_signature": "synthetic-signature", "future_metadata": "preserve"},
					}
				}
				if representation != "extra_content" {
					want["reasoning"] = "Check the weather."
					want["reasoning_details"] = []any{
						map[string]any{"type": "reasoning.text", "text": "Check the weather.", "index": float64(0)},
						map[string]any{"type": "reasoning.encrypted", "data": "synthetic-signature", "format": "google-gemini-v1", "id": "call_weather", "index": float64(1), "future_metadata": "preserve"},
					}
				}

				p := newW2B3OpenRouterProvider(t)
				t.Cleanup(func() { _ = p.Close() })
				requests := 0
				p.httpClient.Transport = w2B3Transport(func(r *http.Request) (*http.Response, error) {
					requests++
					var body map[string]any
					if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
						return nil, err
					}
					if body["model"] != "google/gemini-3.5-flash" {
						t.Errorf("upstream model = %v", body["model"])
					}
					if requests == 2 {
						messages := body["messages"].([]any)
						if len(messages) != 3 {
							return nil, fmt.Errorf("follow-up messages = %d, want 3", len(messages))
						}
						if !reflect.DeepEqual(messages[1], want) {
							t.Errorf("next upstream assistant = %#v, want preserved signature/reasoning %#v", messages[1], want)
						}
						tool := messages[2].(map[string]any)
						if tool["tool_call_id"] != "call_weather" || tool["content"] != "Sunny" {
							t.Errorf("tool result no longer references the original ID: %#v", tool)
						}
					}
					var response string
					if !stream {
						message := want
						if requests == 2 {
							message = map[string]any{"role": "assistant", "content": "Sunny"}
						}
						response = string(w2B3JSON(t, map[string]any{"id": "gen-test", "model": "google/gemini-3.5-flash", "choices": []any{map[string]any{"index": 0, "message": message, "finish_reason": "tool_calls"}}}))
					} else {
						deltas := []map[string]any{{"role": "assistant", "content": "Sunny"}}
						if requests == 1 {
							firstCall := map[string]any{"index": 0, "id": call["id"], "type": call["type"], "function": call["function"]}
							first := map[string]any{"role": "assistant", "tool_calls": []any{firstCall}}
							last := map[string]any{}
							if extra, ok := call["extra_content"]; ok {
								// Signature arrives later, without an ID or function delta.
								last["tool_calls"] = []any{map[string]any{"index": 0, "extra_content": extra}}
							}
							if details, ok := want["reasoning_details"]; ok {
								last["reasoning_details"] = details
								last["reasoning"] = want["reasoning"]
							}
							deltas = []map[string]any{first, last}
						}
						for _, delta := range deltas {
							response += "data: " + string(w2B3JSON(t, map[string]any{"id": "gen-test", "model": "google/gemini-3.5-flash", "choices": []any{map[string]any{"index": 0, "delta": delta}}})) + "\n\n"
						}
						response += "data: [DONE]\n\n"
					}
					return &http.Response{StatusCode: http.StatusOK, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(response))}, nil
				})

				req := &models.ChatCompletionRequest{Model: "google/gemini-3.5-flash", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Weather?"`)}}}
				message := w2B3ClientMessage(t, p, req, stream)
				if !reflect.DeepEqual(message, want) {
					t.Errorf("client assistant = %#v, want preserved signature/reasoning %#v", message, want)
				}
				var replay models.Message
				if err := json.Unmarshal(w2B3JSON(t, message), &replay); err != nil {
					t.Fatal(err)
				}
				req.Messages = append(req.Messages, replay, models.Message{Role: "tool", ToolCallID: "call_weather", Content: json.RawMessage(`"Sunny"`)})
				next := w2B3ClientMessage(t, p, req, stream)
				if next["content"] != "Sunny" || requests != 2 {
					t.Fatalf("continuation = %#v, upstream requests = %d", next, requests)
				}
			})
		}
	}
}

func w2B3JSON(t *testing.T, value any) []byte {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

// Simulate an OpenAI-compatible client consuming the actual re-encoded wire
// message/deltas. Accumulate tool fields by index, including a metadata-only
// delta after the ID/function delta.
func w2B3ClientMessage(t *testing.T, p *Provider, req *models.ChatCompletionRequest, stream bool) map[string]any {
	t.Helper()
	if !stream {
		resp, err := p.ChatCompletion(context.Background(), req)
		if err != nil {
			t.Fatal(err)
		}
		var wire map[string]any
		if err := json.Unmarshal(w2B3JSON(t, resp), &wire); err != nil {
			t.Fatal(err)
		}
		return wire["choices"].([]any)[0].(map[string]any)["message"].(map[string]any)
	}
	message := map[string]any{"content": nil}
	call := map[string]any{}
	chunks, errs := p.StreamChatCompletion(context.Background(), req)
	for chunk := range chunks {
		var wire map[string]any
		if err := json.Unmarshal(w2B3JSON(t, chunk), &wire); err != nil {
			t.Fatal(err)
		}
		for _, rawChoice := range wire["choices"].([]any) {
			delta := rawChoice.(map[string]any)["delta"].(map[string]any)
			for key, value := range delta {
				if key == "tool_calls" {
					for _, rawCall := range value.([]any) {
						for field, v := range rawCall.(map[string]any) {
							if field != "index" {
								call[field] = v
							}
						}
					}
					message[key] = []any{call}
				} else {
					message[key] = value
				}
			}
		}
	}
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	return message
}
