package mock

import (
	"encoding/json"
	"net/http"
)

// renderMessages answers /v1/messages in the Anthropic shape. Anthropic's
// input_tokens excludes the cache tokens, which it reports separately.
func (s *Server) renderMessages(w http.ResponseWriter, req *Request, resp *Response) {
	id := nextID("msg_mock_")
	stop := anthropicStopReason(resp)
	if req.Stream {
		s.streamMessages(w, req, resp, id, stop)
		return
	}
	var content []any
	if resp.Content != "" || len(resp.ToolCalls) == 0 {
		content = append(content, map[string]any{"type": "text", "text": resp.Content})
	}
	for _, tc := range resp.ToolCalls {
		content = append(content, map[string]any{"type": "tool_use", "id": tc.ID, "name": tc.Name, "input": toolInput(tc)})
	}
	body := map[string]any{
		"id": id, "type": "message", "role": "assistant", "model": req.Model,
		"content": content, "stop_reason": stop, "stop_sequence": nil,
	}
	if !resp.NoUsage {
		body["usage"] = anthropicUsage(resp.Usage, resp.Usage.Completion)
	}
	writeJSON(w, http.StatusOK, body)
}

func (s *Server) streamMessages(w http.ResponseWriter, req *Request, resp *Response, id, stop string) {
	sse := startSSE(w, req.HTTP, resp)
	event := func(typ string, fields map[string]any) bool {
		fields["type"] = typ
		return sse.send(typ, fields)
	}
	start := map[string]any{
		"id": id, "type": "message", "role": "assistant", "model": req.Model,
		"content": []any{}, "stop_reason": nil, "stop_sequence": nil,
	}
	if !resp.NoUsage {
		start["usage"] = anthropicUsage(resp.Usage, 0)
	}
	if !event("message_start", map[string]any{"message": start}) {
		return
	}
	block := 0
	if resp.Content != "" || len(resp.ToolCalls) == 0 {
		if !event("content_block_start", map[string]any{"index": block, "content_block": map[string]any{"type": "text", "text": ""}}) {
			return
		}
		for _, piece := range splitPieces(resp.Content, s.fix.Config.StreamChunks) {
			if !event("content_block_delta", map[string]any{"index": block, "delta": map[string]any{"type": "text_delta", "text": piece}}) {
				return
			}
		}
		if !event("content_block_stop", map[string]any{"index": block}) {
			return
		}
		block++
	}
	for _, tc := range resp.ToolCalls {
		head := map[string]any{"type": "tool_use", "id": tc.ID, "name": tc.Name, "input": map[string]any{}}
		if !event("content_block_start", map[string]any{"index": block, "content_block": head}) {
			return
		}
		for _, frag := range splitPieces(tc.Arguments, 2) {
			if !event("content_block_delta", map[string]any{"index": block, "delta": map[string]any{"type": "input_json_delta", "partial_json": frag}}) {
				return
			}
		}
		if !event("content_block_stop", map[string]any{"index": block}) {
			return
		}
		block++
	}
	delta := map[string]any{"delta": map[string]any{"stop_reason": stop, "stop_sequence": nil}}
	if !resp.NoUsage {
		delta["usage"] = map[string]any{"output_tokens": resp.Usage.Completion}
	}
	if !event("message_delta", delta) {
		return
	}
	event("message_stop", map[string]any{})
}

func anthropicStopReason(resp *Response) string {
	switch {
	case resp.FinishReason != "":
		return resp.FinishReason
	case len(resp.ToolCalls) > 0:
		return "tool_use"
	}
	return "end_turn"
}

func anthropicUsage(u Usage, outputTokens int) map[string]any {
	return map[string]any{
		"input_tokens":                max(0, u.Prompt-u.CachedTokens-u.CacheWriteTokens),
		"output_tokens":               outputTokens,
		"cache_read_input_tokens":     u.CachedTokens,
		"cache_creation_input_tokens": u.CacheWriteTokens,
	}
}

// toolInput is a tool call's arguments as the object Anthropic's input takes;
// arguments that are not a JSON object are wrapped as {"arguments": text}.
func toolInput(tc ToolCall) any {
	var obj map[string]any
	if json.Unmarshal([]byte(tc.Arguments), &obj) == nil && obj != nil {
		return obj
	}
	return map[string]any{"arguments": tc.Arguments}
}
