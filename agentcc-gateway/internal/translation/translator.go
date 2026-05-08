package translation

import (
	"context"
	"errors"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ErrNotImplemented is returned by translator stubs whose bodies have not yet
// been filled in. Agents B and C replace these returns with real logic.
var ErrNotImplemented = errors.New("translator: not implemented")

// InboundTranslator converts a request in some api_format into the canonical
// OpenAI ChatCompletionRequest, and converts the resulting
// ChatCompletionResponse (or StreamChunk sequence) back into that format's
// wire representation.
type InboundTranslator interface {
	// Name returns the api_format identifier (e.g. "anthropic", "google").
	Name() string

	// RequestToCanonical parses a raw request body (in the native format) into
	// an OpenAI ChatCompletionRequest. Returns any fields that couldn't be
	// translated as a non-fatal warning slice — the handler records these in
	// rc.Metadata["translation_drops"] for observability.
	RequestToCanonical(body []byte) (*models.ChatCompletionRequest, []string, error)

	// ResponseFromCanonical converts an OpenAI ChatCompletionResponse back
	// into the native response body.
	ResponseFromCanonical(resp *models.ChatCompletionResponse) ([]byte, error)

	// StreamEventsFromCanonical converts a channel of OpenAI StreamChunks into
	// a channel of already-formatted SSE event bytes (including "event: X\n"
	// and "data: {...}\n\n" framing). Handles synthetic boundary events that
	// don't exist in the OpenAI stream (e.g. content_block_start/stop for
	// Anthropic).
	StreamEventsFromCanonical(ctx context.Context, chunks <-chan models.StreamChunk) (<-chan []byte, <-chan error)

	// ErrorFromCanonical converts a gateway APIError (or upstream provider
	// error already normalized into APIError shape) into the inbound
	// format's native error body. A client calling /v1/messages expects an
	// Anthropic-shape error even if the failure originated in an OpenAI
	// upstream; this keeps error handling consistent for SDK consumers.
	ErrorFromCanonical(err *models.APIError) (statusCode int, body []byte, contentType string)
}
