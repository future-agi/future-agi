package mock

import (
	"net/http"
	"time"
)

// renderResponses answers /v1/responses in the OpenAI Responses shape.
func (s *Server) renderResponses(w http.ResponseWriter, req *Request, resp *Response, cost float64) {
	msgID := nextID("msg_mock_")
	full := s.responseObject(req, resp, cost, msgID, false)
	if !req.Stream {
		writeJSON(w, http.StatusOK, full)
		return
	}
	sse := startSSE(w, req.HTTP, resp)
	seq := 0
	event := func(typ string, fields map[string]any) bool {
		fields["type"], fields["sequence_number"] = typ, seq
		seq++
		return sse.send(typ, fields)
	}
	if !event("response.created", map[string]any{"response": s.responseObject(req, resp, cost, msgID, true)}) {
		return
	}
	out := 0
	if resp.Content != "" || len(resp.ToolCalls) == 0 {
		item := messageItem(msgID, "", "in_progress")
		if !event("response.output_item.added", map[string]any{"output_index": out, "item": item}) {
			return
		}
		for _, piece := range splitPieces(resp.Content, s.fix.Config.StreamChunks) {
			if !event("response.output_text.delta", map[string]any{"item_id": msgID, "output_index": out, "content_index": 0, "delta": piece}) {
				return
			}
		}
		if !event("response.output_text.done", map[string]any{"item_id": msgID, "output_index": out, "content_index": 0, "text": resp.Content}) ||
			!event("response.output_item.done", map[string]any{"output_index": out, "item": messageItem(msgID, resp.Content, "completed")}) {
			return
		}
		out++
	}
	for _, tc := range resp.ToolCalls {
		itemID := "fc_" + tc.ID
		if !event("response.output_item.added", map[string]any{"output_index": out, "item": functionCallItem(tc, "", "in_progress")}) {
			return
		}
		for _, frag := range splitPieces(tc.Arguments, 2) {
			if !event("response.function_call_arguments.delta", map[string]any{"item_id": itemID, "output_index": out, "delta": frag}) {
				return
			}
		}
		if !event("response.function_call_arguments.done", map[string]any{"item_id": itemID, "output_index": out, "arguments": tc.Arguments}) ||
			!event("response.output_item.done", map[string]any{"output_index": out, "item": functionCallItem(tc, tc.Arguments, "completed")}) {
			return
		}
		out++
	}
	event("response.completed", map[string]any{"response": full})
}

// responseObject is the response object; inProgress is its state when a
// stream starts (no output, no usage).
func (s *Server) responseObject(req *Request, resp *Response, cost float64, msgID string, inProgress bool) map[string]any {
	status, incomplete := responseStatus(resp)
	obj := map[string]any{
		"id": "resp_" + msgID, "object": "response", "created_at": time.Now().Unix(),
		"model": req.Model, "status": status, "incomplete_details": incomplete, "output": []any{}, "usage": nil,
	}
	if resp.ServedProvider != "" {
		obj["provider"] = resp.ServedProvider
	}
	if inProgress {
		obj["status"], obj["incomplete_details"] = "in_progress", nil
		return obj
	}
	var output []any
	if resp.Content != "" || len(resp.ToolCalls) == 0 {
		output = append(output, messageItem(msgID, resp.Content, "completed"))
	}
	for _, tc := range resp.ToolCalls {
		output = append(output, functionCallItem(tc, tc.Arguments, "completed"))
	}
	obj["output"] = output
	if !resp.NoUsage {
		obj["usage"] = responsesUsage(resp, cost)
	}
	return obj
}

func responseStatus(resp *Response) (string, any) {
	switch resp.FinishReason {
	case "length":
		return "incomplete", map[string]any{"reason": "max_output_tokens"}
	case "content_filter":
		return "incomplete", map[string]any{"reason": "content_filter"}
	}
	return "completed", nil
}

func messageItem(id, text, status string) map[string]any {
	content := []any{}
	if status == "completed" {
		content = append(content, map[string]any{"type": "output_text", "text": text, "annotations": []any{}})
	}
	return map[string]any{"type": "message", "id": id, "status": status, "role": "assistant", "content": content}
}

func functionCallItem(tc ToolCall, args, status string) map[string]any {
	return map[string]any{"type": "function_call", "id": "fc_" + tc.ID, "call_id": tc.ID, "name": tc.Name, "arguments": args, "status": status}
}

func responsesUsage(resp *Response, cost float64) map[string]any {
	u := resp.Usage
	out := map[string]any{
		"input_tokens": u.Prompt, "output_tokens": u.Completion, "total_tokens": u.Prompt + u.Completion,
		"input_tokens_details":  map[string]any{"cached_tokens": u.CachedTokens},
		"output_tokens_details": map[string]any{"reasoning_tokens": u.ReasoningTokens},
	}
	if resp.ReportCost {
		out["cost"] = cost
	}
	return out
}
