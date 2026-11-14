package openai

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

type streamToolCallNameDelta struct {
	index     int
	id        string
	name      string
	arguments string
}

// TestStreamChatCompletion_ToolCallNameWire pins names to initial tool deltas,
// with absent name keys and lossless arguments on interleaved continuations.
func TestStreamChatCompletion_ToolCallNameWire(t *testing.T) {
	tests := []struct {
		name          string
		events        []string
		want          []streamToolCallNameDelta
		wantArguments map[int]string
	}{
		{
			name: "single tool call",
			events: []string{
				`{"id":"chatcmpl-tools","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_weather","type":"function","function":{"name":"get_weather","arguments":""}}]},"finish_reason":null}]}`,
				`{"id":"chatcmpl-tools","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"city\":"}}]},"finish_reason":null}]}`,
				`{"id":"chatcmpl-tools","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"Paris\"}"}}]},"finish_reason":null}]}`,
				`{"id":"chatcmpl-tools","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}`,
				`[DONE]`,
			},
			want: []streamToolCallNameDelta{
				{index: 0, id: "call_weather", name: "get_weather", arguments: ""},
				{index: 0, arguments: `{"city":`},
				{index: 0, arguments: `"Paris"}`},
			},
			wantArguments: map[int]string{0: `{"city":"Paris"}`},
		},
		{
			name: "parallel tool calls with interleaved arguments",
			events: []string{
				`{"id":"chatcmpl-tools","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_weather","type":"function","function":{"name":"get_weather","arguments":""}},{"index":1,"id":"call_time","type":"function","function":{"name":"get_time","arguments":""}}]},"finish_reason":null}]}`,
				`{"id":"chatcmpl-tools","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"city\":"}}]},"finish_reason":null}]}`,
				`{"id":"chatcmpl-tools","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"function":{"arguments":"{\"timezone\":"}}]},"finish_reason":null}]}`,
				`{"id":"chatcmpl-tools","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"Paris\"}"}}]},"finish_reason":null}]}`,
				`{"id":"chatcmpl-tools","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"function":{"arguments":"\"UTC\"}"}}]},"finish_reason":null}]}`,
				`{"id":"chatcmpl-tools","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}`,
				`[DONE]`,
			},
			want: []streamToolCallNameDelta{
				{index: 0, id: "call_weather", name: "get_weather", arguments: ""},
				{index: 1, id: "call_time", name: "get_time", arguments: ""},
				{index: 0, arguments: `{"city":`},
				{index: 1, arguments: `{"timezone":`},
				{index: 0, arguments: `"Paris"}`},
				{index: 1, arguments: `"UTC"}`},
			},
			wantArguments: map[int]string{0: `{"city":"Paris"}`, 1: `{"timezone":"UTC"}`},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
					t.Errorf("request = %s %s, want POST /v1/chat/completions", r.Method, r.URL.Path)
				}
				w.Header().Set("Content-Type", "text/event-stream")
				for _, event := range tt.events {
					fmt.Fprintf(w, "data: %s\n\n", event)
					w.(http.Flusher).Flush()
				}
			}))
			defer ts.Close()

			p := newTestProvider(t, ts.URL)
			defer p.Close()
			chunks, err := collect(p.StreamChatCompletion(context.Background(), &models.ChatCompletionRequest{
				Model:    "gpt-4o",
				Stream:   true,
				Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Check the weather and time."`)}},
			}))
			if err != nil {
				t.Fatalf("stream error: %v", err)
			}
			assertStreamToolCallNameWire(t, chunks, tt.want, tt.wantArguments)
		})
	}
}

// TestStreamChatCompletionViaResponses_ToolCallNameWire pins the same wire
// contract after Responses events are converted, including parallel calls.
func TestStreamChatCompletionViaResponses_ToolCallNameWire(t *testing.T) {
	tests := []struct {
		name          string
		events        []string
		want          []streamToolCallNameDelta
		wantArguments map[int]string
	}{
		{
			name:   "single tool call",
			events: toolStreamEvents,
			want: []streamToolCallNameDelta{
				{index: 0, id: "call_2", name: "get_weather", arguments: ""},
				{index: 0, arguments: `{"city":`},
				{index: 0, arguments: `"Paris"}`},
			},
			wantArguments: map[int]string{0: `{"city":"Paris"}`},
		},
		{
			name: "parallel tool calls with interleaved arguments",
			events: []string{
				`{"type":"response.created","sequence_number":0,"response":{"id":"resp_parallel","object":"response","created_at":1761580000,"status":"in_progress","model":"gpt-5.6-terra","output":[],"usage":null}}`,
				`{"type":"response.output_item.added","sequence_number":1,"output_index":0,"item":{"id":"rs_parallel","type":"reasoning","summary":[]}}`,
				`{"type":"response.output_item.done","sequence_number":2,"output_index":0,"item":{"id":"rs_parallel","type":"reasoning","summary":[]}}`,
				`{"type":"response.output_item.added","sequence_number":3,"output_index":1,"item":{"id":"fc_weather","type":"function_call","status":"in_progress","call_id":"call_weather","name":"get_weather","arguments":""}}`,
				`{"type":"response.output_item.added","sequence_number":4,"output_index":2,"item":{"id":"fc_time","type":"function_call","status":"in_progress","call_id":"call_time","name":"get_time","arguments":""}}`,
				`{"type":"response.function_call_arguments.delta","sequence_number":5,"item_id":"fc_weather","output_index":1,"delta":"{\"city\":"}`,
				`{"type":"response.function_call_arguments.delta","sequence_number":6,"item_id":"fc_time","output_index":2,"delta":"{\"timezone\":"}`,
				`{"type":"response.function_call_arguments.delta","sequence_number":7,"item_id":"fc_weather","output_index":1,"delta":"\"Paris\"}"}`,
				`{"type":"response.function_call_arguments.delta","sequence_number":8,"item_id":"fc_time","output_index":2,"delta":"\"UTC\"}"}`,
				`{"type":"response.function_call_arguments.done","sequence_number":9,"item_id":"fc_weather","output_index":1,"arguments":"{\"city\":\"Paris\"}"}`,
				`{"type":"response.output_item.done","sequence_number":10,"output_index":1,"item":{"id":"fc_weather","type":"function_call","status":"completed","call_id":"call_weather","name":"get_weather","arguments":"{\"city\":\"Paris\"}"}}`,
				`{"type":"response.function_call_arguments.done","sequence_number":11,"item_id":"fc_time","output_index":2,"arguments":"{\"timezone\":\"UTC\"}"}`,
				`{"type":"response.output_item.done","sequence_number":12,"output_index":2,"item":{"id":"fc_time","type":"function_call","status":"completed","call_id":"call_time","name":"get_time","arguments":"{\"timezone\":\"UTC\"}"}}`,
				`{"type":"response.completed","sequence_number":13,"response":{"id":"resp_parallel","object":"response","created_at":1761580000,"status":"completed","model":"gpt-5.6-terra","output":[{"id":"rs_parallel","type":"reasoning","summary":[]},{"id":"fc_weather","type":"function_call","status":"completed","call_id":"call_weather","name":"get_weather","arguments":"{\"city\":\"Paris\"}"},{"id":"fc_time","type":"function_call","status":"completed","call_id":"call_time","name":"get_time","arguments":"{\"timezone\":\"UTC\"}"}],"usage":{"input_tokens":12,"output_tokens":8,"total_tokens":20}}}`,
			},
			want: []streamToolCallNameDelta{
				{index: 0, id: "call_weather", name: "get_weather", arguments: ""},
				{index: 1, id: "call_time", name: "get_time", arguments: ""},
				{index: 0, arguments: `{"city":`},
				{index: 1, arguments: `{"timezone":`},
				{index: 0, arguments: `"Paris"}`},
				{index: 1, arguments: `"UTC"}`},
			},
			wantArguments: map[int]string{0: `{"city":"Paris"}`, 1: `{"timezone":"UTC"}`},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ts := fakeOpenAI(t, func(w http.ResponseWriter, r *http.Request) {
				sse(w, tt.events...)
			})
			defer ts.Close()
			p := newTestProvider(t, ts.URL)
			defer p.Close()

			chunks, err := collect(p.StreamChatCompletionViaResponses(context.Background(), toolTurnRequest("gpt-5.6-terra")))
			if err != nil {
				t.Fatalf("stream error: %v", err)
			}
			assertStreamToolCallNameWire(t, chunks, tt.want, tt.wantArguments)
		})
	}
}

// assertStreamToolCallNameWire checks re-marshaled chunks rather than Go field
// values, so an absent name is distinguishable from an authoritative empty one.
func assertStreamToolCallNameWire(t *testing.T, chunks []models.StreamChunk, want []streamToolCallNameDelta, wantArguments map[int]string) {
	t.Helper()
	seen := 0
	argumentsByIndex := make(map[int]string)
	for chunkIndex, chunk := range chunks {
		data, err := json.Marshal(chunk)
		if err != nil {
			t.Fatalf("marshal chunk %d: %v", chunkIndex, err)
		}
		var wire map[string]json.RawMessage
		if err := json.Unmarshal(data, &wire); err != nil {
			t.Fatalf("decode chunk %d: %v", chunkIndex, err)
		}
		var choices []struct {
			Delta map[string]json.RawMessage `json:"delta"`
		}
		if err := json.Unmarshal(wire["choices"], &choices); err != nil {
			t.Fatalf("decode choices in chunk %d: %v", chunkIndex, err)
		}
		for _, choice := range choices {
			rawCalls, ok := choice.Delta["tool_calls"]
			if !ok {
				continue
			}
			var calls []map[string]any
			if err := json.Unmarshal(rawCalls, &calls); err != nil {
				t.Fatalf("decode tool calls in chunk %d: %v", chunkIndex, err)
			}
			for _, call := range calls {
				if seen >= len(want) {
					t.Fatalf("unexpected tool delta in chunk %d: %s", chunkIndex, data)
				}
				expected := want[seen]
				seen++
				if call["index"] != float64(expected.index) {
					t.Fatalf("tool delta %d index = %v, want %d", seen-1, call["index"], expected.index)
				}
				fn, ok := call["function"].(map[string]any)
				if !ok {
					t.Fatalf("chunk %d tool %d has no function object: %s", chunkIndex, expected.index, data)
				}
				if expected.name != "" {
					if call["id"] != expected.id || call["type"] != "function" || fn["name"] != expected.name {
						t.Errorf("initial tool %d = %v, want id %q, type function, name %q", expected.index, call, expected.id, expected.name)
					}
				} else if _, ok := fn["name"]; ok {
					t.Errorf("chunk %d tool %d continuation must omit function.name, got %s", chunkIndex, expected.index, data)
				}
				fragment, ok := fn["arguments"].(string)
				if !ok {
					t.Fatalf("chunk %d tool %d must contain a string arguments key: %s", chunkIndex, expected.index, data)
				}
				if fragment != expected.arguments {
					t.Errorf("chunk %d tool %d arguments = %q, want %q", chunkIndex, expected.index, fragment, expected.arguments)
				}
				argumentsByIndex[expected.index] += fragment
			}
		}
	}
	if seen != len(want) {
		t.Errorf("received %d tool deltas, want %d", seen, len(want))
	}
	for index, fullArguments := range wantArguments {
		if got := argumentsByIndex[index]; got != fullArguments {
			t.Errorf("tool %d concatenated arguments = %q, want %q", index, got, fullArguments)
		}
	}
}
