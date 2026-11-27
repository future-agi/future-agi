package server

import (
	"context"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// chatRouteChecker is implemented by providers that know a model they serve
// only on another route (Perplexity's Agent API models, on /v1/responses).
type chatRouteChecker interface {
	UnsupportedChatRoute(req *models.ChatCompletionRequest) *models.APIError
}

// unsupportedChatRoute is the error for sending req to p on chat completions,
// or nil when p may be called. The provider refuses such a request itself on
// every path; the chat handler asks first so that an async job is never
// accepted for it.
func unsupportedChatRoute(p providers.Provider, req *models.ChatCompletionRequest) *models.APIError {
	checker, ok := p.(chatRouteChecker)
	if !ok {
		return nil
	}
	return checker.UnsupportedChatRoute(req)
}

// refuseChatRoute runs refusal through the pipeline in place of the provider
// call and returns the error to write: the caller is authenticated first, and
// Request Logs record the 400 and its reason, with no upstream call and no
// cost.
func (h *Handlers) refuseChatRoute(ctx context.Context, rc *models.RequestContext, refusal *models.APIError) error {
	err := h.processTextInspection(ctx, rc, "chat.completions", func(context.Context, *models.RequestContext) error {
		return refusal
	})
	if err == nil {
		return refusal
	}
	return err
}
