package logging

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"strconv"
	"sync"
	"syscall"
	"time"
)

const maxFlushRetries = 3 // drop records after this many consecutive failures

// Enqueue refuses records while the buffer holds twice maxBuffer, and warns
// about it at most this often.
const overflowWarnInterval = 10 * time.Second

// On shutdown, Close tries each batch up to finalFlushAttempts times, this far
// apart, before its deadline: a backend that is restarting may be back.
var (
	finalFlushAttempts  = 3
	finalFlushRetryWait = time.Second
)

type LogFlusher struct {
	buffer           []TraceRecord
	mu               sync.Mutex
	overflow         int       // under mu: records Enqueue refused because the buffer was full
	overflowWarned   time.Time // under mu: when that was last logged
	closed           bool      // under mu: Close has begun, so Enqueue refuses records
	lost             int       // under mu: records refused or dropped since then; Close reports them
	webhookURL       string
	webhookSecret    string
	interval         time.Duration
	maxBuffer        int
	client           *http.Client
	consecutiveFails int

	// kick asks Run for a flush once the buffer reaches maxBuffer. It holds
	// one request, so the records that arrive during a slow send wait for
	// the next flush instead of each starting one.
	kick chan struct{}

	// sendMu lets one send run at a time, so Close never sends a batch that a
	// flush is still sending. A flush sends with sendCtx, which Close cancels
	// once its deadline passes. Close closes stop to end Run.
	sendMu      sync.Mutex
	sendCtx     context.Context
	cancelSends context.CancelFunc
	stop        chan struct{}
}

type logFlushPayload struct {
	Logs []logEntry `json:"logs"`
}

type logEntry struct {
	RequestID          string            `json:"request_id"`
	Timestamp          time.Time         `json:"timestamp"`
	Model              string            `json:"model"`
	ResolvedModel      string            `json:"resolved_model,omitempty"`
	Provider           string            `json:"provider"`
	PromptTokens       int               `json:"prompt_tokens"`
	CompletionTokens   int               `json:"completion_tokens"`
	TotalTokens        int               `json:"total_tokens"`
	StatusCode         int               `json:"status_code"`
	LatencyMs          int64             `json:"latency_ms"`
	IsStream           bool              `json:"is_stream"`
	IsError            bool              `json:"is_error"`
	ErrorMessage       string            `json:"error_message,omitempty"`
	CacheHit           bool              `json:"cache_hit"`
	FallbackUsed       bool              `json:"fallback_used"`
	GuardrailTriggered bool              `json:"guardrail_triggered"`
	GuardrailResults   json.RawMessage   `json:"guardrail_results,omitempty"`
	Cost               float64           `json:"cost"`
	AuthKeyID          string            `json:"auth_key_id,omitempty"`
	UserID             string            `json:"user_id,omitempty"`
	SessionID          string            `json:"session_id,omitempty"`
	Metadata           map[string]string `json:"metadata,omitempty"`
	RequestBody        json.RawMessage   `json:"request_body,omitempty"`
	ResponseBody       json.RawMessage   `json:"response_body,omitempty"`
	RequestHeaders     json.RawMessage   `json:"request_headers,omitempty"`
}

func NewLogFlusher(webhookURL, webhookSecret string, interval time.Duration, maxBuffer int) *LogFlusher {
	if interval <= 0 {
		interval = 30 * time.Second
	}
	if maxBuffer <= 0 {
		maxBuffer = 5000
	}
	sendCtx, cancelSends := context.WithCancel(context.Background())
	return &LogFlusher{
		kick:          make(chan struct{}, 1),
		sendCtx:       sendCtx,
		cancelSends:   cancelSends,
		stop:          make(chan struct{}),
		webhookURL:    webhookURL,
		webhookSecret: webhookSecret,
		interval:      interval,
		maxBuffer:     maxBuffer,
		client:        &http.Client{Timeout: 15 * time.Second},
	}
}

func (f *LogFlusher) Run(ctx context.Context) {
	ticker := time.NewTicker(f.interval)
	defer ticker.Stop()

	slog.Info("log flusher started",
		"interval", f.interval.String(),
		"webhook_url", f.webhookURL,
		"max_buffer", f.maxBuffer,
	)

	for {
		select {
		case <-ctx.Done():
			f.flush()
			slog.Info("log flusher stopped")
			return
		case <-f.stop:
			return // Close makes the last flush
		case <-ticker.C:
			f.flush()
		case <-f.kick:
			f.flush()
		}
	}
}

func (f *LogFlusher) Enqueue(rec TraceRecord) {
	f.mu.Lock()
	if f.closed {
		f.lost++
		f.mu.Unlock()
		return
	}
	if len(f.buffer) >= 2*f.maxBuffer {
		// The webhook is not keeping up: refuse the record rather than let
		// the buffer grow without bound.
		f.overflow++
		overflow := f.overflow
		warn := time.Since(f.overflowWarned) >= overflowWarnInterval
		if warn {
			f.overflowWarned = time.Now()
		}
		f.mu.Unlock()
		if warn {
			slog.Warn("log flusher: buffer full, dropping new records",
				"dropped_total", overflow,
				"buffered", 2*f.maxBuffer,
			)
		}
		return
	}
	f.buffer = append(f.buffer, rec)
	shouldFlush := len(f.buffer) >= f.maxBuffer
	f.mu.Unlock()

	if shouldFlush {
		select {
		case f.kick <- struct{}{}:
		default: // a flush is already pending
		}
	}
}

// encodeLogs builds the webhook payload for records.
func encodeLogs(records []TraceRecord) ([]byte, error) {
	entries := make([]logEntry, len(records))
	for i, rec := range records {
		var grJSON json.RawMessage
		if len(rec.GuardrailResults) > 0 {
			grJSON, _ = json.Marshal(rec.GuardrailResults)
		}
		var reqBody, respBody json.RawMessage
		if len(rec.RequestBodyJSON) > 0 {
			reqBody = rec.RequestBodyJSON
		} else if rec.RequestBody != nil {
			reqBody, _ = json.Marshal(rec.RequestBody)
		}
		if len(rec.ResponseBodyJSON) > 0 {
			respBody = rec.ResponseBodyJSON
		} else if rec.ResponseBody != nil {
			respBody, _ = json.Marshal(rec.ResponseBody)
		}
		var reqHeaders json.RawMessage
		if len(rec.RequestHeaders) > 0 {
			reqHeaders, _ = json.Marshal(rec.RequestHeaders)
		}
		// Extract cost from metadata to promote as top-level field.
		var cost float64
		if costStr, ok := rec.Metadata["cost"]; ok {
			cost, _ = strconv.ParseFloat(costStr, 64)
		}
		entries[i] = logEntry{
			RequestID:          rec.RequestID,
			Timestamp:          rec.Timestamp,
			Model:              rec.Model,
			ResolvedModel:      rec.ResolvedModel,
			Provider:           rec.Provider,
			PromptTokens:       rec.PromptTokens,
			CompletionTokens:   rec.CompletionTokens,
			TotalTokens:        rec.TotalTokens,
			StatusCode:         rec.StatusCode,
			LatencyMs:          rec.LatencyMs,
			IsStream:           rec.IsStream,
			IsError:            rec.StatusCode >= 400,
			ErrorMessage:       rec.ErrorMessage,
			CacheHit:           rec.CacheHit,
			FallbackUsed:       rec.FallbackUsed,
			GuardrailTriggered: rec.GuardrailTriggered,
			GuardrailResults:   grJSON,
			Cost:               cost,
			AuthKeyID:          rec.AuthKeyID,
			UserID:             rec.UserID,
			SessionID:          rec.SessionID,
			Metadata:           rec.Metadata,
			RequestBody:        reqBody,
			ResponseBody:       respBody,
			RequestHeaders:     reqHeaders,
		}
	}

	return json.Marshal(logFlushPayload{Logs: entries})
}

func (f *LogFlusher) flush() {
	f.sendMu.Lock()
	defer f.sendMu.Unlock()

	records := f.take()
	if len(records) == 0 {
		return
	}

	body, err := encodeLogs(records)
	if err != nil {
		slog.Error("log flusher: marshal failed", "error", err, "count", len(records))
		f.countLost(len(records))
		return
	}

	status, err := f.post(f.sendCtx, body)
	if err != nil && f.sendCtx.Err() != nil {
		// Close's deadline passed during the send; Close counts these.
		f.reEnqueue(records)
		return
	}
	if retryable(status, err) {
		cause := slog.Any("error", err)
		if err == nil {
			cause = slog.Int("status", status)
		}
		f.consecutiveFails++
		if f.consecutiveFails > maxFlushRetries && !f.closing() {
			slog.Error("log flusher: max retries exceeded, dropping records",
				cause,
				"count", len(records),
				"consecutive_failures", f.consecutiveFails,
			)
			f.consecutiveFails = 0
			f.countLost(len(records))
			return
		}
		slog.Error("log flusher: send failed, re-enqueuing records",
			cause,
			"count", len(records),
			"retry", f.consecutiveFails,
		)
		f.reEnqueue(records)
		return
	}
	if status >= 400 {
		// Client error — retrying won't help, drop immediately.
		slog.Error("log flusher: webhook returned client error, dropping records",
			"status", status,
			"count", len(records),
		)
		f.consecutiveFails = 0
		f.countLost(len(records))
		return
	}

	// Success — reset retry counter.
	f.consecutiveFails = 0

	slog.Debug("log flusher: sent records",
		"count", len(records),
		"status", status,
	)
}

// Close stops accepting records and makes a last attempt to deliver the
// buffered ones, trying each batch up to finalFlushAttempts times until ctx
// ends. A flush already sending finishes first (or is cut off when ctx ends),
// so no batch is sent twice. It logs how many records were not delivered, and
// how many it did deliver.
func (f *LogFlusher) Close(ctx context.Context) {
	f.mu.Lock()
	if f.closed {
		f.mu.Unlock()
		return
	}
	f.closed = true
	f.mu.Unlock()
	close(f.stop)
	defer f.cancelSends()
	defer context.AfterFunc(ctx, f.cancelSends)()

	f.sendMu.Lock()
	defer f.sendMu.Unlock()

	records := f.take()
	var undelivered int
	var err error
	if len(records) > 0 {
		undelivered, err = f.deliver(ctx, records)
	}
	delivered := len(records) - undelivered

	f.mu.Lock()
	undelivered += f.lost
	f.mu.Unlock()
	switch {
	case undelivered > 0:
		attrs := []any{"undelivered", undelivered}
		if delivered > 0 {
			attrs = append(attrs, "delivered", delivered)
		}
		level := slog.LevelError
		if err != nil {
			attrs = append(attrs, "error", err)
			if backendGone(err) {
				// Stop order often stops the backend first (compose does).
				level = slog.LevelWarn
			}
		}
		slog.Log(context.Background(), level, "log flusher: request logs not delivered before shutdown", attrs...)
	case len(records) > 0:
		slog.Info("log flusher: delivered buffered request logs before shutdown", "count", len(records))
	}
	slog.Info("log flusher stopped")
}

// deliver sends records in batches of at most maxBuffer, so one failed send
// does not lose the whole backlog: up to twice maxBuffer buffered records, plus
// as many from a flush that gave its batch back. A batch the webhook refuses
// with a client error is not delivered, but the batches after it are still
// sent, as a flush would. Any other failure stops it: the batches after would
// meet it too. It returns how many records it did not deliver, and why.
func (f *LogFlusher) deliver(ctx context.Context, records []TraceRecord) (int, error) {
	var undelivered int
	var lastErr error
	for start := 0; start < len(records); start += f.maxBuffer {
		batch := records[start:min(start+f.maxBuffer, len(records))]
		err := f.deliverBatch(ctx, batch)
		if err == nil {
			continue
		}
		var status statusError
		if !errors.As(err, &status) || retryable(int(status), nil) {
			return undelivered + len(records) - start, err
		}
		undelivered += len(batch)
		lastErr = err
	}
	return undelivered, lastErr
}

// deliverBatch sends one batch, trying again after a failed send or a server
// error, up to finalFlushAttempts sends in all while ctx lasts.
func (f *LogFlusher) deliverBatch(ctx context.Context, records []TraceRecord) error {
	if err := ctx.Err(); err != nil {
		return err // a send cut off at the deadline left these: don't encode them
	}
	body, err := encodeLogs(records)
	if err != nil {
		return err
	}
	for attempt := 1; ; attempt++ {
		if ctxErr := ctx.Err(); ctxErr != nil {
			if err == nil {
				err = ctxErr
			}
			return err
		}
		status, postErr := f.post(ctx, body)
		if postErr == nil && status < 400 {
			return nil
		}
		err = postErr
		if err == nil {
			err = statusError(status)
		}
		if !retryable(status, postErr) || attempt >= finalFlushAttempts {
			return err
		}
		select {
		case <-time.After(finalFlushRetryWait):
		case <-ctx.Done():
			return err
		}
	}
}

// statusError is the error for a send the webhook answered with this status.
type statusError int

func (e statusError) Error() string {
	return fmt.Sprintf("webhook returned status %d", int(e))
}

// retryable reports whether a send that returned status and err may succeed
// if tried again: it failed before the webhook answered, or the webhook had a
// server error. A client error will not change on a retry.
func retryable(status int, err error) bool {
	return err != nil || status >= 500
}

// backendGone reports whether err says there is no backend to send to: it
// refused the connection, or its name no longer resolves, as a stopped compose
// service's does.
func backendGone(err error) bool {
	var dnsErr *net.DNSError
	return errors.Is(err, syscall.ECONNREFUSED) || (errors.As(err, &dnsErr) && dnsErr.IsNotFound)
}

// closing reports whether Close has begun. A flush then re-enqueues the
// records it would drop after maxFlushRetries, for Close's last attempt.
func (f *LogFlusher) closing() bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.closed
}

// countLost notes that a flush dropped n records for good. Once Close has
// begun, Close reports them as undelivered.
func (f *LogFlusher) countLost(n int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.closed {
		f.lost += n
	}
}

// take empties the buffer and returns what was in it.
func (f *LogFlusher) take() []TraceRecord {
	f.mu.Lock()
	defer f.mu.Unlock()
	records := f.buffer
	f.buffer = nil
	return records
}

// post sends one batch to the webhook and returns the response status.
func (f *LogFlusher) post(ctx context.Context, body []byte) (int, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, f.webhookURL, bytes.NewReader(body))
	if err != nil {
		return 0, fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	if f.webhookSecret != "" {
		req.Header.Set("X-Webhook-Secret", f.webhookSecret)
	}

	resp, err := f.client.Do(req)
	if err != nil {
		return 0, err
	}
	resp.Body.Close()
	return resp.StatusCode, nil
}

// reEnqueue puts failed records back into the buffer (up to maxBuffer).
// This prevents data loss when the webhook endpoint is temporarily unavailable.
// Once Close has begun it keeps them all for Close's last attempt: Enqueue
// refuses records by then, so the buffer cannot grow further.
func (f *LogFlusher) reEnqueue(records []TraceRecord) {
	f.mu.Lock()
	defer f.mu.Unlock()

	capacity := f.maxBuffer - len(f.buffer)
	if f.closed {
		capacity = len(records)
	}
	if capacity <= 0 {
		slog.Warn("log flusher: buffer full, dropping records",
			"dropped", len(records),
		)
		return
	}
	if len(records) > capacity {
		slog.Warn("log flusher: partial re-enqueue, dropping excess",
			"re_enqueued", capacity,
			"dropped", len(records)-capacity,
		)
		records = records[:capacity]
	}
	f.buffer = append(f.buffer, records...)
}

func FormatLogsWebhookURL(controlPlaneURL string) string {
	return fmt.Sprintf("%s/agentcc/webhook/logs/", controlPlaneURL)
}
