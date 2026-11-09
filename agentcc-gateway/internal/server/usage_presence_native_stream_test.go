package server

import (
	"encoding/binary"
	"fmt"
	"hash/crc32"
	"net/http"
	"strings"
)

// These are provider wire fixtures, not canonical Usage literals. The Bedrock
// frames exercise its real HTTP/event-stream decoder without contacting AWS.
func presenceStream(a upInt2Adapter, usage string) func(http.ResponseWriter) {
	return presenceStreamWithStart(a, "", usage)
}

func presenceStreamWithStart(a upInt2Adapter, start, usage string) func(http.ResponseWriter) {
	switch a.name {
	case "openai", "azure":
		return upInt2OpenAISSE(usage)
	case "anthropic":
		return upInt2AnthropicSSE(start, usage)
	}
	return func(w http.ResponseWriter) {
		member := ""
		if usage != "" {
			member = `,"usage":` + usage
		}
		sse := func(s string) { fmt.Fprintf(w, "data: %s\n\n", s); w.(http.Flusher).Flush() }
		switch a.name {
		case "gemini":
			w.Header().Set("Content-Type", "text/event-stream")
			sse(upInt2Body(a, usage))
		case "cohere":
			w.Header().Set("Content-Type", "text/event-stream")
			sse(`{"type":"message-start","id":"c1"}`)
			sse(`{"type":"content-delta","delta":{"message":{"content":{"text":"` + upInt2Text + `"}}}}`)
			sse(`{"type":"message-end","delta":{"finish_reason":"COMPLETE"` + member + `}}`)
		case "bedrock-invoke":
			w.Header().Set("Content-Type", "application/vnd.amazon.eventstream")
			startUsage := ""
			if start != "" {
				startUsage = `,"usage":` + start
			}
			for _, body := range []string{
				`{"type":"message_start","message":{"id":"msg_1","model":"` + a.model + `"` + startUsage + `}}`,
				`{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"` + upInt2Text + `"}}`,
				`{"type":"message_delta","delta":{"stop_reason":"end_turn"}` + member + `}`,
				`{"type":"message_stop"}`,
			} {
				presenceAWSFrame(w, "chunk", body)
			}
		case "bedrock-converse":
			w.Header().Set("Content-Type", "application/vnd.amazon.eventstream")
			presenceAWSFrame(w, "messageStart", `{"role":"assistant"}`)
			presenceAWSFrame(w, "contentBlockDelta", `{"contentBlockIndex":0,"delta":{"text":"`+upInt2Text+`"}}`)
			presenceAWSFrame(w, "messageStop", `{"stopReason":"end_turn"}`)
			presenceAWSFrame(w, "metadata", `{`+strings.TrimPrefix(member, ",")+`}`)
		default:
			panic(a.name)
		}
	}
}

func presenceAWSFrame(w http.ResponseWriter, event, payload string) {
	var headers []byte
	for _, pair := range [][2]string{{":message-type", "event"}, {":event-type", event}} {
		headers = append(headers, byte(len(pair[0])))
		headers = append(headers, pair[0]...)
		headers = append(headers, 7, byte(len(pair[1])>>8), byte(len(pair[1])))
		headers = append(headers, pair[1]...)
	}
	frame := make([]byte, 12)
	binary.BigEndian.PutUint32(frame[:4], uint32(16+len(headers)+len(payload)))
	binary.BigEndian.PutUint32(frame[4:8], uint32(len(headers)))
	binary.BigEndian.PutUint32(frame[8:12], crc32.ChecksumIEEE(frame[:8]))
	frame = append(frame, headers...)
	frame = append(frame, payload...)
	frame = binary.BigEndian.AppendUint32(frame, crc32.ChecksumIEEE(frame))
	_, _ = w.Write(frame)
	w.(http.Flusher).Flush()
}
