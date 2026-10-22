package server

import (
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// checkToolCalls is call-order step 17, run on the final non-stream
// response after settlement. It is a no-op: it refuses nothing and edits
// nothing. Keep observeHook as its first call.
func (h *Handlers) checkToolCalls(rc *models.RequestContext, resp *models.ChatCompletionResponse) *models.APIError {
	h.observeHook(hookCheckToolCalls, rc)
	return nil
}

// checkStreamToolCalls runs on a stream's buffered tool calls when they are
// complete, before the stream's post-plugins run, and returns the calls to
// send; the stream loop calls it. It is a no-op: every call is kept. Keep
// observeHook as its first call.
func (h *Handlers) checkStreamToolCalls(rc *models.RequestContext, calls []models.ToolCall) (keep []models.ToolCall, err *models.APIError) {
	h.observeHook(hookCheckStreamToolCalls, rc)
	return calls, nil
}
