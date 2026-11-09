package ext

import (
	"bufio"
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mockup/mock"
)

// toolsBody is a request offering two tools, in the chat or the Anthropic shape.
func toolsBody(model string, stream, anthropic bool) string {
	tool := func(name string) map[string]any {
		if anthropic {
			return map[string]any{"name": name, "input_schema": map[string]any{"type": "object"}}
		}
		return map[string]any{"type": "function", "function": map[string]any{"name": name}}
	}
	b, _ := json.Marshal(map[string]any{"model": model, "stream": stream, "tools": []any{tool("search"), tool("fetch")},
		"messages": []any{map[string]any{"role": "user", "content": "hi"}}})
	return string(b)
}

// sseData splits a complete stream into its data payloads, failing on a
// stream that does not frame every event as "event:"/"data:" lines and a
// blank line.
func sseData(t *testing.T, b []byte) []string {
	t.Helper()
	var out []string
	pending := ""
	sc := bufio.NewScanner(bytes.NewReader(b))
	for sc.Scan() {
		line := sc.Text()
		switch {
		case line == "":
			if pending == "" {
				t.Fatalf("an event without data:\n%s", b)
			}
			out, pending = append(out, pending), ""
		case strings.HasPrefix(line, "data: "):
			pending = strings.TrimPrefix(line, "data: ")
		case strings.HasPrefix(line, "event: "):
		default:
			t.Fatalf("malformed SSE line %q:\n%s", line, b)
		}
	}
	if pending != "" {
		t.Fatalf("the stream ends mid-event:\n%s", b)
	}
	return out
}

type streamedCall struct {
	id, name  string
	args      strings.Builder
	argEvents int
}

// chatStreamCalls rebuilds the tool calls of a chat stream from its deltas.
func chatStreamCalls(t *testing.T, events []string) ([]*streamedCall, string) {
	t.Helper()
	var calls []*streamedCall
	finish := ""
	for _, data := range events {
		if data == "[DONE]" {
			continue
		}
		var c struct {
			Choices []struct {
				FinishReason *string `json:"finish_reason"`
				Delta        struct {
					ToolCalls []struct {
						Index    int    `json:"index"`
						ID       string `json:"id"`
						Function struct {
							Name      *string `json:"name"`
							Arguments string  `json:"arguments"`
						} `json:"function"`
					} `json:"tool_calls"`
				} `json:"delta"`
			} `json:"choices"`
		}
		if err := json.Unmarshal([]byte(data), &c); err != nil {
			t.Fatalf("chunk %s: %v", data, err)
		}
		for _, ch := range c.Choices {
			if ch.FinishReason != nil {
				finish = *ch.FinishReason
			}
			for _, d := range ch.Delta.ToolCalls {
				if d.Function.Name != nil { // a call's head
					if d.Index != len(calls) {
						t.Fatalf("call head at index %d, want %d", d.Index, len(calls))
					}
					calls = append(calls, &streamedCall{id: d.ID, name: *d.Function.Name})
					continue
				}
				if d.Index >= len(calls) {
					t.Fatalf("argument delta for call %d before its head", d.Index)
				}
				calls[d.Index].args.WriteString(d.Function.Arguments)
				calls[d.Index].argEvents++
			}
		}
	}
	return calls, finish
}

func TestStreamToolsStreamsNCallsAsArgumentDeltas(t *testing.T) {
	m := newExtMock(t)
	status, b := m.chat(t, "key-1", toolsBody("test/model-a", true, false), mock.BehaviorHeader, "stream_tools:3")
	if status != http.StatusOK {
		t.Fatalf("status %d body %s", status, b)
	}
	events := sseData(t, b)
	if events[len(events)-1] != "[DONE]" {
		t.Fatalf("the stream does not end with [DONE]: %s", b)
	}
	calls, finish := chatStreamCalls(t, events)
	if len(calls) != 3 || finish != "tool_calls" {
		t.Fatalf("%d calls, finish %q; want 3, tool_calls", len(calls), finish)
	}
	for i, c := range calls {
		wantName := []string{"search", "fetch", "search"}[i]
		if c.id != fmt.Sprintf("call_mock_%d", i+1) || c.name != wantName ||
			c.args.String() != fmt.Sprintf(`{"query":"mock-%d"}`, i+1) || c.argEvents < 2 {
			t.Errorf("call %d = %s %s(%s) in %d argument deltas", i, c.id, c.name, c.args.String(), c.argEvents)
		}
	}

	// Unstreamed, the same calls are in the body, with no text.
	_, b = m.chat(t, "key-1", toolsBody("test/model-a", false, false), mock.BehaviorHeader, "stream_tools:2")
	msg := decode(t, b)["choices"].([]any)[0].(map[string]any)["message"].(map[string]any)
	if tcs := msg["tool_calls"].([]any); len(tcs) != 2 || msg["content"] != nil {
		t.Fatalf("unstreamed stream_tools:2 = %v", msg)
	}
}

// The Anthropic and Responses streams carry the calls as their own argument
// deltas (input_json_delta, function_call_arguments.delta).
func TestStreamToolsInEveryStreamShape(t *testing.T) {
	m := newExtMock(t)
	for _, c := range []struct {
		path, body, deltaType, argField string
	}{
		{"/v1/messages", toolsBody("test/model-a", true, true), "content_block_delta", "partial_json"},
		{"/v1/responses", toolsBody("test/model-a", true, false), "response.function_call_arguments.delta", ""},
	} {
		status, b := m.do(t, "POST", c.path, c.body, "Authorization", "Bearer key-1", mock.BehaviorHeader, "stream_tools:2")
		if status != http.StatusOK {
			t.Fatalf("%s: status %d body %s", c.path, status, b)
		}
		var args []string
		for _, data := range sseData(t, b) {
			var ev map[string]any
			json.Unmarshal([]byte(data), &ev)
			if ev["type"] != c.deltaType {
				continue
			}
			if c.argField == "" {
				args = append(args, ev["delta"].(string))
			} else if d, _ := ev["delta"].(map[string]any); d["type"] == "input_json_delta" {
				args = append(args, d[c.argField].(string))
			}
		}
		if got := strings.Join(args, ""); got != `{"query":"mock-1"}{"query":"mock-2"}` || len(args) < 4 {
			t.Errorf("%s: argument deltas %q", c.path, args)
		}
	}
}

func TestStreamToolsRejectsABadCount(t *testing.T) {
	m := newExtMock(t)
	for _, arg := range []string{"0", "-1", "x", "", "65"} {
		status, b := m.chat(t, "key-1", chatBody("test/model-a", true), mock.BehaviorHeader, "stream_tools:"+arg)
		if status != http.StatusBadRequest || errorCode(t, b) != "invalid_mock_directive" {
			t.Errorf("stream_tools:%s: status %d body %s", arg, status, b)
		}
	}
	if _, n := m.state.TotalChargeMicros(); n != 0 {
		t.Fatalf("refused calls were charged: %d", n)
	}
}

// tool_args sets the arguments of every returned call, verbatim, from
// standard or URL-safe base64, padded or not.
func TestToolArgsDecoding(t *testing.T) {
	m := newExtMock(t)
	args := `{"cmd":"rm -rf /tmp/x?","n":1}`
	for _, enc := range []*base64.Encoding{base64.StdEncoding, base64.RawStdEncoding, base64.URLEncoding, base64.RawURLEncoding} {
		_, b := m.chat(t, "key-1", toolsBody("test/model-a", false, false), mock.BehaviorHeader, "tools,tool_args:"+enc.EncodeToString([]byte(args)))
		calls := decode(t, b)["choices"].([]any)[0].(map[string]any)["message"].(map[string]any)["tool_calls"].([]any)
		if got := calls[0].(map[string]any)["function"].(map[string]any)["arguments"]; got != args {
			t.Fatalf("arguments = %v, want %s (%s)", got, args, b)
		}
	}

	// Every streamed call gets them, and Anthropic's input is the object.
	encoded := base64.StdEncoding.EncodeToString([]byte(args))
	_, b := m.chat(t, "key-1", toolsBody("test/model-a", true, false), mock.BehaviorHeader, "stream_tools:2,tool_args:"+encoded)
	calls, _ := chatStreamCalls(t, sseData(t, b))
	if len(calls) != 2 || calls[0].args.String() != args || calls[1].args.String() != args {
		t.Fatalf("streamed calls %v", calls)
	}
	_, b = m.do(t, "POST", "/v1/messages", toolsBody("test/model-a", false, true), "x-api-key", "key-1", mock.BehaviorHeader, "tools,tool_args:"+encoded)
	var want any
	json.Unmarshal([]byte(args), &want)
	content := decode(t, b)["content"].([]any)
	if input := content[len(content)-1].(map[string]any)["input"]; !reflect.DeepEqual(input, want) {
		t.Fatalf("Anthropic input = %v, want %v", input, want)
	}

	for name, behavior := range map[string]string{
		"not base64":       "tools,tool_args:%%%",
		"not JSON":         "tools,tool_args:" + base64.StdEncoding.EncodeToString([]byte("{nope")),
		"no tool calls":    "tool_args:" + encoded,
		"before the calls": "tool_args:" + encoded + ",tools",
	} {
		status, b := m.chat(t, "key-1", toolsBody("test/model-a", false, false), mock.BehaviorHeader, behavior)
		if status != http.StatusBadRequest || errorCode(t, b) != "invalid_mock_directive" {
			t.Errorf("%s: status %d body %s", name, status, b)
		}
	}
}
