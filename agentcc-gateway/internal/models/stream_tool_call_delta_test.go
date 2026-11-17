package models

import (
	"encoding/json"
	"testing"
)

// streamToolCalls marshals a StreamChunk and returns the raw tool_calls
// entries of its first choice, each decoded into a key map, so that whether a
// key is present stays observable. A typed struct reads an absent name and an
// empty one as the same "".
func streamToolCalls(t *testing.T, chunk StreamChunk) []map[string]json.RawMessage {
	t.Helper()
	data, err := json.Marshal(chunk)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}
	var wire struct {
		Choices []struct {
			Delta struct {
				ToolCalls []map[string]json.RawMessage `json:"tool_calls"`
			} `json:"delta"`
		} `json:"choices"`
	}
	if err := json.Unmarshal(data, &wire); err != nil {
		t.Fatalf("Unmarshal error: %v (%s)", err, data)
	}
	if len(wire.Choices) != 1 {
		t.Fatalf("got %d choices, want 1: %s", len(wire.Choices), data)
	}
	return wire.Choices[0].Delta.ToolCalls
}

// functionKeys decodes the "function" object of one tool_calls entry.
func functionKeys(t *testing.T, call map[string]json.RawMessage) map[string]json.RawMessage {
	t.Helper()
	raw, ok := call["function"]
	if !ok {
		t.Fatalf("tool call has no function object: %v", call)
	}
	var fn map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fn); err != nil {
		t.Fatalf("function is not an object: %v (%s)", err, raw)
	}
	return fn
}

func jsonString(t *testing.T, raw json.RawMessage) string {
	t.Helper()
	var s string
	if err := json.Unmarshal(raw, &s); err != nil {
		t.Fatalf("not a JSON string: %v (%s)", err, raw)
	}
	return s
}

// An OpenAI-compatible upstream names a streamed tool call on its first delta
// only; every later delta carries an arguments fragment and no "name" key. The
// gateway decodes each chunk into a StreamChunk and encodes it again, and the
// encoded continuation must still have no "name" key. Sending "name":"" tells
// a client that accumulates the call that its name is now empty.
func TestStreamChunkToolCallContinuationOmitsName(t *testing.T) {
	tests := []struct {
		name         string
		upstream     string
		wantName     string // "" means the "name" key must be absent
		wantID       string
		wantType     string
		wantArgument string
	}{
		{
			name:         "first delta keeps name, id, type and its empty arguments",
			upstream:     `{"id":"c","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"lookup_city","arguments":""}}]},"finish_reason":null}]}`,
			wantName:     "lookup_city",
			wantID:       "call_1",
			wantType:     "function",
			wantArgument: "",
		},
		{
			name:         "arguments-only continuation has no name key",
			upstream:     `{"id":"c","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"city\":"}}]},"finish_reason":null}]}`,
			wantArgument: `{"city":`,
		},
		{
			name:         "a continuation an upstream sent with name \"\" goes out without the key",
			upstream:     `{"id":"c","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"name":"","arguments":"\"Paris\"}"}}]},"finish_reason":null}]}`,
			wantArgument: `"Paris"}`,
		},
		{
			name:         "an empty arguments fragment keeps its arguments key",
			upstream:     `{"id":"c","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":""}}]},"finish_reason":null}]}`,
			wantArgument: "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var chunk StreamChunk
			if err := json.Unmarshal([]byte(tt.upstream), &chunk); err != nil {
				t.Fatalf("Unmarshal upstream chunk: %v", err)
			}
			calls := streamToolCalls(t, chunk)
			if len(calls) != 1 {
				t.Fatalf("got %d tool calls, want 1", len(calls))
			}
			call := calls[0]
			fn := functionKeys(t, call)

			name, hasName := fn["name"]
			if tt.wantName == "" {
				if hasName {
					t.Errorf(`continuation function carries "name":%s; the key must be absent`, name)
				}
			} else if !hasName || jsonString(t, name) != tt.wantName {
				t.Errorf("function name = %s, want %q", name, tt.wantName)
			}

			args, hasArgs := fn["arguments"]
			if !hasArgs {
				t.Fatalf("function has no arguments key: %v", fn)
			}
			if got := jsonString(t, args); got != tt.wantArgument {
				t.Errorf("arguments = %q, want %q", got, tt.wantArgument)
			}

			for key, want := range map[string]string{"id": tt.wantID, "type": tt.wantType} {
				got, present := call[key]
				switch {
				case want == "" && present:
					t.Errorf("continuation carries %s %s; want it absent", key, got)
				case want != "" && (!present || jsonString(t, got) != want):
					t.Errorf("%s = %s, want %q", key, got, want)
				}
			}
		})
	}
}

// Two calls streamed in parallel interleave their deltas by index. Each
// call's continuations must keep their own fragments, and none may gain a name.
func TestStreamChunkParallelToolCallContinuationsOmitName(t *testing.T) {
	upstream := []string{
		`{"id":"c","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_a","type":"function","function":{"name":"lookup_city","arguments":""}},{"index":1,"id":"call_b","type":"function","function":{"name":"lookup_time","arguments":""}}]},"finish_reason":null}]}`,
		`{"id":"c","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"city\":"}}]},"finish_reason":null}]}`,
		`{"id":"c","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"function":{"arguments":"{\"zone\":"}}]},"finish_reason":null}]}`,
		`{"id":"c","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"Paris\"}"}},{"index":1,"function":{"arguments":"\"CET\"}"}}]},"finish_reason":null}]}`,
	}

	names := map[int]string{}
	args := map[int]string{}
	for i, raw := range upstream {
		var chunk StreamChunk
		if err := json.Unmarshal([]byte(raw), &chunk); err != nil {
			t.Fatalf("chunk %d: Unmarshal: %v", i, err)
		}
		for _, call := range streamToolCalls(t, chunk) {
			var index int
			if err := json.Unmarshal(call["index"], &index); err != nil {
				t.Fatalf("chunk %d: index: %v", i, err)
			}
			fn := functionKeys(t, call)
			name, hasName := fn["name"]
			if i == 0 {
				if !hasName {
					t.Fatalf("first delta of call %d lost its name", index)
				}
				names[index] = jsonString(t, name)
			} else if hasName {
				t.Errorf(`chunk %d: continuation of call %d carries "name":%s; the key must be absent`, i, index, name)
			}
			args[index] += jsonString(t, fn["arguments"])
		}
	}

	if names[0] != "lookup_city" || names[1] != "lookup_time" {
		t.Errorf("names = %v, want lookup_city and lookup_time", names)
	}
	if args[0] != `{"city":"Paris"}` {
		t.Errorf("call 0 arguments = %q", args[0])
	}
	if args[1] != `{"zone":"CET"}` {
		t.Errorf("call 1 arguments = %q", args[1])
	}
}

// A delta that names only the call's index and id has no function part, and
// none must be invented for it.
func TestStreamChunkToolCallWithoutFunctionStaysWithoutOne(t *testing.T) {
	var chunk StreamChunk
	raw := `{"id":"c","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_1"}]},"finish_reason":null}]}`
	if err := json.Unmarshal([]byte(raw), &chunk); err != nil {
		t.Fatalf("Unmarshal: %v", err)
	}
	calls := streamToolCalls(t, chunk)
	if len(calls) != 1 {
		t.Fatalf("got %d tool calls, want 1", len(calls))
	}
	if fn, ok := calls[0]["function"]; ok {
		t.Errorf("tool call gained a function object %s", fn)
	}
}

// A complete tool call, as a non-streamed response carries it, is a different
// type from a streamed delta and keeps its contract: "name" is always written.
func TestCompleteToolCallStillWritesName(t *testing.T) {
	for _, tc := range []struct {
		name string
		call ToolCall
		want string
	}{
		{name: "named call", call: ToolCall{ID: "call_1", Type: "function", Function: FunctionCall{Name: "lookup_city", Arguments: `{"city":"Paris"}`}}, want: `"lookup_city"`},
		{name: "a nameless complete call still writes the key", call: ToolCall{ID: "call_1", Type: "function", Function: FunctionCall{Arguments: `{}`}}, want: `""`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			message := decodeMessage(t, Choice{Message: Message{Role: "assistant", ToolCalls: []ToolCall{tc.call}}})
			var calls []map[string]json.RawMessage
			if err := json.Unmarshal(message["tool_calls"], &calls); err != nil || len(calls) != 1 {
				t.Fatalf("tool_calls = %s (err %v)", message["tool_calls"], err)
			}
			fn := functionKeys(t, calls[0])
			if got, ok := fn["name"]; !ok || string(got) != tc.want {
				t.Errorf("complete call name = %s (present %v), want %s", got, ok, tc.want)
			}
			if _, ok := fn["arguments"]; !ok {
				t.Errorf("complete call lost its arguments key: %v", fn)
			}
		})
	}
}
