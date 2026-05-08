package logging

import (
	"encoding/json"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// TraceRecord captures a snapshot of a request's lifecycle for logging.
type TraceRecord struct {
	RequestID string    `json:"request_id"`
	TraceID   string    `json:"trace_id"`
	Timestamp time.Time `json:"timestamp"`

	// Request metadata.
	Model         string `json:"model"`
	ResolvedModel string `json:"resolved_model,omitempty"`
	Provider      string `json:"provider"`
	IsStream      bool   `json:"is_stream"`
	UserID        string `json:"user_id,omitempty"`
	SessionID     string `json:"session_id,omitempty"`

	// Auth context.
	AuthKeyID    string `json:"auth_key_id,omitempty"`
	AuthKeyName  string `json:"auth_key_name,omitempty"`
	AuthKeyOwner string `json:"auth_key_owner,omitempty"`

	// Response metrics.
	StatusCode       int    `json:"status_code"`
	LatencyMs        int64  `json:"latency_ms"`
	PromptTokens     int    `json:"prompt_tokens"`
	CompletionTokens int    `json:"completion_tokens"`
	TotalTokens      int    `json:"total_tokens"`
	FinishReason     string `json:"finish_reason,omitempty"`

	// Pipeline flags.
	CacheHit           bool `json:"cache_hit"`
	FallbackUsed       bool `json:"fallback_used"`
	GuardrailTriggered bool `json:"guardrail_triggered"`
	ShortCircuited     bool `json:"short_circuited"`
	Timeout            bool `json:"timeout"`

	// Pipeline timings.
	Timings map[string]int64 `json:"timings,omitempty"` // stage name → microseconds

	// Error context.
	ErrorMessage string `json:"error_message,omitempty"`
	ErrorCode    string `json:"error_code,omitempty"`

	// Optional bodies.
	RequestBody      *models.ChatCompletionRequest  `json:"request_body,omitempty"`
	ResponseBody     *models.ChatCompletionResponse `json:"response_body,omitempty"`
	RequestBodyJSON  json.RawMessage                `json:"request_body_json,omitempty"`
	ResponseBodyJSON json.RawMessage                `json:"response_body_json,omitempty"`

	RequestHeaders map[string][]string `json:"request_headers,omitempty"`

	// Guardrail results — structured details of each triggered guardrail check.
	GuardrailResults []models.GuardrailResult `json:"guardrail_results,omitempty"`

	// Custom metadata.
	Metadata map[string]string `json:"metadata,omitempty"`
}

// buildRecord constructs a TraceRecord from a RequestContext.
func buildRecord(rc *models.RequestContext, cfg config.RequestLoggingConfig) TraceRecord {
	rec := TraceRecord{
		RequestID: rc.RequestID,
		TraceID:   rc.TraceID,
		Timestamp: rc.StartTime,

		Model:         rc.Model,
		ResolvedModel: rc.ResolvedModel,
		Provider:      rc.Provider,
		IsStream:      rc.IsStream,
		UserID:        rc.UserID,
		SessionID:     rc.SessionID,

		LatencyMs: rc.Elapsed().Milliseconds(),

		CacheHit:           rc.Flags.CacheHit,
		FallbackUsed:       rc.Flags.FallbackUsed,
		GuardrailTriggered: rc.Flags.GuardrailTriggered,
		ShortCircuited:     rc.Flags.ShortCircuited,
		Timeout:            rc.Flags.Timeout,
	}

	// Auth context from metadata.
	rec.AuthKeyID = rc.Metadata["auth_key_id"]
	rec.AuthKeyName = rc.Metadata["auth_key_name"]
	rec.AuthKeyOwner = rc.Metadata["auth_key_owner"]

	// Response metrics.
	if rc.Response != nil {
		rec.StatusCode = 200
		if rc.Response.Usage != nil {
			rec.PromptTokens = rc.Response.Usage.PromptTokens
			rec.CompletionTokens = rc.Response.Usage.CompletionTokens
			rec.TotalTokens = rc.Response.Usage.TotalTokens
		}
		if len(rc.Response.Choices) > 0 {
			rec.FinishReason = rc.Response.Choices[0].FinishReason
		}
	}
	if rec.StatusCode == 0 {
		switch rc.EndpointType {
		case "embedding":
			if rc.EmbeddingResponse != nil {
				rec.StatusCode = 200
			}
		case "image":
			if rc.ImageResponse != nil {
				rec.StatusCode = 200
			}
		case "speech", "speech_stream":
			if rc.Metadata["response_content_type"] != "" {
				rec.StatusCode = 200
			}
		case "transcription":
			if rc.TranscriptionResp != nil {
				rec.StatusCode = 200
			}
		case "translation":
			if rc.TranslationResp != nil {
				rec.StatusCode = 200
			}
		case "rerank":
			if rc.RerankResponse != nil {
				rec.StatusCode = 200
			}
		case "search":
			if rc.SearchResponse != nil {
				rec.StatusCode = 200
			}
		case "ocr":
			if rc.OCRResponse != nil {
				rec.StatusCode = 200
			}
		}
	}
	if rec.ResolvedModel == "" && rec.StatusCode > 0 {
		rec.ResolvedModel = rc.Model
	}

	// Determine status code from errors.
	if len(rc.Errors) > 0 {
		lastErr := rc.Errors[len(rc.Errors)-1]
		if apiErr, ok := lastErr.(*models.APIError); ok {
			rec.StatusCode = apiErr.Status
			rec.ErrorMessage = apiErr.Message
			rec.ErrorCode = apiErr.Code
		} else {
			if rec.StatusCode == 0 {
				rec.StatusCode = 500
			}
			rec.ErrorMessage = lastErr.Error()
		}
	}

	if rc.Flags.Timeout && rec.StatusCode == 0 {
		rec.StatusCode = 408
	}

	// Copy timings.
	if len(rc.Timings) > 0 {
		rec.Timings = make(map[string]int64, len(rc.Timings))
		for k, v := range rc.Timings {
			rec.Timings[k] = v.Microseconds()
		}
	}

	// Copy metadata (exclude internal keys).
	if len(rc.Metadata) > 0 {
		rec.Metadata = make(map[string]string, len(rc.Metadata))
		for k, v := range rc.Metadata {
			if k == "authorization" {
				continue // Never log auth header value.
			}
			rec.Metadata[k] = v
		}
		if len(rec.Metadata) == 0 {
			rec.Metadata = nil
		}
	}

	// Guardrail results.
	if len(rc.GuardrailResults) > 0 {
		rec.GuardrailResults = make([]models.GuardrailResult, len(rc.GuardrailResults))
		copy(rec.GuardrailResults, rc.GuardrailResults)
	}

	// Optional bodies.
	if cfg.IncludeBodies {
		rec.RequestBody = rc.Request
		rec.ResponseBody = rc.Response
		rec.RequestBodyJSON, rec.ResponseBodyJSON = buildEndpointBodyJSON(rc)
		if rc.RequestHeaders != nil {
			rec.RequestHeaders = sanitizeHeaders(rc.RequestHeaders)
		}
	}

	return rec
}

func buildEndpointBodyJSON(rc *models.RequestContext) (json.RawMessage, json.RawMessage) {
	marshal := func(v any) json.RawMessage {
		if v == nil {
			return nil
		}
		b, err := json.Marshal(v)
		if err != nil {
			return nil
		}
		return b
	}

	switch rc.EndpointType {
	case "speech", "speech_stream":
		if rc.SpeechRequest == nil {
			return nil, nil
		}
		resp := map[string]any{
			"binary":       true,
			"content_type": rc.Metadata["response_content_type"],
		}
		if size := rc.Metadata["response_size_bytes"]; size != "" {
			resp["size_bytes"] = size
		}
		return marshal(rc.SpeechRequest), marshal(resp)
	case "transcription":
		if rc.TranscriptionReq == nil {
			return nil, marshal(rc.TranscriptionResp)
		}
		req := map[string]any{
			"model":           rc.TranscriptionReq.Model,
			"file_name":       rc.TranscriptionReq.FileName,
			"file_size_bytes": len(rc.TranscriptionReq.FileData),
			"language":        rc.TranscriptionReq.Language,
			"response_format": rc.TranscriptionReq.ResponseFormat,
		}
		if rc.TranscriptionReq.Temperature != nil {
			req["temperature"] = *rc.TranscriptionReq.Temperature
		}
		if audioSeconds := rc.Metadata["audio_seconds"]; audioSeconds != "" {
			req["audio_seconds"] = audioSeconds
		}
		return marshal(req), marshal(rc.TranscriptionResp)
	case "translation":
		if rc.TranslationReq == nil {
			return nil, marshal(rc.TranslationResp)
		}
		req := map[string]any{
			"model":           rc.TranslationReq.Model,
			"file_name":       rc.TranslationReq.FileName,
			"file_size_bytes": len(rc.TranslationReq.FileData),
			"prompt":          rc.TranslationReq.Prompt,
			"response_format": rc.TranslationReq.ResponseFormat,
		}
		if rc.TranslationReq.Temperature != nil {
			req["temperature"] = *rc.TranslationReq.Temperature
		}
		return marshal(req), marshal(rc.TranslationResp)
	default:
		return nil, nil
	}
}

// redactRecord applies privacy redaction to message content in the trace record.
// It creates shallow copies of request/response to avoid mutating the live objects.
func redactRecord(rec TraceRecord, r *privacy.Redactor, perKeyMode string) TraceRecord {
	if rec.RequestBody != nil {
		redactedReq := *rec.RequestBody
		redactedReq.Messages = redactMessages(rec.RequestBody.Messages, r, perKeyMode)
		rec.RequestBody = &redactedReq
	}

	if rec.ResponseBody != nil {
		redactedResp := *rec.ResponseBody
		redactedResp.Choices = redactChoices(rec.ResponseBody.Choices, r, perKeyMode)
		rec.ResponseBody = &redactedResp
	}

	return rec
}

func redactMessages(msgs []models.Message, r *privacy.Redactor, mode string) []models.Message {
	if len(msgs) == 0 {
		return msgs
	}

	out := make([]models.Message, len(msgs))
	for i, m := range msgs {
		out[i] = m

		// Redact content (json.RawMessage — could be string or structured).
		if len(m.Content) > 0 {
			out[i].Content = redactContent(m.Content, r, mode)
		}
	}
	return out
}

func redactChoices(choices []models.Choice, r *privacy.Redactor, mode string) []models.Choice {
	if len(choices) == 0 {
		return choices
	}

	out := make([]models.Choice, len(choices))
	for i, c := range choices {
		out[i] = c
		if len(c.Message.Content) > 0 {
			out[i].Message.Content = redactContent(c.Message.Content, r, mode)
		}
	}
	return out
}

var sensitiveHeaderKeys = map[string]bool{
	"authorization":    true,
	"x-api-key":        true,
	"api-key":          true,
	"x-auth-token":     true,
	"cookie":           true,
	"x-webhook-secret": true,
}

func sanitizeHeaders(h map[string][]string) map[string][]string {
	out := make(map[string][]string, len(h))
	for k, v := range h {
		if sensitiveHeaderKeys[strings.ToLower(k)] {
			out[k] = []string{"***"}
		} else {
			out[k] = v
		}
	}
	return out
}

func redactContent(content json.RawMessage, r *privacy.Redactor, mode string) json.RawMessage {
	var s string
	if err := json.Unmarshal(content, &s); err == nil {
		redacted := r.RedactForMode(s, mode)
		b, _ := json.Marshal(redacted)
		return b
	}

	var parts []map[string]any
	if err := json.Unmarshal(content, &parts); err == nil {
		for _, part := range parts {
			if part["type"] != "text" {
				continue
			}
			text, ok := part["text"].(string)
			if !ok {
				continue
			}
			part["text"] = r.RedactForMode(text, mode)
		}
		b, _ := json.Marshal(parts)
		return b
	}

	return content
}
