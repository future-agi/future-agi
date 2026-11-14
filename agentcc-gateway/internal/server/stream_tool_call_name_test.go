package server

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// toolCallUpstreams are the streams an upstream sends for a tool call that
// looks up a city, in each provider's own wire format. Only the first event of
// the call names it; the arguments follow in fragments.
var toolCallUpstreams = map[string]string{
	// OpenAI: continuations carry no "name" key at all.
	"/chat/completions": "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"tool_calls\":[{\"index\":0,\"id\":\"call_city\",\"type\":\"function\",\"function\":{\"name\":\"lookup_city\",\"arguments\":\"\"}}]},\"finish_reason\":null}]}\n\n" +
		"data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{\"tool_calls\":[{\"index\":0,\"function\":{\"arguments\":\"{\\\"city\\\":\"}}]},\"finish_reason\":null}]}\n\n" +
		"data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{\"tool_calls\":[{\"index\":0,\"function\":{\"arguments\":\"\\\"Paris\\\"}\"}}]},\"finish_reason\":null}]}\n\n" +
		"data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"tool_calls\"}]}\n\n" +
		"data: [DONE]\n\n",
	// Anthropic: the name is on content_block_start; input_json_delta carries
	// only a fragment.
	"/messages": "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_1\",\"type\":\"message\",\"role\":\"assistant\",\"model\":\"claude-sonnet-5-5\",\"content\":[],\"usage\":{\"input_tokens\":5,\"output_tokens\":1}}}\n\n" +
		"event: content_block_start\ndata: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"tool_use\",\"id\":\"toolu_city\",\"name\":\"lookup_city\",\"input\":{}}}\n\n" +
		"event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"input_json_delta\",\"partial_json\":\"{\\\"city\\\":\"}}\n\n" +
		"event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"input_json_delta\",\"partial_json\":\"\\\"Paris\\\"}\"}}\n\n" +
		"event: content_block_stop\ndata: {\"type\":\"content_block_stop\",\"index\":0}\n\n" +
		"event: message_delta\ndata: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"tool_use\"},\"usage\":{\"output_tokens\":9}}\n\n" +
		"event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n",
	// Gemini sends each function call whole, so its one delta names it.
	":streamGenerateContent": "data: {\"candidates\":[{\"content\":{\"role\":\"model\",\"parts\":[{\"functionCall\":{\"name\":\"lookup_city\",\"args\":{\"city\":\"Paris\"}}}]},\"finishReason\":\"STOP\"}],\"usageMetadata\":{\"promptTokenCount\":4,\"candidatesTokenCount\":2,\"totalTokenCount\":6},\"modelVersion\":\"gemini-3.7-flash\"}\n\n",
}

// toolCallUpstream serves toolCallUpstreams on whichever route the gateway
// calls, flushing after every event so each arrives as its own chunk.
func toolCallUpstream(t *testing.T) *httptest.Server {
	t.Helper()
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var stream string
		for suffix, s := range toolCallUpstreams {
			if strings.HasSuffix(r.URL.Path, suffix) {
				stream = s
			}
		}
		if stream == "" {
			io.WriteString(w, `{"data":[]}`) // the startup connectivity check
			return
		}
		io.Copy(io.Discard, r.Body)
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		for _, event := range strings.SplitAfter(stream, "\n\n") {
			if event == "" {
				continue
			}
			io.WriteString(w, event)
			w.(http.Flusher).Flush()
		}
	}))
	t.Cleanup(ts.Close)
	return ts
}

// A client streams a tool call from /v1/chat/completions and accumulates it by
// index, taking a call's name from the delta that carries one. Every delta
// after the first of a call carries only an arguments fragment and must have no
// "name" key: a "name":"" there tells such a client the call's name is now
// empty, and the call is dropped. This holds whether the upstream speaks the
// OpenAI format, which the gateway decodes and encodes again, or a format the
// gateway translates.
func TestChatStreamToolCallContinuationsCarryNoName(t *testing.T) {
	tools := `"tools":[{"type":"function","function":{"name":"lookup_city","description":"Look up a city.","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}]`
	tests := []struct {
		name, model, wantID string
	}{
		{name: "openai upstream, decoded and encoded again", model: "gpt-4o", wantID: "call_city"},
		{name: "anthropic upstream, translated", model: "claude-sonnet-5-5", wantID: "toolu_city"},
		{name: "gemini upstream, translated", model: "gemini-3.7-flash"},
	}

	gatewayURL := streamingGateway(t, toolCallUpstream(t).URL)
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			route := streamRoute{
				name: tt.name,
				path: "/v1/chat/completions",
				body: `{"model":"` + tt.model + `","stream":true,"max_tokens":64,"messages":[{"role":"user","content":"Look up Paris."}],` + tools + `}`,
			}
			resp, body := postStream(t, gatewayURL, route, nil)
			if resp.StatusCode != http.StatusOK {
				t.Fatalf("status %d:\n%s", resp.StatusCode, body)
			}
			frames := sseFrames(body)
			if len(frames) == 0 || frames[len(frames)-1].data != "[DONE]" {
				t.Fatalf("stream did not end with [DONE]:\n%s", body)
			}

			type wireCall struct {
				Index    int                        `json:"index"`
				ID       *string                    `json:"id"`
				Type     *string                    `json:"type"`
				Function map[string]json.RawMessage `json:"function"`
			}
			seen := map[int]int{} // index → deltas seen
			names := map[int]string{}
			args := map[int]string{}
			ids := map[int]string{}
			for _, frame := range frames[:len(frames)-1] {
				var chunk struct {
					Choices []struct {
						Delta struct {
							ToolCalls []wireCall `json:"tool_calls"`
						} `json:"delta"`
					} `json:"choices"`
				}
				if err := json.Unmarshal([]byte(frame.data), &chunk); err != nil {
					t.Fatalf("unreadable chunk %q: %v", frame.data, err)
				}
				for _, choice := range chunk.Choices {
					for _, call := range choice.Delta.ToolCalls {
						if call.Function == nil {
							seen[call.Index]++
							continue
						}
						name, hasName := call.Function["name"]
						if seen[call.Index] == 0 {
							if !hasName {
								t.Fatalf("first delta of call %d has no name: %s", call.Index, frame.data)
							}
							var first string
							if err := json.Unmarshal(name, &first); err != nil {
								t.Fatalf("name %s: %v", name, err)
							}
							names[call.Index] = first
							if call.Type == nil || *call.Type != "function" {
								t.Errorf("first delta of call %d has type %v, want function", call.Index, call.Type)
							}
							if call.ID != nil {
								ids[call.Index] = *call.ID
							}
						} else if hasName {
							t.Errorf(`continuation of call %d carries "name":%s; the key must be absent: %s`, call.Index, name, frame.data)
						}
						var fragment string
						if raw, ok := call.Function["arguments"]; ok {
							if err := json.Unmarshal(raw, &fragment); err != nil {
								t.Fatalf("arguments %s: %v", raw, err)
							}
						}
						args[call.Index] += fragment
						seen[call.Index]++
					}
				}
			}

			if len(names) != 1 || names[0] != "lookup_city" {
				t.Fatalf("tool calls by index = %v, want one lookup_city:\n%s", names, body)
			}
			var got map[string]any
			if err := json.Unmarshal([]byte(args[0]), &got); err != nil || got["city"] != "Paris" || len(got) != 1 {
				t.Errorf(`arguments = %q, want {"city":"Paris"}`, args[0])
			}
			if tt.wantID != "" && ids[0] != tt.wantID {
				t.Errorf("id = %q, want %q", ids[0], tt.wantID)
			}
			if ids[0] == "" {
				t.Errorf("first delta has no id")
			}
		})
	}
}
