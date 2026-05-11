package logging

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"sync"
	"time"
)

const maxFlushRetries = 3 // drop records after this many consecutive failures

type LogFlusher struct {
	buffer           []TraceRecord
	mu               sync.Mutex
	webhookURL       string
	webhookSecret    string
	interval         time.Duration
	maxBuffer        int
	client           *http.Client
	consecutiveFails int
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
	return &LogFlusher{
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
		case <-ticker.C:
			f.flush()
		}
	}
}

func (f *LogFlusher) Enqueue(rec TraceRecord) {
	f.mu.Lock()
	f.buffer = append(f.buffer, rec)
	shouldFlush := len(f.buffer) >= f.maxBuffer
	f.mu.Unlock()

	if shouldFlush {
		go f.flush()
	}
}

func (f *LogFlusher) flush() {
	f.mu.Lock()
	if len(f.buffer) == 0 {
		f.mu.Unlock()
		return
	}
	records := f.buffer
	f.buffer = nil
	f.mu.Unlock()

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

	payload := logFlushPayload{
		Logs: entries,
	}

	body, err := json.Marshal(payload)
	if err != nil {
		slog.Error("log flusher: marshal failed", "error", err, "count", len(records))
		return
	}

	req, err := http.NewRequest("POST", f.webhookURL, bytes.NewReader(body))
	if err != nil {
		slog.Error("log flusher: create request failed", "error", err)
		return
	}
	req.Header.Set("Content-Type", "application/json")
	if f.webhookSecret != "" {
		req.Header.Set("X-Webhook-Secret", f.webhookSecret)
	}

	resp, err := f.client.Do(req)
	if err != nil {
		f.consecutiveFails++
		if f.consecutiveFails > maxFlushRetries {
			slog.Error("log flusher: max retries exceeded, dropping records",
				"error", err,
				"count", len(records),
				"consecutive_failures", f.consecutiveFails,
			)
			f.consecutiveFails = 0
			return
		}
		slog.Error("log flusher: send failed, re-enqueuing records",
			"error", err,
			"count", len(records),
			"retry", f.consecutiveFails,
		)
		f.reEnqueue(records)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 500 {
		f.consecutiveFails++
		if f.consecutiveFails > maxFlushRetries {
			slog.Error("log flusher: max retries exceeded after server errors, dropping records",
				"status", resp.StatusCode,
				"count", len(records),
				"consecutive_failures", f.consecutiveFails,
			)
			f.consecutiveFails = 0
			return
		}
		slog.Error("log flusher: webhook returned server error, re-enqueuing records",
			"status", resp.StatusCode,
			"count", len(records),
			"retry", f.consecutiveFails,
		)
		f.reEnqueue(records)
		return
	}
	if resp.StatusCode >= 400 {
		// Client error — retrying won't help, drop immediately.
		slog.Error("log flusher: webhook returned client error, dropping records",
			"status", resp.StatusCode,
			"count", len(records),
		)
		f.consecutiveFails = 0
		return
	}

	// Success — reset retry counter.
	f.consecutiveFails = 0

	slog.Debug("log flusher: sent records",
		"count", len(records),
		"status", resp.StatusCode,
	)
}

// reEnqueue puts failed records back into the buffer (up to maxBuffer).
// This prevents data loss when the webhook endpoint is temporarily unavailable.
func (f *LogFlusher) reEnqueue(records []TraceRecord) {
	f.mu.Lock()
	defer f.mu.Unlock()

	capacity := f.maxBuffer - len(f.buffer)
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
