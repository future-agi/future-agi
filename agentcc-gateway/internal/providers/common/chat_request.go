package common

import (
	"slices"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ChatRequestForUpstream makes a per-dispatch copy of the request, messages and
// tool calls. OpenRouter continuation fields belong only to an OpenRouter-type
// instance, never to another upstream that happens to use the same wire format.
// Keep the canonical transcript intact for retries/fallbacks back to OpenRouter.
// Other nested payloads (including raw JSON bytes) remain read-only.
func ChatRequestForUpstream(req *models.ChatCompletionRequest, usesOpenRouter bool) models.ChatCompletionRequest {
	out := *req
	out.Messages = slices.Clone(req.Messages)
	for i := range out.Messages {
		message := &out.Messages[i]
		message.ToolCalls = slices.Clone(message.ToolCalls)
		if usesOpenRouter {
			continue
		}
		message.Reasoning = ""
		message.ReasoningDetails = nil
		for j := range message.ToolCalls {
			message.ToolCalls[j].ExtraContent = nil
		}
	}
	return out
}
