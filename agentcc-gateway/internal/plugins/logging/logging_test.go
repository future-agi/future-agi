package logging

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// newRC creates a minimal RequestContext for testing. StartTime is set
// slightly in the past so Elapsed() returns a positive duration.
func newRC() *models.RequestContext {
	return &models.RequestContext{
		RequestID:     "req-123",
		TraceID:       "trace-456",
		StartTime:     time.Now().Add(-50 * time.Millisecond),
		Model:         "gpt-4",
		ResolvedModel: "gpt-4-0613",
		Provider:      "openai",
		IsStream:      false,
		UserID:        "user-1",
		SessionID:     "sess-1",
		Metadata:      make(map[string]string),
		Timings:       make(map[string]time.Duration),
		Errors:        nil,
	}
}

func enabledCfg() config.RequestLoggingConfig {
	return config.RequestLoggingConfig{
		Enabled:       true,
		IncludeBodies: false,
		BufferSize:    64,
		Workers:       1,
	}
}

func disabledCfg() config.RequestLoggingConfig {
	return config.RequestLoggingConfig{
		Enabled: false,
	}
}

// capturingHandler is a slog handler that records log records for inspection.
type capturingHandler struct {
	mu      sync.Mutex
	records []slog.Record
}

func (h *capturingHandler) Enabled(_ context.Context, _ slog.Level) bool { return true }
func (h *capturingHandler) Handle(_ context.Context, r slog.Record) error {
	h.mu.Lock()
	h.records = append(h.records, r)
	h.mu.Unlock()
	return nil
}
func (h *capturingHandler) WithAttrs(_ []slog.Attr) slog.Handler { return h }
func (h *capturingHandler) WithGroup(_ string) slog.Handler      { return h }

func (h *capturingHandler) count() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.records)
}

func (h *capturingHandler) get(i int) slog.Record {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.records[i]
}

// findAttr looks for a named attribute in a slog.Record.
func findAttr(r slog.Record, key string) (slog.Value, bool) {
	var val slog.Value
	var found bool
	r.Attrs(func(a slog.Attr) bool {
		if a.Key == key {
			val = a.Value
			found = true
			return false
		}
		return true
	})
	return val, found
}

// installCapturingLogger replaces the default slog logger with one that
// captures records. It returns the handler and a cleanup function.
func installCapturingLogger() (*capturingHandler, func()) {
	h := &capturingHandler{}
	prev := slog.Default()
	slog.SetDefault(slog.New(h))
	return h, func() { slog.SetDefault(prev) }
}

// ---------------------------------------------------------------------------
// Plugin basics
// ---------------------------------------------------------------------------

func TestPlugin_Name(t *testing.T) {
	p := New(disabledCfg(), nil)
	if got := p.Name(); got != "logging" {
		t.Errorf("Name() = %q, want %q", got, "logging")
	}
}

func TestPlugin_Priority(t *testing.T) {
	p := New(disabledCfg(), nil)
	if got := p.Priority(); got != 900 {
		t.Errorf("Priority() = %d, want 900", got)
	}
}

func TestPlugin_ProcessRequest_Noop(t *testing.T) {
	p := New(enabledCfg(), nil)
	defer p.Close()

	rc := newRC()
	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Errorf("ProcessRequest Action = %v, want Continue", result.Action)
	}
	if result.Response != nil {
		t.Error("ProcessRequest should return nil Response")
	}
	if result.Error != nil {
		t.Error("ProcessRequest should return nil Error")
	}
}

// ---------------------------------------------------------------------------
// Record construction
// ---------------------------------------------------------------------------

func TestBuildRecord_BasicFields(t *testing.T) {
	rc := newRC()
	rc.Response = &models.ChatCompletionResponse{}
	rec := buildRecord(rc, enabledCfg())

	if rec.RequestID != "req-123" {
		t.Errorf("RequestID = %q, want %q", rec.RequestID, "req-123")
	}
	if rec.TraceID != "trace-456" {
		t.Errorf("TraceID = %q, want %q", rec.TraceID, "trace-456")
	}
	if rec.Model != "gpt-4" {
		t.Errorf("Model = %q, want %q", rec.Model, "gpt-4")
	}
	if rec.ResolvedModel != "gpt-4-0613" {
		t.Errorf("ResolvedModel = %q, want %q", rec.ResolvedModel, "gpt-4-0613")
	}
	if rec.Provider != "openai" {
		t.Errorf("Provider = %q, want %q", rec.Provider, "openai")
	}
	if rec.IsStream {
		t.Error("IsStream should be false")
	}
	if rec.UserID != "user-1" {
		t.Errorf("UserID = %q, want %q", rec.UserID, "user-1")
	}
	if rec.SessionID != "sess-1" {
		t.Errorf("SessionID = %q, want %q", rec.SessionID, "sess-1")
	}
	if rec.Timestamp.IsZero() {
		t.Error("Timestamp should not be zero")
	}
	if rec.LatencyMs < 0 {
		t.Errorf("LatencyMs = %d, want >= 0", rec.LatencyMs)
	}
}

func TestBuildRecord_ResponseMetrics(t *testing.T) {
	rc := newRC()
	rc.Response = &models.ChatCompletionResponse{
		Usage: &models.Usage{
			PromptTokens:     100,
			CompletionTokens: 50,
			TotalTokens:      150,
		},
		Choices: []models.Choice{
			{FinishReason: "stop"},
		},
	}
	rec := buildRecord(rc, enabledCfg())

	if rec.PromptTokens != 100 {
		t.Errorf("PromptTokens = %d, want 100", rec.PromptTokens)
	}
	if rec.CompletionTokens != 50 {
		t.Errorf("CompletionTokens = %d, want 50", rec.CompletionTokens)
	}
	if rec.TotalTokens != 150 {
		t.Errorf("TotalTokens = %d, want 150", rec.TotalTokens)
	}
	if rec.FinishReason != "stop" {
		t.Errorf("FinishReason = %q, want %q", rec.FinishReason, "stop")
	}
	if rec.StatusCode != 200 {
		t.Errorf("StatusCode = %d, want 200", rec.StatusCode)
	}
}

func TestBuildRecord_AuthMetadata(t *testing.T) {
	rc := newRC()
	rc.Metadata["auth_key_id"] = "key-abc"
	rc.Metadata["auth_key_name"] = "my-key"
	rc.Metadata["auth_key_owner"] = "alice"

	rec := buildRecord(rc, enabledCfg())

	if rec.AuthKeyID != "key-abc" {
		t.Errorf("AuthKeyID = %q, want %q", rec.AuthKeyID, "key-abc")
	}
	if rec.AuthKeyName != "my-key" {
		t.Errorf("AuthKeyName = %q, want %q", rec.AuthKeyName, "my-key")
	}
	if rec.AuthKeyOwner != "alice" {
		t.Errorf("AuthKeyOwner = %q, want %q", rec.AuthKeyOwner, "alice")
	}
}

func TestBuildRecord_ErrorFields(t *testing.T) {
	rc := newRC()
	apiErr := &models.APIError{
		Status:  429,
		Code:    "rate_limit_exceeded",
		Message: "too many requests",
	}
	rc.Errors = []error{apiErr}

	rec := buildRecord(rc, enabledCfg())

	if rec.StatusCode != 429 {
		t.Errorf("StatusCode = %d, want 429", rec.StatusCode)
	}
	if rec.ErrorMessage != "too many requests" {
		t.Errorf("ErrorMessage = %q, want %q", rec.ErrorMessage, "too many requests")
	}
	if rec.ErrorCode != "rate_limit_exceeded" {
		t.Errorf("ErrorCode = %q, want %q", rec.ErrorCode, "rate_limit_exceeded")
	}
}

func TestBuildRecord_GenericError(t *testing.T) {
	rc := newRC()
	rc.Errors = []error{errors.New("something broke")}

	rec := buildRecord(rc, enabledCfg())

	if rec.StatusCode != 500 {
		t.Errorf("StatusCode = %d, want 500", rec.StatusCode)
	}
	if rec.ErrorMessage != "something broke" {
		t.Errorf("ErrorMessage = %q, want %q", rec.ErrorMessage, "something broke")
	}
}

func TestBuildRecord_Timeout(t *testing.T) {
	rc := newRC()
	rc.Flags.Timeout = true

	rec := buildRecord(rc, enabledCfg())

	if rec.StatusCode != 408 {
		t.Errorf("StatusCode = %d, want 408", rec.StatusCode)
	}
	if !rec.Timeout {
		t.Error("Timeout flag should be true")
	}
}

func TestBuildRecord_Timings(t *testing.T) {
	rc := newRC()
	rc.Timings["auth"] = 1500 * time.Microsecond
	rc.Timings["provider"] = 35 * time.Millisecond

	rec := buildRecord(rc, enabledCfg())

	if rec.Timings == nil {
		t.Fatal("Timings should not be nil")
	}
	if got, want := rec.Timings["auth"], int64(1500); got != want {
		t.Errorf("Timings[auth] = %d, want %d", got, want)
	}
	if got, want := rec.Timings["provider"], int64(35000); got != want {
		t.Errorf("Timings[provider] = %d, want %d", got, want)
	}
}

func TestBuildRecord_MetadataExcludesAuthorization(t *testing.T) {
	rc := newRC()
	rc.Metadata["authorization"] = "Bearer sk-secret"
	rc.Metadata["custom_tag"] = "prod"

	rec := buildRecord(rc, enabledCfg())

	if rec.Metadata == nil {
		t.Fatal("Metadata should not be nil")
	}
	if _, ok := rec.Metadata["authorization"]; ok {
		t.Error("authorization key should be excluded from record metadata")
	}
	if rec.Metadata["custom_tag"] != "prod" {
		t.Errorf("Metadata[custom_tag] = %q, want %q", rec.Metadata["custom_tag"], "prod")
	}
}

func TestBuildRecord_IncludeBodies(t *testing.T) {
	rc := newRC()
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4"}
	rc.Response = &models.ChatCompletionResponse{ID: "resp-1"}

	cfg := enabledCfg()
	cfg.IncludeBodies = true

	rec := buildRecord(rc, cfg)

	if rec.RequestBody == nil {
		t.Error("RequestBody should be non-nil when include_bodies is true")
	}
	if rec.ResponseBody == nil {
		t.Error("ResponseBody should be non-nil when include_bodies is true")
	}
	if rec.RequestBody.Model != "gpt-4" {
		t.Errorf("RequestBody.Model = %q, want %q", rec.RequestBody.Model, "gpt-4")
	}
	if rec.ResponseBody.ID != "resp-1" {
		t.Errorf("ResponseBody.ID = %q, want %q", rec.ResponseBody.ID, "resp-1")
	}
}

func TestBuildRecord_ExcludeBodies(t *testing.T) {
	rc := newRC()
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4"}
	rc.Response = &models.ChatCompletionResponse{ID: "resp-1"}

	cfg := enabledCfg()
	cfg.IncludeBodies = false

	rec := buildRecord(rc, cfg)

	if rec.RequestBody != nil {
		t.Error("RequestBody should be nil when include_bodies is false")
	}
	if rec.ResponseBody != nil {
		t.Error("ResponseBody should be nil when include_bodies is false")
	}
}

func TestBuildRecord_IncludeHeaders(t *testing.T) {
	rc := newRC()
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4"}
	rc.Response = &models.ChatCompletionResponse{ID: "resp-1"}
	rc.RequestHeaders = http.Header{
		"Content-Type":  {"application/json"},
		"Authorization": {"Bearer sk-secret"},
		"X-Custom":      {"value1", "value2"},
	}

	cfg := enabledCfg()
	cfg.IncludeBodies = true

	rec := buildRecord(rc, cfg)

	if rec.RequestHeaders == nil {
		t.Fatal("RequestHeaders should be non-nil when include_bodies is true and headers are set")
	}
	if rec.RequestHeaders["Content-Type"][0] != "application/json" {
		t.Errorf("Content-Type = %q, want %q", rec.RequestHeaders["Content-Type"][0], "application/json")
	}
	if rec.RequestHeaders["Authorization"][0] != "***" {
		t.Errorf("Authorization should be sanitized to ***, got %q", rec.RequestHeaders["Authorization"][0])
	}
	if len(rec.RequestHeaders["X-Custom"]) != 2 {
		t.Errorf("X-Custom should have 2 values, got %d", len(rec.RequestHeaders["X-Custom"]))
	}
}

func TestBuildRecord_ExcludeHeaders(t *testing.T) {
	rc := newRC()
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4"}
	rc.Response = &models.ChatCompletionResponse{ID: "resp-1"}
	rc.RequestHeaders = http.Header{
		"Content-Type": {"application/json"},
	}

	cfg := enabledCfg()
	cfg.IncludeBodies = false

	rec := buildRecord(rc, cfg)

	if rec.RequestHeaders != nil {
		t.Error("RequestHeaders should be nil when include_bodies is false")
	}
}

func TestBuildRecord_TranscriptionBodiesAndStatus(t *testing.T) {
	rc := newRC()
	rc.Model = "whisper-1"
	rc.ResolvedModel = ""
	rc.EndpointType = "transcription"
	rc.TranscriptionReq = &models.TranscriptionRequest{
		Model:          "whisper-1",
		FileName:       "sample.wav",
		FileData:       []byte("fake wav data"),
		Language:       "en",
		ResponseFormat: "json",
	}
	rc.TranscriptionResp = &models.TranscriptionResponse{Text: "hello there"}
	rc.Metadata["audio_seconds"] = "1.250000"

	cfg := enabledCfg()
	cfg.IncludeBodies = true

	rec := buildRecord(rc, cfg)

	if rec.StatusCode != 200 {
		t.Fatalf("StatusCode = %d, want 200", rec.StatusCode)
	}
	if rec.ResolvedModel != "whisper-1" {
		t.Fatalf("ResolvedModel = %q, want %q", rec.ResolvedModel, "whisper-1")
	}
	if rec.RequestBodyJSON == nil {
		t.Fatal("RequestBodyJSON should be set for transcription")
	}
	if rec.ResponseBodyJSON == nil {
		t.Fatal("ResponseBodyJSON should be set for transcription")
	}

	var reqBody map[string]any
	if err := json.Unmarshal(rec.RequestBodyJSON, &reqBody); err != nil {
		t.Fatalf("unmarshal request body: %v", err)
	}
	if reqBody["file_name"] != "sample.wav" {
		t.Fatalf("file_name = %v, want sample.wav", reqBody["file_name"])
	}
	if reqBody["audio_seconds"] != "1.250000" {
		t.Fatalf("audio_seconds = %v, want 1.250000", reqBody["audio_seconds"])
	}

	var respBody models.TranscriptionResponse
	if err := json.Unmarshal(rec.ResponseBodyJSON, &respBody); err != nil {
		t.Fatalf("unmarshal response body: %v", err)
	}
	if respBody.Text != "hello there" {
		t.Fatalf("respBody.Text = %q, want %q", respBody.Text, "hello there")
	}
}

func TestBuildRecord_SpeechBodiesAndStatus(t *testing.T) {
	rc := newRC()
	rc.Model = "tts-1"
	rc.ResolvedModel = ""
	rc.EndpointType = "speech"
	rc.SpeechRequest = &models.SpeechRequest{
		Model: "tts-1",
		Input: "hello world",
		Voice: "alloy",
	}
	rc.Metadata["response_content_type"] = "audio/mpeg"
	rc.Metadata["response_size_bytes"] = "1234"

	cfg := enabledCfg()
	cfg.IncludeBodies = true

	rec := buildRecord(rc, cfg)

	if rec.StatusCode != 200 {
		t.Fatalf("StatusCode = %d, want 200", rec.StatusCode)
	}
	if rec.ResolvedModel != "tts-1" {
		t.Fatalf("ResolvedModel = %q, want %q", rec.ResolvedModel, "tts-1")
	}
	if rec.RequestBodyJSON == nil {
		t.Fatal("RequestBodyJSON should be set for speech")
	}
	if rec.ResponseBodyJSON == nil {
		t.Fatal("ResponseBodyJSON should be set for speech")
	}

	var respBody map[string]any
	if err := json.Unmarshal(rec.ResponseBodyJSON, &respBody); err != nil {
		t.Fatalf("unmarshal response body: %v", err)
	}
	if respBody["content_type"] != "audio/mpeg" {
		t.Fatalf("content_type = %v, want audio/mpeg", respBody["content_type"])
	}
	if respBody["size_bytes"] != "1234" {
		t.Fatalf("size_bytes = %v, want 1234", respBody["size_bytes"])
	}
}

func TestRedactMessages_RedactsTextInContentArrays(t *testing.T) {
	r := privacy.New(privacy.ModeFull, nil)
	msgs := []models.Message{{
		Role: "user",
		Content: json.RawMessage(`[
			{"type":"text","text":"secret@example.com"},
			{"type":"image_url","image_url":{"url":"https://example.com/a.png"}}
		]`),
	}}

	got := redactMessages(msgs, r, privacy.ModeFull)

	var parts []map[string]any
	if err := json.Unmarshal(got[0].Content, &parts); err != nil {
		t.Fatalf("unmarshal redacted content: %v", err)
	}
	if parts[0]["text"] != "[REDACTED]" {
		t.Errorf("first text part = %v, want [REDACTED]", parts[0]["text"])
	}
	if parts[1]["type"] != "image_url" {
		t.Errorf("second part type = %v, want image_url", parts[1]["type"])
	}
}

func TestRedactChoices_RedactsTextInContentArrays(t *testing.T) {
	r := privacy.New(privacy.ModeFull, nil)
	choices := []models.Choice{{
		Message: models.Message{
			Role: "assistant",
			Content: json.RawMessage(`[
				{"type":"text","text":"super secret"},
				{"type":"text","text":" output"}
			]`),
		},
	}}

	got := redactChoices(choices, r, privacy.ModeFull)

	var parts []map[string]any
	if err := json.Unmarshal(got[0].Message.Content, &parts); err != nil {
		t.Fatalf("unmarshal redacted choice content: %v", err)
	}
	if parts[0]["text"] != "[REDACTED]" {
		t.Errorf("first text part = %v, want [REDACTED]", parts[0]["text"])
	}
	if parts[1]["text"] != "[REDACTED]" {
		t.Errorf("second text part = %v, want [REDACTED]", parts[1]["text"])
	}
}

func TestBuildRecord_NilResponse(t *testing.T) {
	rc := newRC()
	rc.Response = nil

	rec := buildRecord(rc, enabledCfg())

	// With no response and no errors, status code should be 0 (unless timeout).
	if rec.StatusCode != 0 {
		t.Errorf("StatusCode = %d, want 0 for nil response and no errors", rec.StatusCode)
	}
	if rec.PromptTokens != 0 {
		t.Errorf("PromptTokens = %d, want 0", rec.PromptTokens)
	}
	if rec.FinishReason != "" {
		t.Errorf("FinishReason = %q, want empty", rec.FinishReason)
	}
}

func TestBuildRecord_PipelineFlags(t *testing.T) {
	rc := newRC()
	rc.Flags.CacheHit = true
	rc.Flags.FallbackUsed = true
	rc.Flags.GuardrailTriggered = true
	rc.Flags.ShortCircuited = true
	rc.Response = &models.ChatCompletionResponse{}

	rec := buildRecord(rc, enabledCfg())

	if !rec.CacheHit {
		t.Error("CacheHit should be true")
	}
	if !rec.FallbackUsed {
		t.Error("FallbackUsed should be true")
	}
	if !rec.GuardrailTriggered {
		t.Error("GuardrailTriggered should be true")
	}
	if !rec.ShortCircuited {
		t.Error("ShortCircuited should be true")
	}
}

// ---------------------------------------------------------------------------
// Plugin ProcessResponse
// ---------------------------------------------------------------------------

func TestProcessResponse_EmitsRecord(t *testing.T) {
	handler, cleanup := installCapturingLogger()
	defer cleanup()

	cfg := enabledCfg()
	cfg.BufferSize = 10
	cfg.Workers = 1
	p := New(cfg, nil)

	rc := newRC()
	rc.Response = &models.ChatCompletionResponse{
		Usage: &models.Usage{PromptTokens: 10, CompletionTokens: 5, TotalTokens: 15},
	}

	result := p.ProcessResponse(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Errorf("ProcessResponse Action = %v, want Continue", result.Action)
	}

	// Close drains all buffered records.
	p.Close()

	if p.emitter.Dropped() != 0 {
		t.Errorf("Dropped() = %d, want 0", p.emitter.Dropped())
	}

	// Verify the slog handler captured the "request.trace" message.
	found := false
	for i := 0; i < handler.count(); i++ {
		rec := handler.get(i)
		if rec.Message == "request.trace" {
			found = true
			// Verify a key field was passed through.
			if v, ok := findAttr(rec, "request_id"); ok {
				if v.String() != "req-123" {
					t.Errorf("logged request_id = %q, want %q", v.String(), "req-123")
				}
			} else {
				t.Error("request_id attribute not found in log record")
			}
			break
		}
	}
	if !found {
		t.Error("expected a 'request.trace' log message but none was captured")
	}
}

func TestProcessResponse_Disabled(t *testing.T) {
	handler, cleanup := installCapturingLogger()
	defer cleanup()

	p := New(disabledCfg(), nil)

	rc := newRC()
	rc.Response = &models.ChatCompletionResponse{}

	p.ProcessResponse(context.Background(), rc)
	p.Close()

	// No "request.trace" should appear.
	for i := 0; i < handler.count(); i++ {
		rec := handler.get(i)
		if rec.Message == "request.trace" {
			t.Error("should not emit request.trace when plugin is disabled")
		}
	}
}

func TestProcessResponse_SkipsEmptyModel(t *testing.T) {
	cfg := enabledCfg()
	cfg.BufferSize = 10
	cfg.Workers = 1
	p := New(cfg, nil)

	rc := newRC()
	rc.Model = ""
	rc.Request = nil

	result := p.ProcessResponse(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Errorf("ProcessResponse Action = %v, want Continue", result.Action)
	}

	p.Close()

	// Nothing should have been emitted. The buffer should be empty.
	if p.emitter.Dropped() != 0 {
		t.Errorf("Dropped() = %d, want 0", p.emitter.Dropped())
	}
}

// ---------------------------------------------------------------------------
// Emitter
// ---------------------------------------------------------------------------

func TestEmitter_EmitAndDrain(t *testing.T) {
	handler, cleanup := installCapturingLogger()
	defer cleanup()

	cfg := config.RequestLoggingConfig{
		Enabled:    true,
		BufferSize: 32,
		Workers:    2,
	}
	e := NewTraceEmitter(cfg)

	records := []TraceRecord{
		{RequestID: "r1", Model: "m1"},
		{RequestID: "r2", Model: "m2"},
		{RequestID: "r3", Model: "m3"},
	}
	for _, r := range records {
		e.Emit(r)
	}

	e.Close()

	if e.Dropped() != 0 {
		t.Errorf("Dropped() = %d, want 0", e.Dropped())
	}

	// Count "request.trace" log entries.
	traceCount := 0
	for i := 0; i < handler.count(); i++ {
		rec := handler.get(i)
		if rec.Message == "request.trace" {
			traceCount++
		}
	}
	if traceCount != 3 {
		t.Errorf("captured %d request.trace logs, want 3", traceCount)
	}
}

func TestEmitter_BufferFull(t *testing.T) {
	// To reliably fill the buffer we need workers that don't consume immediately.
	// We achieve this by creating the emitter manually with a buffer size of 1
	// and no workers reading.
	e := &TraceEmitter{
		ch: make(chan TraceRecord, 1),
	}

	// First emit fills the buffer.
	e.Emit(TraceRecord{RequestID: "r1"})
	// Second emit should be dropped.
	e.Emit(TraceRecord{RequestID: "r2"})

	if got := e.Dropped(); got != 1 {
		t.Errorf("Dropped() = %d, want 1", got)
	}

	// Drain the channel to clean up.
	<-e.ch
}

func TestEmitter_Close_Idempotent(t *testing.T) {
	cfg := config.RequestLoggingConfig{
		Enabled:    true,
		BufferSize: 8,
		Workers:    1,
	}
	e := NewTraceEmitter(cfg)

	// First close should work fine.
	e.Close()
	// Second close should not panic.
	e.Close()
}

func TestEmitter_Dropped(t *testing.T) {
	e := &TraceEmitter{
		ch: make(chan TraceRecord, 1),
	}

	if got := e.Dropped(); got != 0 {
		t.Errorf("initial Dropped() = %d, want 0", got)
	}

	// Fill buffer.
	e.Emit(TraceRecord{RequestID: "r1"})
	// Drop 3 more.
	e.Emit(TraceRecord{RequestID: "r2"})
	e.Emit(TraceRecord{RequestID: "r3"})
	e.Emit(TraceRecord{RequestID: "r4"})

	if got := e.Dropped(); got != 3 {
		t.Errorf("Dropped() = %d, want 3", got)
	}

	// Drain channel to clean up.
	<-e.ch
}

// Shutdown does not wait for every request, so a record can be emitted while
// Close runs, or after: it is dropped and counted, not sent on the closed channel.
func TestEmitter_EmitDuringAndAfterClose(t *testing.T) {
	e := NewTraceEmitter(config.RequestLoggingConfig{Enabled: true, BufferSize: 8, Workers: 1})

	var wg sync.WaitGroup
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 200; j++ {
				e.Emit(TraceRecord{RequestID: "r"})
			}
		}()
	}
	e.Close()
	wg.Wait()
	before := e.Dropped()
	e.Emit(TraceRecord{RequestID: "late"})

	if got := e.Dropped() - before; got != 1 {
		t.Errorf("Emit after Close dropped %d records, want 1", got)
	}
}

// Records dropped after Close, which has already logged the drops so far, are
// logged once, not silently counted, and not once per record.
func TestEmitter_WarnsOnceOfRecordsDroppedAfterClose(t *testing.T) {
	logs, restore := installCapturingLogger()
	defer restore()
	e := NewTraceEmitter(config.RequestLoggingConfig{Enabled: true, BufferSize: 8, Workers: 1})
	e.Close()
	before := logs.count()

	for i := 0; i < 3; i++ {
		e.Emit(TraceRecord{RequestID: "late"})
	}

	if got := e.Dropped(); got != 3 {
		t.Errorf("dropped %d records emitted after Close, want 3", got)
	}
	var warnings int
	logs.mu.Lock()
	for _, rec := range logs.records[before:] {
		if rec.Level == slog.LevelWarn {
			warnings++
		}
	}
	logs.mu.Unlock()
	if warnings != 1 {
		t.Errorf("logged %d warnings for 3 records dropped after Close, want 1", warnings)
	}
}

// Emit warns of a dropped record after it lets go of the emitter, so a slow
// log write does not hold up Close.
func TestEmitter_CloseDoesNotWaitOnADropWarning(t *testing.T) {
	warning := make(chan struct{}, 1)
	release := make(chan struct{})
	defer close(release)
	prev := slog.Default()
	slog.SetDefault(slog.New(blockingLogHandler{msg: "request.trace.dropped", started: warning, release: release}))
	defer slog.SetDefault(prev)
	// No workers read the channel, so the record is dropped.
	e := &TraceEmitter{ch: make(chan TraceRecord)}
	go e.Emit(TraceRecord{RequestID: "r1"})
	<-warning

	closed := make(chan struct{})
	go func() {
		defer close(closed)
		e.Close()
	}()
	select {
	case <-closed:
	case <-time.After(time.Second):
		t.Fatal("Close waited for the log write of a dropped record")
	}
}

// ---------------------------------------------------------------------------
// Integration
// ---------------------------------------------------------------------------

func TestPlugin_Close(t *testing.T) {
	cfg := enabledCfg()
	cfg.BufferSize = 16
	cfg.Workers = 1
	p := New(cfg, nil)

	rc := newRC()
	rc.Response = &models.ChatCompletionResponse{}
	p.ProcessResponse(context.Background(), rc)

	// Close should drain without panic.
	p.Close()

	if p.emitter.Dropped() != 0 {
		t.Errorf("Dropped() = %d, want 0", p.emitter.Dropped())
	}
}

// A request that finishes after the plugin is closed is not logged, and does
// not crash the gateway.
func TestProcessResponse_AfterClose(t *testing.T) {
	p := New(enabledCfg(), nil)
	p.Close()

	rc := newRC()
	rc.Response = &models.ChatCompletionResponse{}
	p.ProcessResponse(context.Background(), rc)

	if got := p.emitter.Dropped(); got != 1 {
		t.Errorf("Dropped() = %d, want 1", got)
	}
}

func TestPlugin_Close_NilEmitter(t *testing.T) {
	// When disabled, emitter is nil. Close should not panic.
	p := New(disabledCfg(), nil)
	p.Close()
}

func TestProcessResponse_SkipsInternalKey(t *testing.T) {
	handler, cleanup := installCapturingLogger()
	defer cleanup()

	cfg := enabledCfg()
	cfg.BufferSize = 10
	cfg.Workers = 1
	p := New(cfg, nil)

	rc := newRC()
	rc.Response = &models.ChatCompletionResponse{
		Usage: &models.Usage{PromptTokens: 10, CompletionTokens: 5, TotalTokens: 15},
	}
	rc.Metadata["key_type"] = "internal"

	result := p.ProcessResponse(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Errorf("ProcessResponse Action = %v, want Continue", result.Action)
	}

	p.Close()

	// No "request.trace" should appear for internal keys.
	for i := 0; i < handler.count(); i++ {
		rec := handler.get(i)
		if rec.Message == "request.trace" {
			t.Error("should not emit request.trace for internal key")
		}
	}
}

// decisionRC is a finished System One call whose state and instructions are
// marked so a test can tell whether they reached the log.
func decisionRC(t *testing.T) *models.RequestContext {
	t.Helper()
	rc := newRC()
	rc.EndpointType = "decision"
	rc.Model = "typesafe/jev-latest"
	rc.ResolvedModel = "jev-1.13.0"
	rc.Provider = "typesafe"
	req, apiErr := models.ParseDecisionRequest([]byte(`{"model":"typesafe/jev-latest","state":{"ticket":"private-state"},"questions":{"resolved":{"type":"noul","instructions":"private-instructions"}}}`))
	if apiErr != nil {
		t.Fatal(apiErr)
	}
	req.SetUpstreamModel("jev-latest")
	rc.DecisionRequest = req
	resp, _ := models.ParseDecisionResponse([]byte(`{"model":"jev-1.13.0","answers":{"resolved":{"type":"noul","noul":0.8}},"usage":{"input_tokens":21,"output_tokens":0}}`))
	rc.DecisionResponse = resp
	rc.Response = &models.ChatCompletionResponse{Model: "jev-1.13.0", Usage: &models.Usage{PromptTokens: 21, TotalTokens: 21}}
	rc.Metadata["endpoint_type"] = "decision"
	rc.Metadata["decision_format"] = "systemone"
	rc.Metadata["decision_question_count"] = "1"
	return rc
}

func hasContent(rec TraceRecord) bool {
	all := string(rec.RequestBodyJSON) + string(rec.ResponseBodyJSON) + rec.ErrorMessage
	return strings.Contains(all, "private-state") || strings.Contains(all, "private-instructions")
}

// Without include_bodies a decision still logs its summary: question IDs and
// types, the state's size, the answers and usage.
func TestBuildRecord_DecisionSummaryByDefault(t *testing.T) {
	rec := buildRecord(decisionRC(t), enabledCfg())
	if rec.StatusCode != 200 || rec.PromptTokens != 21 || rec.Provider != "typesafe" {
		t.Fatalf("status = %d tokens = %d provider = %q", rec.StatusCode, rec.PromptTokens, rec.Provider)
	}
	if rec.Metadata["endpoint_type"] != "decision" || rec.Metadata["decision_format"] != "systemone" || rec.Metadata["decision_question_count"] != "1" {
		t.Errorf("metadata = %v", rec.Metadata)
	}
	if hasContent(rec) {
		t.Fatalf("summary carries content: %s %s", rec.RequestBodyJSON, rec.ResponseBodyJSON)
	}
	if !strings.Contains(string(rec.RequestBodyJSON), `"question_ids":["resolved"]`) || !strings.Contains(string(rec.ResponseBodyJSON), `"noul":0.8`) {
		t.Errorf("summary = %s %s", rec.RequestBodyJSON, rec.ResponseBodyJSON)
	}
}

// Full bodies need include_bodies, and any privacy setting keeps them out.
func TestBuildRecord_DecisionFullBodies(t *testing.T) {
	cfg := enabledCfg()
	cfg.IncludeBodies = true
	rec := buildRecord(decisionRC(t), cfg)
	if !strings.Contains(string(rec.RequestBodyJSON), "private-state") || !strings.Contains(string(rec.ResponseBodyJSON), `"answers"`) {
		t.Fatalf("include_bodies did not log the bodies: %s", rec.RequestBodyJSON)
	}
	for _, meta := range []map[string]string{
		{"org_privacy_enabled": "true"},
		{"org_privacy_mode": "full"},
		{"privacy_mode": "patterns"},
	} {
		rc := decisionRC(t)
		for k, v := range meta {
			rc.Metadata[k] = v
		}
		if rec := buildRecord(rc, cfg); hasContent(rec) {
			t.Errorf("privacy %v still logged content", meta)
		}
	}
}

// A vendor's error text never reaches the log; the code and status do.
func TestBuildRecord_DecisionErrorTextNotLogged(t *testing.T) {
	rc := decisionRC(t)
	rc.Response, rc.DecisionResponse = nil, nil
	rc.Errors = []error{&models.APIError{Status: 422, Code: "upstream_rejected", Message: "vendor says private-state is bad"}}
	rec := buildRecord(rc, enabledCfg())
	if rec.StatusCode != 422 || rec.ErrorCode != "upstream_rejected" || strings.Contains(rec.ErrorMessage, "private") {
		t.Fatalf("status = %d code = %q message = %q", rec.StatusCode, rec.ErrorCode, rec.ErrorMessage)
	}
}

// A wrapped decision error is still recorded as its status, not the vendor's text.
func TestBuildRecord_WrappedDecisionErrorTextNotLogged(t *testing.T) {
	rc := decisionRC(t)
	rc.Response, rc.DecisionResponse = nil, nil
	apiErr := &models.APIError{Status: 422, Code: "upstream_rejected", Message: "vendor says private-state is bad"}
	rc.Errors = []error{fmt.Errorf("decision call: %w", apiErr)}
	rec := buildRecord(rc, enabledCfg())
	if rec.StatusCode != 422 || rec.ErrorCode != "upstream_rejected" || strings.Contains(rec.ErrorMessage, "private") {
		t.Fatalf("status = %d code = %q message = %q", rec.StatusCode, rec.ErrorCode, rec.ErrorMessage)
	}
}

func TestBuildRecord_ProviderKeyHeaderMasked(t *testing.T) {
	cfg := enabledCfg()
	cfg.IncludeBodies = true
	rc := decisionRC(t)
	rc.RequestHeaders = http.Header{"X-Agentcc-Provider-Key": {"caller-own-key"}}
	rec := buildRecord(rc, cfg)
	raw, _ := json.Marshal(rec)
	if strings.Contains(string(raw), "caller-own-key") {
		t.Fatal("the provider key header reached the log record")
	}
}

// Where a redactor would apply, a decision logs its summary: the redactor
// walks chat messages and cannot clean a decision's state.
func TestPlugin_DecisionUnderRedactionLogsSummary(t *testing.T) {
	cfg := enabledCfg()
	cfg.IncludeBodies = true
	p := New(cfg, nil)
	defer p.Close()
	f := NewLogFlusher("http://127.0.0.1:1/never", "", time.Hour, 10)
	p.SetFlusher(f)
	p.SetRedactor(privacy.New(privacy.ModeFull, nil))

	p.ProcessResponse(context.Background(), decisionRC(t))
	records := f.take()
	if len(records) != 1 {
		t.Fatalf("records = %d", len(records))
	}
	if hasContent(records[0]) {
		t.Fatalf("redaction on, yet content logged: %s", records[0].RequestBodyJSON)
	}
	if !strings.Contains(string(records[0].RequestBodyJSON), "question_ids") {
		t.Errorf("summary missing: %s", records[0].RequestBodyJSON)
	}
}

// An internal caller's decision is not logged: it is billed by the backend.
func TestPlugin_InternalDecisionNotLogged(t *testing.T) {
	p := New(enabledCfg(), nil)
	defer p.Close()
	f := NewLogFlusher("http://127.0.0.1:1/never", "", time.Hour, 10)
	p.SetFlusher(f)
	rc := decisionRC(t)
	rc.Metadata["key_type"] = "internal"
	p.ProcessResponse(context.Background(), rc)
	if records := f.take(); len(records) != 0 {
		t.Fatalf("internal decision logged %d records", len(records))
	}
}

// Every header the gateway accepts a credential in must be starred before the
// record is stored. x-goog-api-key was missing while the GenAI route reads a
// caller's Google key from exactly that header, so those keys were kept in
// plain text on the request log row.
func TestSanitizeHeaders_StarsEveryCredentialHeader(t *testing.T) {
	const secret = "SENTINEL-DO-NOT-LEAK"

	credentialHeaders := []string{
		"Authorization",
		"Proxy-Authorization",
		"X-Api-Key",
		"Api-Key",
		"X-Goog-Api-Key",
		"X-Goog-Iam-Authorization-Token",
		"X-Amz-Security-Token",
		"X-Auth-Token",
		"Cookie",
		"Set-Cookie",
		"X-Webhook-Secret",
	}

	in := map[string][]string{"Content-Type": {"application/json"}}
	for _, name := range credentialHeaders {
		in[name] = []string{secret}
	}

	out := sanitizeHeaders(in)

	for _, name := range credentialHeaders {
		got := out[name]
		if len(got) != 1 || got[0] == secret {
			t.Errorf("%s = %v, want it starred", name, got)
		}
	}
	// A header that carries no credential stays readable, or the log stops
	// being useful.
	if got := out["Content-Type"]; len(got) != 1 || got[0] != "application/json" {
		t.Errorf("Content-Type = %v, want it left alone", got)
	}
}

// A vector-store write or a run on the assistant's own model has no model and
// no chat request, but it is an endpoint's call and must be logged; only a
// request that names no endpoint is skipped.
func TestProcessResponse_LogsEndpointCallWithoutModel(t *testing.T) {
	handler, cleanup := installCapturingLogger()
	defer cleanup()

	cfg := enabledCfg()
	cfg.BufferSize = 10
	cfg.Workers = 1
	p := New(cfg, nil)

	rc := newRC()
	rc.Model = ""
	rc.Request = nil
	rc.EndpointType = "vector_stores"
	p.ProcessResponse(context.Background(), rc)
	p.Close()

	for i := 0; i < handler.count(); i++ {
		if handler.get(i).Message == "request.trace" {
			return
		}
	}
	t.Fatal("a vector-store call was not logged")
}
