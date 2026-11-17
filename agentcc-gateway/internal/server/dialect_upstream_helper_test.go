package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
)

// dialectUpstream answers in OpenAI, Responses, Anthropic and GenAI format,
// streamed or not, with reply as the text, and counts the generation calls
// it gets.
type dialectUpstream struct {
	reply string
	mu    sync.Mutex
	calls int
}

func (u *dialectUpstream) served() int { u.mu.Lock(); defer u.mu.Unlock(); return u.calls }

func (u *dialectUpstream) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	if r.Method != http.MethodPost { // the server lists models at start
		fmt.Fprint(w, `{"data":[],"models":[]}`)
		return
	}
	u.mu.Lock()
	u.calls++
	u.mu.Unlock()
	stream := bytes.Contains(body, []byte(`"stream":true`)) || strings.Contains(r.URL.Path, "streamGenerateContent")
	text, _ := json.Marshal(u.reply)
	sse := func(events ...string) {
		w.Header().Set("Content-Type", "text/event-stream")
		for _, e := range events {
			fmt.Fprint(w, e+"\n\n")
		}
	}
	switch {
	case r.URL.Path == "/v1/chat/completions" && stream:
		sse(`data: {"id":"c","object":"chat.completion.chunk","model":"gpt-5.5","choices":[{"index":0,"delta":{"content":`+string(text)+`}}]}`,
			`data: {"id":"c","object":"chat.completion.chunk","model":"gpt-5.5","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}`,
			`data: [DONE]`)
	case r.URL.Path == "/v1/chat/completions":
		fmt.Fprint(w, `{"id":"c","object":"chat.completion","model":"gpt-5.5","choices":[{"index":0,"message":{"role":"assistant","content":`+string(text)+`},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}`)
	case r.URL.Path == "/v1/responses" && stream:
		sse(`event: response.output_text.delta`+"\n"+`data: {"type":"response.output_text.delta","delta":`+string(text)+`}`,
			`event: response.completed`+"\n"+`data: {"type":"response.completed","response":{"id":"r","model":"gpt-5.5","status":"completed","output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":`+string(text)+`}]}],"usage":{"input_tokens":5,"output_tokens":3,"total_tokens":8}}}`)
	case r.URL.Path == "/v1/responses":
		fmt.Fprint(w, `{"id":"r","object":"response","model":"gpt-5.5","status":"completed","output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":`+string(text)+`}]}],"usage":{"input_tokens":5,"output_tokens":3,"total_tokens":8}}`)
	case r.URL.Path == "/v1/messages" && stream:
		sse(`event: message_start`+"\n"+`data: {"type":"message_start","message":{"id":"m","type":"message","role":"assistant","model":"claude-sonnet-5-5","content":[],"usage":{"input_tokens":5,"output_tokens":0}}}`,
			`event: content_block_start`+"\n"+`data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}`,
			`event: content_block_delta`+"\n"+`data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":`+string(text)+`}}`,
			`event: content_block_stop`+"\n"+`data: {"type":"content_block_stop","index":0}`,
			`event: message_delta`+"\n"+`data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":3}}`,
			`event: message_stop`+"\n"+`data: {"type":"message_stop"}`)
	case r.URL.Path == "/v1/messages":
		fmt.Fprint(w, `{"id":"m","type":"message","role":"assistant","model":"claude-sonnet-5-5","content":[{"type":"text","text":`+string(text)+`}],"stop_reason":"end_turn","usage":{"input_tokens":5,"output_tokens":3}}`)
	case strings.HasSuffix(r.URL.Path, ":streamGenerateContent"):
		sse(`data: {"candidates":[{"content":{"role":"model","parts":[{"text":` + string(text) + `}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":3,"totalTokenCount":8}}`)
	case strings.HasSuffix(r.URL.Path, ":generateContent"):
		fmt.Fprint(w, `{"candidates":[{"content":{"role":"model","parts":[{"text":`+string(text)+`}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":3,"totalTokenCount":8}}`)
	default:
		http.Error(w, "unexpected path "+r.URL.Path, http.StatusNotFound)
	}
}
