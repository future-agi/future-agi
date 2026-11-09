package server

import (
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// checkToolCalls is call-order step 17: it checks the tool calls of the final
// non-stream response, after settlement, so a blocked response is still
// billed. A refusal is the response to send (422 tool_call_blocked); a
// strip edits resp. No-op until G34. Keep observeHook as its first call.
func (h *Handlers) checkToolCalls(rc *models.RequestContext, resp *models.ChatCompletionResponse) *models.APIError {
	h.observeHook(hookCheckToolCalls, rc)
	return nil
}

// checkStreamToolCalls checks a stream's buffered tool calls when they are
// complete, before the stream's post-plugins run, and returns the calls to
// send. G12 calls it from the stream loop. No-op until G34: every call is
// kept. Keep observeHook as its first call.
func (h *Handlers) checkStreamToolCalls(rc *models.RequestContext, calls []models.ToolCall) (keep []models.ToolCall, err *models.APIError) {
	h.observeHook(hookCheckStreamToolCalls, rc)
	return calls, nil
}
