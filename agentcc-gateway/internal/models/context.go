package models

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"strings"
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

// ToolCallGuardResult is what the tool-call guardrail did to a response's tool
// calls (log-entry-v2 guardrail.tool_calls): how many it checked, and the ids
// of the calls it blocked or stripped.
type ToolCallGuardResult struct {
	Checked  int
	Blocked  []string
	Stripped []string
}

func (t ToolCallGuardResult) clone() ToolCallGuardResult {
	t.Blocked = cloneStrings(t.Blocked)
	t.Stripped = cloneStrings(t.Stripped)
	return t
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

	// The v2 request state, deep-copied.
	RequestV2
}

// RequestV2 is a deep copy of a request's v2 state (see RequestContext):
// what the request log, the generation record and other consumers read
// after the handler returns.
type RequestV2 struct {
	Agent         AgentContext
	Routing       RoutingOptions
	Billing       BillingState
	Evidence      RoutingEvidence
	Privacy       Privacy
	Runtime       *RuntimeIdentity
	ToolCallGuard bool
	ToolCalls     ToolCallGuardResult
	Attempts      []Attempt
	// CustomMetadataKeys are the Metadata keys the caller supplied, as
	// opposed to the gateway's own.
	CustomMetadataKeys []string
	// LogEvidence is RequestContext.LogEvidence, copied.
	LogEvidence map[string]json.RawMessage
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

	// mu serializes writes to Timings and Metadata between the goroutines that
	// legitimately write them — handlers, pre-plugins, sequential post-plugins,
	// streaming callbacks.
	//
	// It does NOT make the parallel post-plugin window safe, and must not be
	// relied on for that: those plugins read both maps directly, so any write
	// during that window is a concurrent map read/write and a runtime fatal,
	// mutex or no mutex. The window is read-only by contract — see
	// pipeline.PostParallel.
	mu sync.Mutex

	// RequestHeaders stores the original HTTP request headers for logging.
	RequestHeaders http.Header

	// EndpointType identifies the API endpoint: "chat", "embedding", "image",
	// "speech", "transcription", or "rerank". Defaults to "chat".
	EndpointType string

	// Guardrail results — populated by the guardrails plugin with triggered checks.
	GuardrailResults []GuardrailResult

	// PassThrough is the request as the guardrail checks read it where
	// Request is not (see PassThroughRequest): the body of a request the
	// handler sends to the provider's own API as the client sent it, or a
	// request of an API other than chat (embeddings, images, audio, rerank,
	// search, OCR, a video job, the Assistants API, vector store search).
	PassThrough PassThroughRequest

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

	// CustomMetadataKeys lists the Metadata keys that came from the caller's
	// x-agentcc-metadata header. Metadata also holds ~120 internal keys written
	// by plugins, so this is the only reliable way to tell caller-supplied
	// dimensions apart from gateway internals when exporting telemetry.
	CustomMetadataKeys []string

	// CallerExtras holds the body's unknown top-level fields (extra_body, plus
	// every OpenAI param newer than UnmarshalJSON's `known` map). Scalars only.
	//
	// Snapshotted in the handler, not read from Request.Extra at export time:
	// the translation layer writes its own state into that same map, so
	// provenance is what keeps gateway internals out.
	CallerExtras map[string]any

	// CallerExtrasDropped counts fields left out — non-scalars and
	// credential-shaped key names.
	CallerExtrasDropped int

	// CredentialSource is whose credentials the last provider call made for
	// this request used: "org" for an instance built from the org's own
	// provider config, "platform" for the gateway's own. "" until a provider
	// is called. Provider names the routing provider either way.
	CredentialSource string

	// v2 request state. Each is written by the component that owns its domain,
	// deep-copied by Snapshot and cleared by Release.
	Agent    AgentContext    // the caller's agent identity
	Routing  RoutingOptions  // the caller's routing fields
	Billing  BillingState    // how the request is billed
	Evidence RoutingEvidence // what routing decided
	Privacy  Privacy         // the resolved content mode
	// Runtime is set when a runtime token authenticated the call.
	Runtime *RuntimeIdentity
	// ToolCallGuard is set when the tool-call guardrail is armed for this
	// request; ToolCalls is what it did.
	ToolCallGuard bool
	ToolCalls     ToolCallGuardResult

	// LogEvidence holds the request's log-entry evidence by name, each value
	// JSON. Set it with
	// SetLogEvidence; the request log writes each entry as evidence.<name>,
	// except identity (agent.identity) and eval_export (the top-level
	// eval_export). It holds numbers, ids and flags, never content, so every
	// content mode keeps it. Nil until the first SetLogEvidence.
	LogEvidence map[string]json.RawMessage

	// Under mu: the attempts made so far (AddAttempt), the finalizers to run on
	// exit (AddFinalizer) and the response headers set by plugins and handlers
	// (SetResponseHeader). Each is allocated on first use.
	attempts        []Attempt
	finalizers      []func()
	finalizersOnce  sync.Once
	finalized       bool
	responseHeaders []ResponseHeader
}

// ResponseHeader is one header set with SetResponseHeader.
type ResponseHeader struct {
	Name  string
	Value string
}

type RequestFlags struct {
	CacheHit           bool
	GuardrailTriggered bool
	FallbackUsed       bool
	Timeout            bool
	ShortCircuited     bool
	// AnswerRefused is set when a guardrail refused an answer it was given:
	// an output check withheld it, or the streaming checker stopped it. The
	// refusal's error is on Errors, but whoever answered did not fail, so
	// provider health does not count it as a provider error.
	AnswerRefused bool
	// AnswerReaders is which output checks will read the request's answer,
	// set by the guardrail plugin before the provider call. When it is not
	// zero, a handler that would otherwise give the post-plugins only the
	// answer's usage (one that relays the provider's own answer, or a stream
	// it does not assemble for the request log) puts the answer itself on
	// Response, so the checks read what the client gets.
	AnswerReaders AnswerReaders
	// AnswerUnavailable is set by a handler when the gateway will not get,
	// or could not read, the answer the client gets (Metadata
	// "answer_unavailable" says why), so no output check can read it. The
	// guardrail plugin then refuses the request, or withholds the answer,
	// when a check on the answer would block: block mode must not let an
	// answer out unchecked.
	AnswerUnavailable bool
}

// PassThroughRequest is a request the guardrail checks read in the chat
// shape where the handler sets no chat request: a body the handler sends to
// the provider's own API as the client sent it (/v1/messages to an
// Anthropic-format provider, /v1beta to a Google-format one, /v1/responses,
// and the Assistants API and vector store search proxied to OpenAI), and a
// request of an API other than chat that the handler sends from its own
// type (embeddings, images, speech, transcriptions and translations,
// rerank, search, OCR, a video job). The handler leaves Request nil on those
// paths, because the cache, the request log and the other plugins that read
// Request would act on a request the provider never gets. The guardrail
// plugin reads it (and the Responses handler, to estimate a held request's
// prompt).
type PassThroughRequest interface {
	// ChatRequest is the request in the chat shape, which the checks read.
	// reason says why when part or all of the request cannot be read: req
	// then holds what can be read (a transcription's prompt, but not its
	// audio; a body above a value nested too deep to read), and is nil when
	// nothing can (a body that does not have its API's shape, token ids, an
	// OCR document). The first call reads the request; the read stops when
	// ctx ends (the request is then unread), and later calls return what it
	// read.
	ChatRequest(ctx context.Context) (req *ChatCompletionRequest, reason string)
	// ApplyChecks writes into the body the handler sends what the checks
	// rewrote in ChatRequest's messages (a remediation: pii-detection's
	// mask, redact or hash), as a chat request's rewrite reaches the
	// provider on /v1/chat/completions. It returns an error, and leaves the
	// body as it was, when the rewrite cannot be written there; the
	// guardrail plugin then refuses the request.
	ApplyChecks() error
}

// NoChatAnswer is a PassThroughRequest of an API whose answer is not model
// text the checks on the answer read (embeddings, images, speech, rerank,
// search, a transcription, OCR, embedContent, and the token count of
// /v1/messages/count_tokens and countTokens). The checks on the request read
// it; the checks on the answer, which run after the provider answered, get
// rc.Request with the answer and never read this request, so they record no
// block on it and send none of its text to an evaluator after the call.
type NoChatAnswer interface {
	NoChatAnswer()
}

// MarkAnswerUnavailable records that no output check can read the request's
// answer, and why (RequestFlags.AnswerUnavailable).
func (rc *RequestContext) MarkAnswerUnavailable(reason string) {
	rc.Flags.AnswerUnavailable = true
	rc.Metadata["answer_unavailable"] = reason
}

// WithheldAnswerError is the error a post-plugin recorded when it withheld
// the answer (a guardrail's post-stage block drops rc.Response and records
// its 403 content_blocked on rc), or nil when no answer was withheld. Call it
// only when the pipeline returned no error: the engine records each error it
// returns, so the only errors on rc then are a withholding post-plugin's.
// Every caller that serves an answer once the post-plugins have run reads it
// (the server's handlers through pipelineErr, and the a2a server's own
// pipeline path), and answers the refusal instead of the missing answer.
func WithheldAnswerError(rc *RequestContext) *APIError {
	if rc.Response != nil || len(rc.Errors) == 0 {
		return nil
	}
	var apiErr *APIError
	if errors.As(rc.Errors[len(rc.Errors)-1], &apiErr) {
		return apiErr
	}
	return nil
}

// AnswerReaders is a set of the output checks that read a request's answer.
type AnswerReaders uint8

const (
	// AnswerReadByRules: a rule in the gateway's config file at the post
	// stage.
	AnswerReadByRules AnswerReaders = 1 << iota
	// AnswerReadByOrg: an organization's check whose type reads the answer.
	AnswerReadByOrg
)

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

// Release returns the RequestContext to the pool after resetting it. It first
// runs any finalizers not yet run, so a handler that defers Release runs them
// on every exit path even without its own deferred RunFinalizers.
func (rc *RequestContext) Release() {
	rc.RunFinalizers()
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
	rc.PassThrough = nil
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
	rc.CustomMetadataKeys = rc.CustomMetadataKeys[:0]
	rc.CallerExtras = nil
	rc.CallerExtrasDropped = 0
	rc.CredentialSource = ""
	rc.Agent = AgentContext{}
	rc.Routing = RoutingOptions{}
	rc.Billing = BillingState{}
	rc.Evidence = RoutingEvidence{}
	rc.Privacy = Privacy{}
	rc.Runtime = nil
	rc.ToolCallGuard = false
	rc.ToolCalls = ToolCallGuardResult{}
	rc.LogEvidence = nil
	// Keep the attempts' backing array for the next request (one allocation
	// fewer per request), zeroed so no attempt outlives its request.
	clear(rc.attempts)
	rc.attempts = rc.attempts[:0]
	rc.finalizers = nil
	rc.finalizersOnce = sync.Once{}
	rc.finalized = false
	rc.responseHeaders = nil

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
		RequestV2:        rc.SnapshotV2(),
	}
}

// SnapshotV2 deep-copies rc's v2 state, for a consumer that copies the v1
// fields its own way (the request log).
func (rc *RequestContext) SnapshotV2() RequestV2 {
	return RequestV2{
		Agent:              rc.Agent.Clone(),
		Routing:            rc.Routing.Clone(),
		Billing:            rc.Billing.Clone(),
		Evidence:           rc.Evidence,
		Privacy:            rc.Privacy,
		Runtime:            rc.Runtime.Clone(),
		ToolCallGuard:      rc.ToolCallGuard,
		ToolCalls:          rc.ToolCalls.clone(),
		Attempts:           rc.AttemptsSnapshot(),
		CustomMetadataKeys: cloneStrings(rc.CustomMetadataKeys),
		LogEvidence:        rc.logEvidenceCopy(),
	}
}

// SetLogEvidence records one log-entry evidence entry: v is stored as
// JSON under name, replacing an earlier entry of that name. The request log
// writes it as evidence.<name>, or agent.identity for "identity" and the
// top-level eval_export for "eval_export". A value that does not encode as
// JSON is dropped. Safe to call from any goroutine that may write Metadata.
func (rc *RequestContext) SetLogEvidence(name string, v any) {
	raw, err := json.Marshal(v)
	if err != nil {
		slog.Warn("log evidence not recorded: value does not encode as JSON", "name", name, "error", err)
		return
	}
	rc.mu.Lock()
	if rc.LogEvidence == nil {
		rc.LogEvidence = make(map[string]json.RawMessage, 2)
	}
	rc.LogEvidence[name] = raw
	rc.mu.Unlock()
}

// logEvidenceCopy is a copy of LogEvidence, nil when it is empty. The values
// are never written after SetLogEvidence stores them, so they are shared.
func (rc *RequestContext) logEvidenceCopy() map[string]json.RawMessage {
	rc.mu.Lock()
	defer rc.mu.Unlock()
	if len(rc.LogEvidence) == 0 {
		return nil
	}
	out := make(map[string]json.RawMessage, len(rc.LogEvidence))
	for k, v := range rc.LogEvidence {
		out[k] = v
	}
	return out
}

// CloneRequest returns a pooled RequestContext holding a copy of rc's chat
// request state: identity, request, routing result and metadata, with maps and
// slices copied and the request struct copied so the clone can rewrite its
// model. Responses, timings, errors and guardrail results are not copied.
// Work that runs after the handler has released rc (an async job) runs on the
// clone, and releases it when done.
func (rc *RequestContext) CloneRequest() *RequestContext {
	cp := AcquireRequestContext()
	cp.RequestID = rc.RequestID
	cp.TraceID = rc.TraceID
	cp.Model = rc.Model
	cp.Provider = rc.Provider
	if rc.Request != nil {
		req := *rc.Request
		cp.Request = &req
	}
	cp.IsStream = rc.IsStream
	for k, v := range rc.Metadata {
		cp.Metadata[k] = v
	}
	cp.SessionID = rc.SessionID
	cp.UserID = rc.UserID
	cp.Flags = rc.Flags
	cp.RequestHeaders = rc.RequestHeaders.Clone()
	cp.EndpointType = rc.EndpointType
	if rc.OrgModelMap != nil {
		cp.OrgModelMap = make(map[string]string, len(rc.OrgModelMap))
		for k, v := range rc.OrgModelMap {
			cp.OrgModelMap[k] = v
		}
	}
	if rc.OrgModelFallbacks != nil {
		cp.OrgModelFallbacks = make(map[string][]string, len(rc.OrgModelFallbacks))
		for k, v := range rc.OrgModelFallbacks {
			cp.OrgModelFallbacks[k] = append([]string(nil), v...)
		}
	}
	cp.CustomMetadataKeys = append(cp.CustomMetadataKeys[:0], rc.CustomMetadataKeys...)
	if rc.CallerExtras != nil {
		cp.CallerExtras = make(map[string]any, len(rc.CallerExtras))
		for k, v := range rc.CallerExtras {
			cp.CallerExtras[k] = v
		}
	}
	cp.CallerExtrasDropped = rc.CallerExtrasDropped
	// The v2 state the handler resolved before admitting the job: the job's
	// pipeline bills, routes and stores content by it exactly as the request
	// would have. Attempts, finalizers and response headers are the job's own.
	cp.Agent = rc.Agent.Clone()
	cp.Routing = rc.Routing.Clone()
	cp.Billing = rc.Billing.Clone()
	cp.Evidence = rc.Evidence
	cp.Privacy = rc.Privacy
	cp.Runtime = rc.Runtime.Clone()
	cp.ToolCallGuard = rc.ToolCallGuard
	cp.ToolCalls = rc.ToolCalls.clone()
	cp.LogEvidence = rc.logEvidenceCopy()
	return cp
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

// DeleteMetadata removes a metadata key. Thread-safe for parallel
// post-plugins.
func (rc *RequestContext) DeleteMetadata(key string) {
	rc.mu.Lock()
	delete(rc.Metadata, key)
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

// WithTraceID stores a trace ID in context. A request's own trace id travels
// in its x-agentcc-trace-id header; this carries an A2A request's trace id to
// the run of the task it starts, which has a context of its own.
func WithTraceID(ctx context.Context, id string) context.Context {
	return context.WithValue(ctx, ContextKeyTraceID, id)
}

// GetTraceID retrieves the trace ID WithTraceID stored, or "".
func GetTraceID(ctx context.Context) string {
	if id, ok := ctx.Value(ContextKeyTraceID).(string); ok {
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

// AddFinalizer registers fn to run when the request ends. Finalizers run once,
// in LIFO order, on every exit path (RunFinalizers); a panicking finalizer is
// logged and the rest still run. One added after the finalizers have run runs
// at once, so a hold taken late is still released.
func (rc *RequestContext) AddFinalizer(fn func()) {
	rc.mu.Lock()
	if !rc.finalized {
		rc.finalizers = append(rc.finalizers, fn)
		rc.mu.Unlock()
		return
	}
	rc.mu.Unlock()
	runFinalizer(fn)
}

// RunFinalizers runs the finalizers registered with AddFinalizer, most recent
// first. Only the first call runs them; later calls wait for it to finish.
// Every public handler defers it (Release also calls it).
func (rc *RequestContext) RunFinalizers() {
	rc.finalizersOnce.Do(rc.runFinalizers)
}

func (rc *RequestContext) runFinalizers() {
	rc.mu.Lock()
	rc.finalized = true
	fns := rc.finalizers
	rc.finalizers = nil
	rc.mu.Unlock()
	for i := len(fns) - 1; i >= 0; i-- {
		runFinalizer(fns[i])
	}
}

func runFinalizer(fn func()) {
	defer func() {
		if p := recover(); p != nil {
			slog.Error("request finalizer panicked", "panic", p)
		}
	}()
	fn()
}

// SetResponseHeader sets a response header for the request. The response
// writer applies every one with ApplyResponseHeaders when it writes the
// gateway's headers, so a plugin or handler that needs its own response header
// sets it here and never edits the header code. Setting a name again replaces
// it.
func (rc *RequestContext) SetResponseHeader(name, value string) {
	rc.mu.Lock()
	defer rc.mu.Unlock()
	for i := range rc.responseHeaders {
		if strings.EqualFold(rc.responseHeaders[i].Name, name) {
			rc.responseHeaders[i].Value = value
			return
		}
	}
	rc.responseHeaders = append(rc.responseHeaders, ResponseHeader{Name: name, Value: value})
}

// ResponseHeaders returns a copy of the headers set with SetResponseHeader, in
// the order they were first set, or nil.
func (rc *RequestContext) ResponseHeaders() []ResponseHeader {
	rc.mu.Lock()
	defer rc.mu.Unlock()
	if len(rc.responseHeaders) == 0 {
		return nil
	}
	return append([]ResponseHeader(nil), rc.responseHeaders...)
}

// ApplyResponseHeaders sets every header from SetResponseHeader on h.
func (rc *RequestContext) ApplyResponseHeaders(h http.Header) {
	rc.mu.Lock()
	defer rc.mu.Unlock()
	for _, kv := range rc.responseHeaders {
		h.Set(kv.Name, kv.Value)
	}
}
