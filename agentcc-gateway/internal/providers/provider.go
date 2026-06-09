package providers

import (
	"context"
	"io"
	"net/http"
	"net/url"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Provider is the interface for calling LLM providers.
type Provider interface {
	// ID returns the provider's unique identifier.
	ID() string

	// ChatCompletion sends a non-streaming chat completion request.
	ChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error)

	// StreamChatCompletion sends a streaming chat completion request.
	// Returns a channel that emits chunks. The channel is closed when streaming is done.
	StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error)

	// ListModels returns available models for this provider.
	ListModels(ctx context.Context) ([]models.ModelObject, error)

	// Close releases resources held by this provider.
	Close() error
}

// EmbeddingProvider is implemented by providers that support embedding generation.
type EmbeddingProvider interface {
	CreateEmbedding(ctx context.Context, req *models.EmbeddingRequest) (*models.EmbeddingResponse, error)
}

// ImageProvider is implemented by providers that support image generation.
type ImageProvider interface {
	CreateImage(ctx context.Context, req *models.ImageRequest) (*models.ImageResponse, error)
}

// SpeechProvider is implemented by providers that support text-to-speech.
type SpeechProvider interface {
	// CreateSpeech returns an audio stream, its content-type, and any error.
	//
	// IMPORTANT: The caller MUST call Close() on the returned io.ReadCloser when done.
	// Some providers (e.g., Azure) hold a concurrency semaphore that is only released
	// when the ReadCloser is closed. Failing to close it will leak the semaphore and
	// eventually block all requests to the provider.
	CreateSpeech(ctx context.Context, req *models.SpeechRequest) (io.ReadCloser, string, error)
}

// TranscriptionProvider is implemented by providers that support speech-to-text.
type TranscriptionProvider interface {
	CreateTranscription(ctx context.Context, req *models.TranscriptionRequest) (*models.TranscriptionResponse, error)
}

// ResponsesProvider is implemented by providers that support the OpenAI Responses API.
type ResponsesProvider interface {
	// CreateResponse sends a Responses API request.
	// Returns the raw JSON response body for pass-through.
	CreateResponse(ctx context.Context, reqBody []byte) ([]byte, int, error)

	// StreamResponse sends a streaming Responses API request.
	// Returns a reader for the SSE stream and the response status.
	StreamResponse(ctx context.Context, reqBody []byte) (io.ReadCloser, int, error)
}

// TranslationProvider is implemented by providers that support audio translation.
type TranslationProvider interface {
	CreateTranslation(ctx context.Context, req *models.TranslationRequest) (*models.TranslationResponse, error)
}

// RerankProvider is implemented by providers that support document reranking.
type RerankProvider interface {
	Rerank(ctx context.Context, req *models.RerankRequest) (*models.RerankResponse, error)
}

// BatchProvider is implemented by providers that support native batch processing
// (e.g., OpenAI Batch API). If not implemented, the gateway falls back to
// concurrent request-level processing.
type BatchProvider interface {
	// SubmitBatch submits a batch of requests to the provider's native batch API.
	// Returns the provider-assigned batch ID.
	SubmitBatch(ctx context.Context, requests []models.BatchRequest) (string, error)

	// GetBatch retrieves the current status of a provider batch.
	GetBatch(ctx context.Context, batchID string) (*models.ProviderBatchStatus, error)

	// CancelBatch cancels a provider batch.
	CancelBatch(ctx context.Context, batchID string) error
}

// AssistantsProvider is implemented by providers that support the OpenAI Assistants API.
type AssistantsProvider interface {
	// ProxyAssistantsRequest forwards a non-streaming Assistants API request.
	// The path is the full API path (e.g., "/v1/assistants", "/v1/threads/thread_abc/runs").
	// Returns the raw response body, HTTP status code, selected response headers, and any error.
	ProxyAssistantsRequest(ctx context.Context, method string, path string, body []byte, queryParams url.Values) (respBody []byte, statusCode int, respHeaders http.Header, err error)

	// StreamAssistantsRequest forwards a streaming Assistants API request.
	// Returns an io.ReadCloser for the SSE stream. The caller must close it.
	StreamAssistantsRequest(ctx context.Context, method string, path string, body []byte, queryParams url.Values) (stream io.ReadCloser, statusCode int, respHeaders http.Header, err error)
}

// AnthropicNativeProvider is implemented by providers that support the native Anthropic Messages API.
type AnthropicNativeProvider interface {
	// CreateAnthropicMessage sends a raw Anthropic Messages API request.
	CreateAnthropicMessage(ctx context.Context, reqBody []byte, headers map[string]string) (respBody []byte, statusCode int, err error)

	// CountAnthropicTokens sends a raw Anthropic count_tokens request.
	CountAnthropicTokens(ctx context.Context, reqBody []byte, headers map[string]string) (respBody []byte, statusCode int, err error)

	// StreamAnthropicMessage sends a streaming Anthropic Messages API request.
	StreamAnthropicMessage(ctx context.Context, reqBody []byte, headers map[string]string) (stream io.ReadCloser, statusCode int, err error)
}

// GenAINativeProvider is implemented by providers that support the native Google GenAI API.
type GenAINativeProvider interface {
	// GenerateContent sends a raw Google GenAI generateContent request.
	GenerateContent(ctx context.Context, model string, reqBody []byte, headers map[string]string) (respBody []byte, statusCode int, err error)

	// StreamGenerateContent sends a streaming Google GenAI request.
	StreamGenerateContent(ctx context.Context, model string, reqBody []byte, headers map[string]string) (stream io.ReadCloser, statusCode int, err error)

	// GenAICountTokens sends a countTokens request.
	GenAICountTokens(ctx context.Context, model string, reqBody []byte, headers map[string]string) (respBody []byte, statusCode int, err error)

	// EmbedContent sends an embedContent request.
	EmbedContent(ctx context.Context, model string, reqBody []byte, headers map[string]string) (respBody []byte, statusCode int, err error)
}

// SearchProvider is implemented by providers that support web search.
type SearchProvider interface {
	Search(ctx context.Context, req *models.SearchRequest) (*models.SearchResponse, error)
}

// OCRProvider is implemented by providers that support optical character recognition.
type OCRProvider interface {
	OCR(ctx context.Context, req *models.OCRRequest) (*models.OCRResponse, error)
}

// VideoProvider is implemented by providers that support video generation.
type VideoProvider interface {
	SubmitVideo(ctx context.Context, req *models.VideoGenerationRequest) (*models.VideoSubmitResponse, error)
	GetVideoStatus(ctx context.Context, providerJobID string) (*models.VideoStatusResponse, error)
	GetVideoContent(ctx context.Context, providerJobID string, variantIndex int) (contentURL string, err error)
	CancelVideo(ctx context.Context, providerJobID string) error
}

// RealtimeProvider is implemented by providers that support WebSocket-based realtime API.
type RealtimeProvider interface {
	RealtimeURL(model string) (string, error)
	RealtimeHeaders(model string) (http.Header, error)
	RealtimeSubprotocols() []string
}

// VectorStoresProvider is implemented by providers that support the OpenAI Vector Stores API.
type VectorStoresProvider interface {
	// ProxyVectorStoresRequest forwards a Vector Stores API request.
	// No streaming, no OpenAI-Beta header needed (GA API).
	ProxyVectorStoresRequest(ctx context.Context, method string, path string, body []byte, queryParams url.Values) (respBody []byte, statusCode int, respHeaders http.Header, err error)
}
