package bedrock

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// TestParseStreamPayload_ToolCallNameWire pins names to initial chunks and keeps
// each sequential tool's argument fragments intact without continuation names.
func TestParseStreamPayload_ToolCallNameWire(t *testing.T) {
	state := newBedrockStreamState("claude-3-sonnet")
	state.messageID = "msg_123"

	events := []struct {
		payload   string
		index     int
		id        string
		name      string
		arguments string
	}{
		{
			payload:   `{"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"call_weather","name":"get_weather","input":{}}}`,
			index:     0,
			id:        "call_weather",
			name:      "get_weather",
			arguments: "",
		},
		{
			payload:   `{"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"{\"city\":"}}`,
			index:     0,
			arguments: `{"city":`,
		},
		{
			payload:   `{"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"\"Paris\",\"unit\":\"celsius\"}"}}`,
			index:     0,
			arguments: `"Paris","unit":"celsius"}`,
		},
		{
			payload:   `{"type":"content_block_start","index":5,"content_block":{"type":"tool_use","id":"call_time","name":"get_time","input":{}}}`,
			index:     1,
			id:        "call_time",
			name:      "get_time",
			arguments: "",
		},
		{
			payload:   `{"type":"content_block_delta","index":5,"delta":{"type":"input_json_delta","partial_json":"{\"timezone\":"}}`,
			index:     1,
			arguments: `{"timezone":`,
		},
		{
			payload:   `{"type":"content_block_delta","index":5,"delta":{"type":"input_json_delta","partial_json":"\"Europe/Paris\"}"}}`,
			index:     1,
			arguments: `"Europe/Paris"}`,
		},
	}

	arguments := make(map[int]string)
	for i, event := range events {
		chunk, done, err := state.parseStreamPayload(&eventStreamMessage{
			Headers: map[string]string{":message-type": "event"},
			Payload: []byte(event.payload),
		})
		if err != nil {
			t.Fatalf("event %d: unexpected error: %v", i, err)
		}
		if done {
			t.Fatalf("event %d: tool call must not finish the stream", i)
		}
		arguments[event.index] += assertStreamToolCallNameWire(t, chunk, event.index, event.id, event.name, event.arguments)
	}

	for index, want := range []string{`{"city":"Paris","unit":"celsius"}`, `{"timezone":"Europe/Paris"}`} {
		if got := arguments[index]; got != want {
			t.Errorf("tool %d concatenated arguments = %q, want %q", index, got, want)
		}
	}
}

// TestParseConverseEvent_ToolCallNameWire pins initial names and verifies that
// interleaved tool continuations omit name while retaining their own arguments.
func TestParseConverseEvent_ToolCallNameWire(t *testing.T) {
	state := &converseStreamState{messageID: "msg_1", model: "model-1"}

	events := []struct {
		eventType string
		payload   string
		index     int
		id        string
		name      string
		arguments string
	}{
		{
			eventType: "contentBlockStart",
			payload:   `{"start":{"toolUse":{"toolUseId":"call_weather","name":"get_weather"}},"contentBlockIndex":3}`,
			index:     0,
			id:        "call_weather",
			name:      "get_weather",
			arguments: "",
		},
		{
			eventType: "contentBlockStart",
			payload:   `{"start":{"toolUse":{"toolUseId":"call_time","name":"get_time"}},"contentBlockIndex":7}`,
			index:     1,
			id:        "call_time",
			name:      "get_time",
			arguments: "",
		},
		{
			eventType: "contentBlockDelta",
			payload:   `{"delta":{"toolUse":{"input":"{\"city\":"}},"contentBlockIndex":3}`,
			index:     0,
			arguments: `{"city":`,
		},
		{
			eventType: "contentBlockDelta",
			payload:   `{"delta":{"toolUse":{"input":"{\"timezone\":"}},"contentBlockIndex":7}`,
			index:     1,
			arguments: `{"timezone":`,
		},
		{
			eventType: "contentBlockDelta",
			payload:   `{"delta":{"toolUse":{"input":"\"Paris\",\"unit\":\"celsius\"}"}},"contentBlockIndex":3}`,
			index:     0,
			arguments: `"Paris","unit":"celsius"}`,
		},
		{
			eventType: "contentBlockDelta",
			payload:   `{"delta":{"toolUse":{"input":"\"Europe/Paris\"}"}},"contentBlockIndex":7}`,
			index:     1,
			arguments: `"Europe/Paris"}`,
		},
	}

	arguments := make(map[int]string)
	for i, event := range events {
		chunk, done, err := state.parseConverseEvent(&eventStreamMessage{
			Headers: map[string]string{":message-type": "event", ":event-type": event.eventType},
			Payload: []byte(event.payload),
		})
		if err != nil {
			t.Fatalf("event %d: unexpected error: %v", i, err)
		}
		if done {
			t.Fatalf("event %d: tool call must not finish the stream", i)
		}
		arguments[event.index] += assertStreamToolCallNameWire(t, chunk, event.index, event.id, event.name, event.arguments)
	}

	for index, want := range []string{`{"city":"Paris","unit":"celsius"}`, `{"timezone":"Europe/Paris"}`} {
		if got := arguments[index]; got != want {
			t.Errorf("tool %d concatenated arguments = %q, want %q", index, got, want)
		}
	}
}

func assertStreamToolCallNameWire(t *testing.T, chunk *models.StreamChunk, wantIndex int, wantID, wantName, wantArguments string) string {
	t.Helper()
	if chunk == nil {
		t.Fatal("expected a tool call chunk")
	}
	wire, err := json.Marshal(chunk)
	if err != nil {
		t.Fatalf("marshal stream chunk: %v", err)
	}
	var decoded map[string]any
	if err := json.Unmarshal(wire, &decoded); err != nil {
		t.Fatalf("decode stream chunk JSON: %v", err)
	}
	choices, ok := decoded["choices"].([]any)
	if !ok || len(choices) != 1 {
		t.Fatalf("expected one choice in chunk: %s", wire)
	}
	choice, ok := choices[0].(map[string]any)
	if !ok {
		t.Fatalf("expected a choice object: %s", wire)
	}
	delta, ok := choice["delta"].(map[string]any)
	if !ok {
		t.Fatalf("expected a delta object: %s", wire)
	}
	calls, ok := delta["tool_calls"].([]any)
	if !ok || len(calls) != 1 {
		t.Fatalf("expected one tool call in chunk: %s", wire)
	}
	call, ok := calls[0].(map[string]any)
	if !ok {
		t.Fatalf("expected a tool call object: %s", wire)
	}
	if got := call["index"]; got != float64(wantIndex) {
		t.Fatalf("tool call index = %v, want %d: %s", got, wantIndex, wire)
	}
	fn, ok := call["function"].(map[string]any)
	if !ok {
		t.Fatalf("expected a function object: %s", wire)
	}
	if wantName != "" {
		if got := call["id"]; got != wantID {
			t.Errorf("initial tool %d id = %v, want %q", wantIndex, got, wantID)
		}
		if got := call["type"]; got != "function" {
			t.Errorf("initial tool %d type = %v, want function", wantIndex, got)
		}
		if got := fn["name"]; got != wantName {
			t.Errorf("initial tool %d name = %v, want %q", wantIndex, got, wantName)
		}
	} else {
		if _, ok := fn["name"]; ok {
			t.Errorf("continuation for tool %d must omit function.name: %s", wantIndex, wire)
		}
	}
	arguments, ok := fn["arguments"].(string)
	if !ok {
		t.Fatalf("tool %d must have a string arguments key, including when empty: %s", wantIndex, wire)
	}
	if arguments != wantArguments {
		t.Errorf("tool %d arguments = %q, want %q", wantIndex, arguments, wantArguments)
	}
	return arguments
}
