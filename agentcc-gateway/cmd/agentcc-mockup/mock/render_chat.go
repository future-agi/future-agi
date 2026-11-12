package mock

import (
	"net/http"
	"time"
)

// renderChat answers /v1/chat/completions in the OpenAI shape, with
// OpenRouter's top-level provider field and usage.cost.
func (s *Server) renderChat(w http.ResponseWriter, req *Request, resp *Response, cost float64) {
	id, created := nextID("gen-mock-"), time.Now().Unix()
	finish := chatFinishReason(resp)
	if req.Stream {
		s.streamChat(w, req, resp, cost, id, created, finish)
		return
	}
	msg := map[string]any{"role": "assistant", "content": resp.Content}
	if len(resp.ToolCalls) > 0 {
		msg["tool_calls"] = chatToolCalls(resp.ToolCalls)
		if resp.Content == "" {
			msg["content"] = nil
		}
	}
	body := map[string]any{
		"id": id, "object": "chat.completion", "created": created, "model": req.Model,
		"choices": []any{map[string]any{"index": 0, "message": msg, "finish_reason": finish}},
	}
	if resp.ServedProvider != "" {
		body["provider"] = resp.ServedProvider
	}
	if !resp.NoUsage {
		body["usage"] = chatUsage(resp, cost)
	}
	writeJSON(w, http.StatusOK, body)
}

// streamChat sends a role chunk, the content (or tool-call argument deltas),
// a finish chunk, a usage chunk with empty choices, then [DONE].
func (s *Server) streamChat(w http.ResponseWriter, req *Request, resp *Response, cost float64, id string, created int64, finish string) {
	sse := startSSE(w, req.HTTP, resp)
	chunk := func(choices []any) map[string]any {
		c := map[string]any{"id": id, "object": "chat.completion.chunk", "created": created, "model": req.Model, "choices": choices}
		if resp.ServedProvider != "" {
			c["provider"] = resp.ServedProvider
		}
		return c
	}
	delta := func(d map[string]any, finishReason any) []any {
		return []any{map[string]any{"index": 0, "delta": d, "finish_reason": finishReason}}
	}
	if !sse.send("", chunk(delta(map[string]any{"role": "assistant", "content": ""}, nil))) {
		return
	}
	for _, piece := range splitPieces(resp.Content, s.fix.Config.StreamChunks) {
		if !sse.send("", chunk(delta(map[string]any{"content": piece}, nil))) {
			return
		}
	}
	for i, tc := range resp.ToolCalls {
		head := map[string]any{"index": i, "id": tc.ID, "type": "function", "function": map[string]any{"name": tc.Name, "arguments": ""}}
		if !sse.send("", chunk(delta(map[string]any{"tool_calls": []any{head}}, nil))) {
			return
		}
		for _, frag := range splitPieces(tc.Arguments, 2) {
			arg := map[string]any{"index": i, "function": map[string]any{"arguments": frag}}
			if !sse.send("", chunk(delta(map[string]any{"tool_calls": []any{arg}}, nil))) {
				return
			}
		}
	}
	if !sse.send("", chunk(delta(map[string]any{}, finish))) {
		return
	}
	if !resp.NoUsage {
		final := chunk([]any{})
		final["usage"] = chatUsage(resp, cost)
		if !sse.send("", final) {
			return
		}
	}
	sse.done()
}

func chatFinishReason(resp *Response) string {
	switch {
	case resp.FinishReason != "":
		return resp.FinishReason
	case len(resp.ToolCalls) > 0:
		return "tool_calls"
	}
	return "stop"
}

func chatToolCalls(calls []ToolCall) []any {
	out := make([]any, 0, len(calls))
	for _, tc := range calls {
		out = append(out, map[string]any{"id": tc.ID, "type": "function", "function": map[string]any{"name": tc.Name, "arguments": tc.Arguments}})
	}
	return out
}

// chatUsage is the OpenAI/OpenRouter usage object; the token details appear
// only when non-zero, and cost only when the fixtures or a directive ask.
func chatUsage(resp *Response, cost float64) map[string]any {
	u := resp.Usage
	out := map[string]any{
		"prompt_tokens": u.Prompt, "completion_tokens": u.Completion, "total_tokens": u.Prompt + u.Completion,
	}
	if u.CachedTokens > 0 || u.CacheWriteTokens > 0 {
		out["prompt_tokens_details"] = map[string]any{"cached_tokens": u.CachedTokens, "cache_write_tokens": u.CacheWriteTokens}
	}
	if u.ReasoningTokens > 0 {
		out["completion_tokens_details"] = map[string]any{"reasoning_tokens": u.ReasoningTokens}
	}
	if resp.ReportCost {
		out["cost"] = cost
	}
	return out
}
