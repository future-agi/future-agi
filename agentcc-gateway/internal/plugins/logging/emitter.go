package logging

import (
	"encoding/json"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// emitterDrainTimeout bounds how long Close waits for the workers to write out
// the buffered trace records.
const emitterDrainTimeout = 5 * time.Second

// TraceEmitter manages the buffered channel and worker goroutines for async trace emission.
type TraceEmitter struct {
	ch      chan TraceRecord
	wg      sync.WaitGroup
	cfg     config.RequestLoggingConfig
	dropped atomic.Int64

	// Emit holds mu to read and Close to write, so a request that finishes
	// after Close (shutdown does not wait for every one) is dropped rather
	// than sent on the closed channel.
	mu     sync.RWMutex
	closed bool // under mu

	warnedClosed atomic.Bool // an Emit after Close has logged the drop
}

// NewTraceEmitter creates a TraceEmitter and starts worker goroutines.
func NewTraceEmitter(cfg config.RequestLoggingConfig) *TraceEmitter {
	bufSize := cfg.BufferSize
	if bufSize <= 0 {
		bufSize = 4096
	}
	workers := cfg.Workers
	if workers <= 0 {
		workers = 2
	}

	e := &TraceEmitter{
		ch:  make(chan TraceRecord, bufSize),
		cfg: cfg,
	}

	for i := 0; i < workers; i++ {
		e.wg.Add(1)
		go e.worker()
	}

	return e
}

// Emit sends a trace record to the buffer. Non-blocking: drops the record if the buffer is full
// or the emitter is closed.
func (e *TraceEmitter) Emit(record TraceRecord) {
	e.mu.RLock()
	closed, sent := e.closed, false
	if !closed {
		select {
		case e.ch <- record:
			sent = true
		default:
		}
	}
	e.mu.RUnlock()
	if sent {
		return
	}
	// Outside mu, so a slow log write does not hold up Close.
	e.dropped.Add(1)
	switch {
	case !closed:
		slog.Warn("request.trace.dropped",
			"request_id", record.RequestID,
		)
	case e.warnedClosed.CompareAndSwap(false, true):
		// Close has logged the drops so far; log the late ones once.
		slog.Warn("trace emitter: dropping records emitted after close",
			"request_id", record.RequestID,
		)
	}
}

// Close closes the channel and waits for workers to drain with a timeout.
func (e *TraceEmitter) Close() {
	e.mu.Lock()
	if e.closed {
		e.mu.Unlock()
		return
	}
	e.closed = true
	close(e.ch)
	e.mu.Unlock()

	done := make(chan struct{})
	go func() {
		e.wg.Wait()
		close(done)
	}()

	select {
	case <-done:
		// All records drained.
	case <-time.After(emitterDrainTimeout):
		slog.Warn("trace emitter drain timeout", "remaining", len(e.ch))
	}

	if dropped := e.dropped.Load(); dropped > 0 {
		slog.Warn("trace records dropped during lifetime", "count", dropped)
	}
}

// Dropped returns the number of dropped trace records.
func (e *TraceEmitter) Dropped() int64 {
	return e.dropped.Load()
}

func (e *TraceEmitter) worker() {
	defer e.wg.Done()
	for record := range e.ch {
		emitToLog(record)
	}
}

func emitToLog(rec TraceRecord) {
	attrs := []slog.Attr{
		slog.String("request_id", rec.RequestID),
		slog.String("trace_id", rec.TraceID),
		slog.Time("timestamp", rec.Timestamp),
		slog.String("model", rec.Model),
		slog.String("provider", rec.Provider),
		slog.Bool("is_stream", rec.IsStream),
		slog.Int("status_code", rec.StatusCode),
		slog.Int64("latency_ms", rec.LatencyMs),
		slog.Int("prompt_tokens", rec.PromptTokens),
		slog.Int("completion_tokens", rec.CompletionTokens),
		slog.Int("total_tokens", rec.TotalTokens),
	}

	if rec.ResolvedModel != "" {
		attrs = append(attrs, slog.String("resolved_model", rec.ResolvedModel))
	}
	if rec.UserID != "" {
		attrs = append(attrs, slog.String("user_id", rec.UserID))
	}
	if rec.SessionID != "" {
		attrs = append(attrs, slog.String("session_id", rec.SessionID))
	}
	if rec.AuthKeyID != "" {
		attrs = append(attrs, slog.String("auth_key_id", rec.AuthKeyID))
	}
	if rec.AuthKeyName != "" {
		attrs = append(attrs, slog.String("auth_key_name", rec.AuthKeyName))
	}
	if rec.FinishReason != "" {
		attrs = append(attrs, slog.String("finish_reason", rec.FinishReason))
	}

	// Pipeline flags (only log when true).
	if rec.CacheHit {
		attrs = append(attrs, slog.Bool("cache_hit", true))
	}
	if rec.FallbackUsed {
		attrs = append(attrs, slog.Bool("fallback_used", true))
	}
	if rec.GuardrailTriggered {
		attrs = append(attrs, slog.Bool("guardrail_triggered", true))
		if len(rec.GuardrailResults) > 0 {
			if b, err := json.Marshal(rec.GuardrailResults); err == nil {
				attrs = append(attrs, slog.String("guardrail_results", string(b)))
			}
		}
	}
	if rec.ShortCircuited {
		attrs = append(attrs, slog.Bool("short_circuited", true))
	}
	if rec.Timeout {
		attrs = append(attrs, slog.Bool("timeout", true))
	}

	// Error context.
	if rec.ErrorMessage != "" {
		attrs = append(attrs, slog.String("error_message", rec.ErrorMessage))
	}
	if rec.ErrorCode != "" {
		attrs = append(attrs, slog.String("error_code", rec.ErrorCode))
	}

	slog.LogAttrs(nil, slog.LevelInfo, "request.trace", attrs...)
}
