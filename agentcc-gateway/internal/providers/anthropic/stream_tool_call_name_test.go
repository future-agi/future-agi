package anthropic

import (
	"encoding/json"
	"strings"
	"testing"
)

// A tool's initial wire delta carries its name; argument continuations omit the
// name key entirely and preserve every argument fragment, including the empty start.
func TestParseSSELines_ToolCallNameOnlyOnInitialWireChunk(t *testing.T) {
	const upstream = `event: message_start
data: {"type":"message_start","message":{"id":"msg_single_tool","type":"message","role":"assistant","model":"claude-opus-5-5","content":[],"usage":{"input_tokens":10,"output_tokens":1}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_weather","name":"get_weather","input":{}}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\"city\":"}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"\"Paris\","}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"\"unit\":\"celsius\"}"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"tool_use","stop_sequence":null},"usage":{"output_tokens":25}}

event: message_stop
data: {"type":"message_stop"}

`

	assertStreamToolCallNameWire(t, upstream, []streamToolCallNameWant{
		{
			id:        "toolu_weather",
			name:      "get_weather",
			fragments: []string{"", `{"city":`, `"Paris",`, `"unit":"celsius"}`},
			arguments: `{"city":"Paris","unit":"celsius"}`,
		},
	})
}

// Multiple calls keep separate wire indexes and argument streams, with names only
// on their initial deltas, even when a text block precedes the tool blocks.
func TestParseSSELines_MultipleToolCallNamesOnlyOnInitialWireChunks(t *testing.T) {
	// Anthropic delivers the two tool blocks sequentially within one response.
	const upstream = `event: message_start
data: {"type":"message_start","message":{"id":"msg_multiple_tools","type":"message","role":"assistant","model":"claude-opus-5-5","content":[],"usage":{"input_tokens":15,"output_tokens":1}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Checking weather and flights."}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: content_block_start
data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_weather","name":"get_weather","input":{}}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\"city\":"}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"\"London\","}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"\"unit\":\"celsius\"}"}}

event: content_block_stop
data: {"type":"content_block_stop","index":1}

event: content_block_start
data: {"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"toolu_flights","name":"lookup_flights","input":{}}}

event: content_block_delta
data: {"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"{\"origin\":"}}

event: content_block_delta
data: {"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"\"LHR\",\"destination\":"}}

event: content_block_delta
data: {"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"\"SFO\"}"}}

event: content_block_stop
data: {"type":"content_block_stop","index":2}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"tool_use","stop_sequence":null},"usage":{"output_tokens":50}}

event: message_stop
data: {"type":"message_stop"}

`

	assertStreamToolCallNameWire(t, upstream, []streamToolCallNameWant{
		{
			id:        "toolu_weather",
			name:      "get_weather",
			fragments: []string{"", `{"city":`, `"London",`, `"unit":"celsius"}`},
			arguments: `{"city":"London","unit":"celsius"}`,
		},
		{
			id:        "toolu_flights",
			name:      "lookup_flights",
			fragments: []string{"", `{"origin":`, `"LHR","destination":`, `"SFO"}`},
			arguments: `{"origin":"LHR","destination":"SFO"}`,
		},
	})
}

type streamToolCallNameWant struct {
	id        string
	name      string
	fragments []string
	arguments string
}

func assertStreamToolCallNameWire(t *testing.T, upstream string, want []streamToolCallNameWant) {
	t.Helper()

	state := &streamState{}
	chunks, done, err := parseSSELines(strings.Split(upstream, "\n"), state)
	if err != nil {
		t.Fatalf("parseSSELines: %v", err)
	}
	if !done {
		t.Fatal("expected done=true after message_stop")
	}
	if len(chunks) == 0 {
		t.Fatal("stream emitted no chunks")
	}

	seen := make([]int, len(want))
	arguments := make([]string, len(want))
	for chunkIndex, chunk := range chunks {
		wire, err := json.Marshal(chunk)
		if err != nil {
			t.Fatalf("marshal chunk %d: %v", chunkIndex, err)
		}
		var decoded map[string]any
		if err := json.Unmarshal(wire, &decoded); err != nil {
			t.Fatalf("decode chunk %d: %v", chunkIndex, err)
		}
		choices, ok := decoded["choices"].([]any)
		if !ok || len(choices) != 1 {
			t.Fatalf("chunk %d: expected one choice, got %s", chunkIndex, wire)
		}
		choice, ok := choices[0].(map[string]any)
		if !ok {
			t.Fatalf("chunk %d: choice is not an object: %s", chunkIndex, wire)
		}
		delta, ok := choice["delta"].(map[string]any)
		if !ok {
			t.Fatalf("chunk %d: delta is not an object: %s", chunkIndex, wire)
		}
		if chunkIndex == 0 && delta["role"] != "assistant" {
			t.Errorf("initial chunk role = %v, want assistant", delta["role"])
		}
		if chunkIndex == len(chunks)-1 && choice["finish_reason"] != "tool_calls" {
			t.Errorf("final chunk finish_reason = %v, want tool_calls", choice["finish_reason"])
		}
		rawCalls, ok := delta["tool_calls"]
		if !ok {
			continue
		}
		calls, ok := rawCalls.([]any)
		if !ok || len(calls) != 1 {
			t.Fatalf("chunk %d: expected one tool call, got %s", chunkIndex, wire)
		}
		call, ok := calls[0].(map[string]any)
		if !ok {
			t.Fatalf("chunk %d: tool call is not an object: %s", chunkIndex, wire)
		}
		wireIndex, ok := call["index"].(float64)
		if !ok || wireIndex < 0 || wireIndex >= float64(len(want)) || wireIndex != float64(int(wireIndex)) {
			t.Fatalf("chunk %d: unexpected tool index %v: %s", chunkIndex, call["index"], wire)
		}
		index := int(wireIndex)
		fn, ok := call["function"].(map[string]any)
		if !ok {
			t.Fatalf("chunk %d, tool %d: function is not an object: %s", chunkIndex, index, wire)
		}
		fragmentIndex := seen[index]
		if fragmentIndex >= len(want[index].fragments) {
			t.Fatalf("chunk %d, tool %d: unexpected extra delta: %s", chunkIndex, index, wire)
		}
		if fragmentIndex == 0 {
			if fn["name"] != want[index].name {
				t.Errorf("tool %d: initial function.name = %v, want %q", index, fn["name"], want[index].name)
			}
			if call["id"] != want[index].id {
				t.Errorf("tool %d: initial id = %v, want %q", index, call["id"], want[index].id)
			}
			if call["type"] != "function" {
				t.Errorf("tool %d: initial type = %v, want function", index, call["type"])
			}
		} else if _, ok := fn["name"]; ok {
			t.Errorf("tool %d, continuation %d: function.name must be absent, got %s", index, fragmentIndex, wire)
		}
		fragment, ok := fn["arguments"].(string)
		if !ok {
			t.Fatalf("tool %d, delta %d: function.arguments must be present as a string: %s", index, fragmentIndex, wire)
		}
		if fragment != want[index].fragments[fragmentIndex] {
			t.Errorf("tool %d, delta %d: arguments = %q, want %q", index, fragmentIndex, fragment, want[index].fragments[fragmentIndex])
		}
		arguments[index] += fragment
		seen[index]++
	}

	for index, expected := range want {
		if seen[index] != len(expected.fragments) {
			t.Errorf("tool %d: got %d deltas, want %d", index, seen[index], len(expected.fragments))
		}
		if arguments[index] != expected.arguments {
			t.Errorf("tool %d: concatenated arguments = %q, want %q", index, arguments[index], expected.arguments)
		}
		if !json.Valid([]byte(arguments[index])) {
			t.Errorf("tool %d: concatenated arguments are not valid JSON: %q", index, arguments[index])
		}
	}
}
