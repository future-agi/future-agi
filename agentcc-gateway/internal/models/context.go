package models

import (
	"context"
	"net/http"
	"sync"
	"time"
)

// GuardrailResult captures a single guardrail check that was triggered.
// Stored on RequestContext by the guardrails plugin for downstream logging.
type GuardrailResult struct {
	Name      string  `json:"name"`
	Score     float64 `json:"score"`
	Threshold float64 `json:"threshold"`
	Action    string  `json:"action"` // "block", "warn", or "log"
	Message   string  `json:"message"`
}

// TraceSnapshot is an immutable copy of RequestContext fields needed by
// async operations (logging, mirroring) after the handler returns.
// This avoids races when the original RequestContext is released to the pool.
type TraceSnapshot struct {
	RequestID        string
	TraceID          string
	StartTime        time.Time
	Model            string
	ResolvedModel    string
	Provider         string
	Request          *ChatCompletionRequest
	Response         *ChatCompletionResponse
	IsStream         bool
	Metadata         map[string]string
	SessionID        string
	UserID           string
	Flags            RequestFlags
	Timings          map[string]time.Duration
	Errors           []error
	GuardrailResults []GuardrailResult
	RequestHeaders   http.Header
}

// RequestContext carries request state through the plugin pipeline.
// Allocated from a sync.Pool and returned after the request completes.
type RequestContext struct {
	RequestID     string
	TraceID       string
	StartTime     time.Time
	Model         string
	ResolvedModel string
	Provider      string
	Request       *ChatCompletionRequest
	Response      *ChatCompletionResponse
	IsStream      bool
	Metadata      map[string]string
	SessionID     string
	UserID        string
	Flags         RequestFlags
	Timings       map[string]time.Duration
	Errors        []error

	// mu protects Timings and Metadata during parallel post-plugin execution.
	mu sync.Mutex

	// RequestHeaders stores the original HTTP request headers for logging.
	RequestHeaders http.Header

	// EndpointType identifies the API endpoint: "chat", "embedding", "image",
	// "speech", "transcription", or "rerank". Defaults to "chat".
	EndpointType string

	// Guardrail results — populated by the guardrails plugin with triggered checks.
	GuardrailResults []GuardrailResult

	// Multimodal request/response pairs — only one pair is set per request.
	EmbeddingRequest  *EmbeddingRequest
	EmbeddingResponse *EmbeddingResponse
	ImageRequest      *ImageRequest
	ImageResponse     *ImageResponse
	SpeechRequest     *SpeechRequest
	TranscriptionReq  *TranscriptionRequest
	TranscriptionResp *TranscriptionResponse
	RerankRequest     *RerankRequest
	RerankResponse    *RerankResponse
	TranslationReq    *TranslationRequest
	TranslationResp   *TranslationResponse
	SearchRequest     *SearchRequest
	SearchResponse    *SearchResponse
	OCRRequest        *OCRRequest
	OCRResponse       *OCRResponse

	// Per-org model map: model name → provider ID override.
	OrgModelMap map[string]string

	// Per-org model fallback chains: model → ordered fallback models.
	OrgModelFallbacks map[string][]string
}

type RequestFlags struct {
	CacheHit           bool
	GuardrailTriggered bool
	FallbackUsed       bool
	Timeout            bool
	ShortCircuited     bool
}

var requestContextPool = sync.Pool{
	New: func() any {
		return &RequestContext{
			Metadata: make(map[string]string, 8),
			Timings:  make(map[string]time.Duration, 8),
			Errors:   make([]error, 0, 4),
		}
	},
}

// AcquireRequestContext gets a RequestContext from the pool.
func AcquireRequestContext() *RequestContext {
	rc := requestContextPool.Get().(*RequestContext)
	rc.StartTime = time.Now()
	rc.EndpointType = "chat"
	return rc
}

// Release returns the RequestContext to the pool after resetting it.
func (rc *RequestContext) Release() {
	rc.RequestID = ""
	rc.TraceID = ""
	rc.StartTime = time.Time{}
	rc.Model = ""
	rc.ResolvedModel = ""
	rc.Provider = ""
	rc.Request = nil
	rc.Response = nil
	rc.IsStream = false
	rc.SessionID = ""
	rc.UserID = ""
	rc.Flags = RequestFlags{}
	rc.EndpointType = ""
	rc.EmbeddingRequest = nil
	rc.EmbeddingResponse = nil
	rc.ImageRequest = nil
	rc.ImageResponse = nil
	rc.SpeechRequest = nil
	rc.TranscriptionReq = nil
	rc.TranscriptionResp = nil
	rc.RerankRequest = nil
	rc.RerankResponse = nil
	rc.TranslationReq = nil
	rc.TranslationResp = nil
	rc.SearchRequest = nil
	rc.SearchResponse = nil
	rc.OCRRequest = nil
	rc.OCRResponse = nil
	rc.OrgModelMap = nil
	rc.OrgModelFallbacks = nil
	rc.GuardrailResults = rc.GuardrailResults[:0]
	rc.RequestHeaders = nil

	// Reuse maps by clearing them (keeps allocated memory).
	for k := range rc.Metadata {
		delete(rc.Metadata, k)
	}
	for k := range rc.Timings {
		delete(rc.Timings, k)
	}
	rc.Errors = rc.Errors[:0]

	requestContextPool.Put(rc)
}

// Elapsed returns the time since the request started.
func (rc *RequestContext) Elapsed() time.Duration {
	return time.Since(rc.StartTime)
}

// Snapshot creates an immutable copy of the RequestContext for async operations.
// Call this before Release() when data is needed after the handler returns.
func (rc *RequestContext) Snapshot() *TraceSnapshot {
	meta := make(map[string]string, len(rc.Metadata))
	for k, v := range rc.Metadata {
		meta[k] = v
	}
	timings := make(map[string]time.Duration, len(rc.Timings))
	for k, v := range rc.Timings {
		timings[k] = v
	}
	errs := make([]error, len(rc.Errors))
	copy(errs, rc.Errors)
	grResults := make([]GuardrailResult, len(rc.GuardrailResults))
	copy(grResults, rc.GuardrailResults)

	return &TraceSnapshot{
		RequestID:        rc.RequestID,
		TraceID:          rc.TraceID,
		StartTime:        rc.StartTime,
		Model:            rc.Model,
		ResolvedModel:    rc.ResolvedModel,
		Provider:         rc.Provider,
		Request:          rc.Request,
		Response:         rc.Response,
		IsStream:         rc.IsStream,
		Metadata:         meta,
		SessionID:        rc.SessionID,
		UserID:           rc.UserID,
		Flags:            rc.Flags,
		Timings:          timings,
		Errors:           errs,
		GuardrailResults: grResults,
		RequestHeaders:   rc.RequestHeaders.Clone(),
	}
}

// AddError appends a non-fatal error.
func (rc *RequestContext) AddError(err error) {
	rc.Errors = append(rc.Errors, err)
}

// RecordTiming records a named duration. Thread-safe for parallel post-plugins.
func (rc *RequestContext) RecordTiming(name string, d time.Duration) {
	rc.mu.Lock()
	rc.Timings[name] = d
	rc.mu.Unlock()
}

// SetMetadata sets a metadata key. Thread-safe for parallel post-plugins.
func (rc *RequestContext) SetMetadata(key, value string) {
	rc.mu.Lock()
	rc.Metadata[key] = value
	rc.mu.Unlock()
}

// Context key types for storing request-scoped values.
type contextKey int

const (
	ContextKeyRequestID contextKey = iota
	ContextKeyTraceID
	ContextKeyRequestContext
)

// WithRequestID stores the request ID in context.
func WithRequestID(ctx context.Context, id string) context.Context {
	return context.WithValue(ctx, ContextKeyRequestID, id)
}

// GetRequestID retrieves the request ID from context.
func GetRequestID(ctx context.Context) string {
	if id, ok := ctx.Value(ContextKeyRequestID).(string); ok {
		return id
	}
	return ""
}

// WithRequestContext stores the RequestContext in context.
func WithRequestContext(ctx context.Context, rc *RequestContext) context.Context {
	return context.WithValue(ctx, ContextKeyRequestContext, rc)
}

// GetRequestContext retrieves the RequestContext from context.
func GetRequestContext(ctx context.Context) *RequestContext {
	if rc, ok := ctx.Value(ContextKeyRequestContext).(*RequestContext); ok {
		return rc
	}
	return nil
}
