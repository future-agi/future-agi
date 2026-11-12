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

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Consume and replay the real adapter's wire output. Only the
// first parallel call is signed; neither IDs nor signature scope may change.
func TestW2B3ParallelOpenRouterRoundTrip(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, managed := range []bool{false, true} {
			t.Run(fmt.Sprintf("stream=%t/managed=%t", stream, managed), func(t *testing.T) {
				const model = "google/gemini-3.5-flash"
				const extra = `{"google":{"thought_signature":"synthetic-parallel-signature","future_field":"preserve"}}`
				const assistant = `{"role":"assistant","content":null,"tool_calls":[{"id":"call_weather","type":"function","function":{"name":"weather","arguments":"{}"},"extra_content":` + extra + `},{"id":"call_time","type":"function","function":{"name":"time","arguments":"{}"}}]}`
				var want map[string]any
				if err := json.Unmarshal([]byte(assistant), &want); err != nil {
					t.Fatal(err)
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
					if requests == 2 {
						messages := body["messages"].([]any)
						if len(messages) != 4 || !reflect.DeepEqual(messages[1], want) {
							t.Errorf("replayed assistant changed: %#v", messages)
						}
						for i, id := range []string{"call_weather", "call_time"} {
							if messages[i+2].(map[string]any)["tool_call_id"] != id {
								t.Errorf("tool result %d lost original id", i)
							}
						}
					}
					response := `{"id":"gen-parallel","model":"` + model + `","choices":[{"index":0,"message":` + assistant + `,"finish_reason":"tool_calls"}]}`
					if requests == 2 {
						response = `{"choices":[{"message":{"role":"assistant","content":"Done"},"finish_reason":"stop"}]}`
					}
					if stream {
						deltas := []string{
							`{"role":"assistant","tool_calls":[{"index":0,"id":"call_weather","type":"function","function":{"name":"weather","arguments":"{}"}},{"index":1,"id":"call_time","type":"function","function":{"name":"time","arguments":"{}"}}]}`,
							`{"tool_calls":[{"index":0,"extra_content":` + extra + `}]}`,
						}
						if requests == 2 {
							deltas = []string{`{"role":"assistant","content":"Done"}`}
						}
						response = ""
						for _, delta := range deltas {
							response += "data: {\"id\":\"gen-parallel\",\"model\":\"" + model + "\",\"choices\":[{\"index\":0,\"delta\":" + delta + "}]}\n\n"
						}
						response += "data: [DONE]\n\n"
					}
					return &http.Response{StatusCode: http.StatusOK, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(response))}, nil
				})
				req := &models.ChatCompletionRequest{Model: model, Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Weather and time?"`)}}}
				var message map[string]any
				if !stream {
					message = w2B3ClientMessage(t, p, req, false)
				} else {
					message = map[string]any{"content": nil}
					calls := []any{map[string]any{}, map[string]any{}}
					chunks, errs := p.StreamChatCompletion(context.Background(), req)
					for chunk := range chunks {
						var wire map[string]any
						if err := json.Unmarshal(w2B3JSON(t, chunk), &wire); err != nil {
							t.Fatal(err)
						}
						for _, choice := range wire["choices"].([]any) {
							delta := choice.(map[string]any)["delta"].(map[string]any)
							for key, value := range delta {
								if key != "tool_calls" {
									message[key] = value
									continue
								}
								for _, rawCall := range value.([]any) {
									call := rawCall.(map[string]any)
									index := int(call["index"].(float64))
									if index < 0 || index >= len(calls) {
										t.Fatalf("unexpected call index %d", index)
									}
									for field, v := range call {
										if field != "index" {
											calls[index].(map[string]any)[field] = v
										}
									}
								}
								message["tool_calls"] = calls
							}
						}
					}
					for err := range errs {
						if err != nil {
							t.Fatal(err)
						}
					}
				}
				if !reflect.DeepEqual(message, want) {
					t.Fatalf("parallel wire reply changed: %#v, want %#v", message, want)
				}
				var replay models.Message
				if err := json.Unmarshal(w2B3JSON(t, message), &replay); err != nil {
					t.Fatal(err)
				}
				req.Messages = append(req.Messages, replay,
					models.Message{Role: "tool", ToolCallID: "call_weather", Content: json.RawMessage(`"Sunny"`)},
					models.Message{Role: "tool", ToolCallID: "call_time", Content: json.RawMessage(`"Noon"`)})
				in := eligibility.Input{Model: model, ProviderID: "custom-router", Aggregator: true, Managed: managed, Request: req,
					Org: &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{Enabled: true}}}
				if got := eligibility.Check(&in); !got.OK {
					t.Fatalf("gateway rejected its own parallel reply: code=%s reason=%s", got.Code, got.Reason)
				}
				next := w2B3ClientMessage(t, p, req, stream)
				if next["content"] != "Done" || requests != 2 {
					t.Fatalf("continuation=%#v requests=%d", next, requests)
				}
			})
		}
	}
}
