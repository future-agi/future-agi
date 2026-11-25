package providers

import (
	"context"
	"io"
	"net/http"
	"net/url"
	"slices"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/speech"
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

// ClientTimeoutReporter is implemented by providers that state the client
// timeout they enforce. That timeout bounds the whole exchange, the streamed
// body included, so it caps the request's own deadline: the gateway reports
// whichever of the two is shorter as x-agentcc-timeout-ms.
type ClientTimeoutReporter interface {
	ClientTimeout() time.Duration
}

// PriceTableNamer is implemented by providers whose models the price table
// keys under a name the provider's ID need not match: the table lists new
// Gemini models only as "vertex_ai/gemini-4-argon" and "gemini/gemini-4-argon",
// so a Gemini provider named "vertex_ai_global" states "vertex_ai".
type PriceTableNamer interface {
	PriceTableName() string
}

// PriceTableName returns the name the price table keys p's models under, or
// "" when p states none.
func PriceTableName(p Provider) string {
	if n, ok := p.(PriceTableNamer); ok {
		return n.PriceTableName()
	}
	return ""
}

// ReportedCostSource is implemented by providers whose responses may state
// what the request cost: the OpenAI-format provider. It says what the
// provider was configured as and where it sends requests, which is what
// decides whether that stated cost is trusted (see ReportedCostVendor).
type ReportedCostSource interface {
	ConfiguredType() string
	BaseURLHost() string
	IgnoresProviderCost() bool
}

// reportedCostVendors are the provider types whose responses state the
// request's cost in a unit the cost plugin reads. Each has a reader in the
// cost plugin (reported.go) and a preset whose base_url names its official
// host (config.KnownProviders).
var reportedCostVendors = []string{"openrouter", "perplexity", "xai"}

// ReportedCostVendors lists the provider types whose stated cost can be
// trusted, for the cost plugin to check it reads each one.
func ReportedCostVendors() []string { return slices.Clone(reportedCostVendors) }

// ReportedCostVendor is the vendor whose own statement of what a request
// cost the cost plugin may record for a request p served, or "" when it may
// not. p is registered under providerID: the config.yaml key, or for an org
// instance the dashboard's provider name.
//
// The vendor is p's configured type, or when its config records none (a
// dashboard provider, or an untyped config.yaml entry) providerID. It is
// trusted only when it is one of reportedCostVendors, p sends its requests to
// that vendor's official host (the hostname of its preset base_url; scheme
// and port are not compared), and its config does not set
// ignore_provider_cost. An OpenAI-compatible provider of any other type or
// name, or one that points the vendor's type at a proxy, is priced from the
// catalogue: a base URL alone never earns trust, and nor does a type alone.
func ReportedCostVendor(p Provider, providerID string) string {
	s, ok := p.(ReportedCostSource)
	if !ok || s.IgnoresProviderCost() {
		return ""
	}
	vendor := s.ConfiguredType()
	if vendor == "" {
		vendor = providerID
	}
	if !slices.Contains(reportedCostVendors, vendor) {
		return ""
	}
	official := officialHost(vendor)
	if official == "" || !strings.EqualFold(s.BaseURLHost(), official) {
		return ""
	}
	return vendor
}

// officialHost is the hostname of vendor's preset base_url, or "".
func officialHost(vendor string) string {
	preset, ok := config.KnownProviders[vendor]
	if !ok {
		return ""
	}
	u, err := url.Parse(preset.BaseURL)
	if err != nil {
		return ""
	}
	return strings.ToLower(u.Hostname())
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

// SpeechRelayProvider is implemented by speech vendors reached over their own streaming
// WebSocket APIs, which the gateway relays with the platform's credential.
type SpeechRelayProvider interface {
	// Vendor names the vendor whose API is served, at /{vendor}/...
	Vendor() string
	// Open prepares the upstream connection and meter for one vendor path, query and headers.
	Open(path string, query url.Values, header http.Header) (*speech.Session, error)
}

// VectorStoresProvider is implemented by providers that support the OpenAI Vector Stores API.
type VectorStoresProvider interface {
	// ProxyVectorStoresRequest forwards a Vector Stores API request.
	// No streaming, no OpenAI-Beta header needed (GA API).
	ProxyVectorStoresRequest(ctx context.Context, method string, path string, body []byte, queryParams url.Values) (respBody []byte, statusCode int, respHeaders http.Header, err error)
}

// SystemOneProvider is implemented by providers that serve structured
// decisions in the System One format. The request body is forwarded as is.
type SystemOneProvider interface {
	SystemOne(ctx context.Context, req *models.DecisionRequest) (*models.DecisionResponse, error)
}

// DecisionsProvider is implemented by providers that serve OpenAI's
// Decisions API. The request body is forwarded as is.
type DecisionsProvider interface {
	CreateDecision(ctx context.Context, req *models.DecisionRequest) (*models.DecisionResponse, error)
}
