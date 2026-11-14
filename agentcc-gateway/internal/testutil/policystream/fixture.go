// Package policystream supplies local streaming fixtures for policy acceptance tests.
package policystream

import (
	"encoding/binary"
	"encoding/json"
	"fmt"
	"hash/crc32"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Reply emits the provider's complete text or forced-function stream. Bedrock
// uses CRC-framed AWS events, not SSE or the non-streaming JSON response.
func Reply(w http.ResponseWriter, provider string, forced bool) {
	w.Header().Set("Content-Type", "text/event-stream")
	if provider == "bedrock" || provider == "converse" {
		w.Header().Set("Content-Type", "application/vnd.amazon.eventstream")
	}
	emit := func(event, data string) {
		if provider == "bedrock" || provider == "converse" {
			// InvokeModel wraps Anthropic payloads in chunk events; Converse
			// uses its own messageStart/contentBlockDelta event names.
			if provider == "bedrock" {
				event = "chunk"
			}
			_, _ = w.Write(frame(event, []byte(data)))
		} else {
			_, _ = fmt.Fprintf(w, "event: %s\ndata: %s\n\n", event, data)
		}
	}
	switch provider {
	case "anthropic", "bedrock":
		emit("message_start", `{"type":"message_start","message":{"id":"synthetic","type":"message","role":"assistant","model":"test-model","content":[],"stop_reason":null,"usage":{"input_tokens":1,"output_tokens":0}}}`)
		reason := "end_turn"
		if forced {
			reason = "tool_use"
			emit("content_block_start", `{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"call_synthetic","name":"permitted_tool","input":{}}}`)
			emit("content_block_delta", `{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{}"}}`)
		} else {
			emit("content_block_start", `{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}`)
			emit("content_block_delta", `{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"ok"}}`)
		}
		emit("content_block_stop", `{"type":"content_block_stop","index":0}`)
		emit("message_delta", `{"type":"message_delta","delta":{"stop_reason":"`+reason+`","stop_sequence":null},"usage":{"output_tokens":1}}`)
		emit("message_stop", `{"type":"message_stop"}`)
	case "converse":
		emit("messageStart", `{"role":"assistant"}`)
		reason := "end_turn"
		if forced {
			reason = "tool_use"
			emit("contentBlockStart", `{"contentBlockIndex":0,"start":{"toolUse":{"toolUseId":"call_synthetic","name":"permitted_tool"}}}`)
			emit("contentBlockDelta", `{"contentBlockIndex":0,"delta":{"toolUse":{"input":"{}"}}}`)
		} else {
			emit("contentBlockDelta", `{"contentBlockIndex":0,"delta":{"text":"ok"}}`)
		}
		emit("contentBlockStop", `{"contentBlockIndex":0}`)
		emit("messageStop", `{"stopReason":"`+reason+`"}`)
		emit("metadata", `{"usage":{"inputTokens":1,"outputTokens":1,"totalTokens":2},"metrics":{"latencyMs":1}}`)
	case "gemini":
		part := `{"text":"ok"}`
		if forced {
			part = `{"functionCall":{"name":"permitted_tool","args":{}}}`
		}
		emit("message", `{"candidates":[{"index":0,"content":{"role":"model","parts":[`+part+`]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":1,"candidatesTokenCount":1,"totalTokenCount":2}}`)
	case "cohere":
		emit("message-start", `{"type":"message-start","id":"synthetic"}`)
		emit("content-start", `{"type":"content-start","index":0,"delta":{"message":{"content":{"type":"text","text":""}}}}`)
		emit("content-delta", `{"type":"content-delta","index":0,"delta":{"message":{"content":{"text":"ok"}}}}`)
		emit("content-end", `{"type":"content-end","index":0}`)
		emit("message-end", `{"type":"message-end","finish_reason":"COMPLETE","usage":{"tokens":{"input_tokens":1,"output_tokens":1}}}`)
	default: // OpenAI-compatible and Azure Chat Completions.
		emit("message", `{"id":"synthetic","object":"chat.completion.chunk","model":"test-model","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}`)
		delta, reason := `{"content":"ok"}`, "stop"
		if forced {
			delta = `{"tool_calls":[{"index":0,"id":"call_synthetic","type":"function","function":{"name":"permitted_tool","arguments":"{}"}}]}`
			reason = "tool_calls"
		}
		emit("message", `{"id":"synthetic","object":"chat.completion.chunk","model":"test-model","choices":[{"index":0,"delta":`+delta+`,"finish_reason":null}]}`)
		emit("message", `{"id":"synthetic","object":"chat.completion.chunk","model":"test-model","choices":[{"index":0,"delta":{},"finish_reason":"`+reason+`"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
		emit("message", "[DONE]")
	}
}

func frame(event string, payload []byte) []byte {
	var headers []byte
	for _, h := range [][2]string{{":message-type", "event"}, {":event-type", event}, {":content-type", "application/json"}} {
		headers = append(headers, byte(len(h[0])))
		headers = append(headers, h[0]...)
		headers = append(headers, 7, byte(len(h[1])>>8), byte(len(h[1])))
		headers = append(headers, h[1]...)
	}
	out := make([]byte, 8)
	binary.BigEndian.PutUint32(out[:4], uint32(16+len(headers)+len(payload)))
	binary.BigEndian.PutUint32(out[4:], uint32(len(headers)))
	out = binary.BigEndian.AppendUint32(out, crc32.ChecksumIEEE(out))
	out = append(out, headers...)
	out = append(out, payload...)
	return binary.BigEndian.AppendUint32(out, crc32.ChecksumIEEE(out))
}

// Collect drains both channels, preserving errors rather than mistaking EOF
// for success. CheckSuccess separately verifies observable output and completion.
func Collect(chunks <-chan models.StreamChunk, errs <-chan error) ([]models.StreamChunk, error) {
	var result []models.StreamChunk
	var first error
	for chunks != nil || errs != nil {
		select {
		case chunk, ok := <-chunks:
			if !ok {
				chunks = nil
			} else {
				result = append(result, chunk)
			}
		case err, ok := <-errs:
			if !ok {
				errs = nil
			} else if err != nil && first == nil {
				first = err
			}
		}
	}
	return result, first
}

func CheckSuccess(t *testing.T, chunks []models.StreamChunk, forced bool) {
	t.Helper()
	var text, name, args strings.Builder
	finish := ""
	for _, chunk := range chunks {
		for _, choice := range chunk.Choices {
			if choice.Delta.Content != nil {
				text.WriteString(*choice.Delta.Content)
			}
			if choice.FinishReason != nil {
				finish = *choice.FinishReason
			}
			for _, call := range choice.Delta.ToolCalls {
				if call.Function != nil {
					name.WriteString(call.Function.Name)
					args.WriteString(call.Function.Arguments)
				}
			}
		}
	}
	if forced {
		if name.String() != "permitted_tool" || args.String() != "{}" || finish != "tool_calls" {
			t.Errorf("stream tool=%q args=%q finish=%q; want permitted_tool, {}, tool_calls", name.String(), args.String(), finish)
		}
	} else if text.String() != "ok" || finish != "stop" {
		t.Errorf("stream text=%q finish=%q; want ok, stop", text.String(), finish)
	}
	encoded, _ := json.Marshal(chunks)
	t.Logf("completed stream=%s", encoded)
}
