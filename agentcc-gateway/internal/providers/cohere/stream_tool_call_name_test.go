package cohere

import (
	"encoding/json"
	"testing"
)

// TestParseStreamData_ToolCallNameWireContract pins the initial name and requires
// argument-only continuation chunks to omit name without losing argument bytes.
func TestParseStreamData_ToolCallNameWireContract(t *testing.T) {
	assertToolCallNameWireStream(t, []toolCallNameWireEvent{
		{
			data:      `{"type":"tool-call-start","index":0,"delta":{"message":{"tool_calls":{"id":"call-weather","type":"function","function":{"name":"get_weather","arguments":""}}}}}`,
			index:     0,
			id:        "call-weather",
			name:      "get_weather",
			arguments: "",
		},
		{
			data:      `{"type":"tool-call-delta","index":0,"delta":{"message":{"tool_calls":{"function":{"arguments":"{\"loc"}}}}}`,
			index:     0,
			arguments: `{"loc`,
		},
		{
			data:      `{"type":"tool-call-delta","index":0,"delta":{"message":{"tool_calls":{"function":{"arguments":"ation\":\"Paris\"}"}}}}}`,
			index:     0,
			arguments: `ation":"Paris"}`,
		},
	}, map[int]string{
		0: `{"location":"Paris"}`,
	})
}

// TestParseStreamData_MultipleToolCallNamesWireContract pins independent indexes,
// initial names, and name-free argument continuations for two calls in one stream.
func TestParseStreamData_MultipleToolCallNamesWireContract(t *testing.T) {
	// Cohere's parser associates deltas with the most recently started call,
	// so stream the two calls sequentially rather than interleaving their deltas.
	assertToolCallNameWireStream(t, []toolCallNameWireEvent{
		{
			data:      `{"type":"tool-call-start","index":0,"delta":{"message":{"tool_calls":{"id":"call-weather","type":"function","function":{"name":"get_weather","arguments":""}}}}}`,
			index:     0,
			id:        "call-weather",
			name:      "get_weather",
			arguments: "",
		},
		{
			data:      `{"type":"tool-call-delta","index":0,"delta":{"message":{"tool_calls":{"function":{"arguments":"{\"location\":"}}}}}`,
			index:     0,
			arguments: `{"location":`,
		},
		{
			data:      `{"type":"tool-call-delta","index":0,"delta":{"message":{"tool_calls":{"function":{"arguments":"\"Paris\"}"}}}}}`,
			index:     0,
			arguments: `"Paris"}`,
		},
		{
			data:      `{"type":"tool-call-start","index":1,"delta":{"message":{"tool_calls":{"id":"call-time","type":"function","function":{"name":"get_time","arguments":"{\"timezone\":"}}}}}`,
			index:     1,
			id:        "call-time",
			name:      "get_time",
			arguments: `{"timezone":`,
		},
		{
			data:      `{"type":"tool-call-delta","index":1,"delta":{"message":{"tool_calls":{"function":{"arguments":"\"Europe/"}}}}}`,
			index:     1,
			arguments: `"Europe/`,
		},
		{
			data:      `{"type":"tool-call-delta","index":1,"delta":{"message":{"tool_calls":{"function":{"arguments":"Paris\"}"}}}}}`,
			index:     1,
			arguments: `Paris"}`,
		},
	}, map[int]string{
		0: `{"location":"Paris"}`,
		1: `{"timezone":"Europe/Paris"}`,
	})
}

type toolCallNameWireEvent struct {
	data      string
	index     int
	id        string
	name      string
	arguments string
}

func assertToolCallNameWireStream(t *testing.T, events []toolCallNameWireEvent, wantArguments map[int]string) {
	t.Helper()
	state := newStreamState("command-r-plus")
	argumentsByIndex := make(map[int]string)

	for eventIndex, event := range events {
		chunk, done, err := state.parseStreamData(event.data)
		if err != nil {
			t.Fatalf("event %d: parseStreamData: %v", eventIndex, err)
		}
		if done {
			t.Fatalf("event %d: tool-call event unexpectedly ended the stream", eventIndex)
		}
		if chunk == nil {
			t.Fatalf("event %d: expected a tool-call chunk", eventIndex)
		}

		wire, err := json.Marshal(chunk)
		if err != nil {
			t.Fatalf("event %d: marshal chunk: %v", eventIndex, err)
		}
		var decoded map[string]any
		if err := json.Unmarshal(wire, &decoded); err != nil {
			t.Fatalf("event %d: decode chunk JSON: %v", eventIndex, err)
		}
		choices, ok := decoded["choices"].([]any)
		if !ok || len(choices) != 1 {
			t.Fatalf("event %d: expected one choice in %s", eventIndex, wire)
		}
		choice, ok := choices[0].(map[string]any)
		if !ok {
			t.Fatalf("event %d: expected a choice object in %s", eventIndex, wire)
		}
		delta, ok := choice["delta"].(map[string]any)
		if !ok {
			t.Fatalf("event %d: expected a delta object in %s", eventIndex, wire)
		}
		toolCalls, ok := delta["tool_calls"].([]any)
		if !ok || len(toolCalls) != 1 {
			t.Fatalf("event %d: expected one tool-call delta in %s", eventIndex, wire)
		}
		call, ok := toolCalls[0].(map[string]any)
		if !ok {
			t.Fatalf("event %d: expected a tool-call object in %s", eventIndex, wire)
		}
		index, ok := call["index"].(float64)
		if !ok || index != float64(event.index) {
			t.Fatalf("event %d: index = %v, want %d; chunk: %s", eventIndex, call["index"], event.index, wire)
		}
		fn, ok := call["function"].(map[string]any)
		if !ok {
			t.Fatalf("event %d: expected a function object in %s", eventIndex, wire)
		}

		if event.name != "" {
			if fn["name"] != event.name {
				t.Errorf("event %d: initial function.name = %v, want %q", eventIndex, fn["name"], event.name)
			}
			if call["id"] != event.id {
				t.Errorf("event %d: initial id = %v, want %q", eventIndex, call["id"], event.id)
			}
			if call["type"] != "function" {
				t.Errorf("event %d: initial type = %v, want function", eventIndex, call["type"])
			}
		} else if _, ok := fn["name"]; ok {
			t.Errorf("event %d: argument-only continuation must omit function.name; chunk: %s", eventIndex, wire)
		}

		arguments, ok := fn["arguments"].(string)
		if !ok {
			t.Fatalf("event %d: function must contain an arguments string, even when empty; chunk: %s", eventIndex, wire)
		}
		if arguments != event.arguments {
			t.Errorf("event %d: function.arguments = %q, want %q", eventIndex, arguments, event.arguments)
		}
		argumentsByIndex[int(index)] += arguments
	}

	if len(argumentsByIndex) != len(wantArguments) {
		t.Fatalf("argument indexes: got %d, want %d", len(argumentsByIndex), len(wantArguments))
	}
	for index, want := range wantArguments {
		if got := argumentsByIndex[index]; got != want {
			t.Errorf("tool index %d: concatenated arguments = %q, want %q", index, got, want)
		}
	}
}
