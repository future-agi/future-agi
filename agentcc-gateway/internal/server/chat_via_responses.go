package server

import (
	"context"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// translatedChatCompletion calls provider with a request translated from
// another dialect: over the Responses API when the model refuses the request on
// chat completions, as gpt-5.6 refuses function tools while it reasons.
func translatedChatCompletion(ctx context.Context, provider providers.Provider, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	if rp, ok := provider.(providers.ChatViaResponsesProvider); ok && rp.ChatNeedsResponses(req) {
		return rp.ChatCompletionViaResponses(ctx, req)
	}
	return provider.ChatCompletion(ctx, req)
}

// translatedStreamChatCompletion is translatedChatCompletion for a stream.
func translatedStreamChatCompletion(ctx context.Context, provider providers.Provider, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	if rp, ok := provider.(providers.ChatViaResponsesProvider); ok && rp.ChatNeedsResponses(req) {
		return rp.StreamChatCompletionViaResponses(ctx, req)
	}
	return provider.StreamChatCompletion(ctx, req)
}

// translatesDialect reports whether rc's chat request was translated from
// another dialect (Anthropic Messages here; GenAI calls the helpers above
// itself). Only such a request may move to the Responses API:
// /v1/chat/completions forwards the caller's request as sent.
func translatesDialect(rc *models.RequestContext) bool {
	return rc != nil && rc.EndpointType == "anthropic_messages"
}

// dispatchChatCompletion is the chat dispatch's one upstream call.
func dispatchChatCompletion(ctx context.Context, rc *models.RequestContext, provider providers.Provider, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	if translatesDialect(rc) {
		return translatedChatCompletion(ctx, provider, req)
	}
	return provider.ChatCompletion(ctx, req)
}

// dispatchStreamChatCompletion is dispatchChatCompletion for a stream.
func dispatchStreamChatCompletion(ctx context.Context, rc *models.RequestContext, provider providers.Provider, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	if translatesDialect(rc) {
		return translatedStreamChatCompletion(ctx, provider, req)
	}
	return provider.StreamChatCompletion(ctx, req)
}
