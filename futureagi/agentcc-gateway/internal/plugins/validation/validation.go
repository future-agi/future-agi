package validation

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// Plugin validates requests against model capabilities and limits
// before they reach the provider.
type Plugin struct {
	modelDB func() *modeldb.ModelDB
	enabled bool
}

// New creates a new validation plugin.
func New(enabled bool, modelDBGetter func() *modeldb.ModelDB) *Plugin {
	return &Plugin{
		modelDB: modelDBGetter,
		enabled: enabled,
	}
}

func (p *Plugin) Name() string  { return "validation" }
func (p *Plugin) Priority() int { return 70 } // After auth (20) + guardrails (50), before rate limit (80) and cache (200).

// ProcessRequest validates the request against model capabilities.
func (p *Plugin) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !p.enabled {
		return pipeline.ResultContinue()
	}

	db := p.modelDB()
	if db == nil {
		return pipeline.ResultContinue()
	}

	info, ok := db.Get(rc.Model)
	if !ok {
		// Unknown model — let the provider handle it.
		return pipeline.ResultContinue()
	}

	// Check deprecation — warn but don't block.
	if info.DeprecationDate != "" {
		rc.Metadata["model_deprecated"] = info.DeprecationDate
		slog.Warn("model is deprecated",
			"model", rc.Model,
			"deprecation_date", info.DeprecationDate,
			"request_id", rc.RequestID,
		)
	}

	// Skip capability check for non-chat requests (embeddings, images, etc.)
	// where rc.Request is nil.
	if rc.Request == nil {
		return pipeline.ResultContinue()
	}
	if reason, ok := info.Capabilities.ValidateRequest(rc.Request); !ok {
		return pipeline.ResultError(&models.APIError{
			Status:  http.StatusBadRequest,
			Type:    models.ErrTypeInvalidRequest,
			Code:    "model_capability_error",
			Message: reason,
		})
	}

	// Estimate input tokens for sanity check.
	if info.MaxInputTokens > 0 && rc.Request != nil {
		estimated := estimateTokens(rc.Request)
		if estimated > 0 {
			// Only block on clear violations (1.5x over limit).
			if estimated > int(float64(info.MaxInputTokens)*1.5) {
				// 413 Payload Too Large is the correct status for a
				// client-side size problem. Returning 500 here misled
				// callers into treating size issues as provider outages
				// and kicking retry loops that never succeed.
				return pipeline.ResultError(&models.APIError{
					Status:  http.StatusRequestEntityTooLarge,
					Type:    models.ErrTypeInvalidRequest,
					Code:    "input_too_large",
					Message: "estimated input tokens significantly exceed model limit",
				})
			}
			// Warn if approaching limit (95%).
			if estimated > int(float64(info.MaxInputTokens)*0.95) {
				rc.Metadata["input_token_warning"] = "approaching model input limit"
				slog.Warn("request approaching model input limit",
					"model", rc.Model,
					"estimated_tokens", estimated,
					"max_input_tokens", info.MaxInputTokens,
					"request_id", rc.RequestID,
				)
			}
		}
	}

	// Store model limits in metadata for response headers.
	if info.MaxOutputTokens > 0 {
		rc.Metadata["model_max_output_tokens"] = strconv.Itoa(info.MaxOutputTokens)
	}

	return pipeline.ResultContinue()
}

// ProcessResponse is a no-op for the validation plugin.
func (p *Plugin) ProcessResponse(_ context.Context, _ *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// estimateTokens gives a rough token count from message content.
// Uses ~4 chars per token as a conservative heuristic for text.
//
// For multimodal content blocks (image_url, input_audio, file) the raw
// base64 payload is NOT representative of real token usage — providers
// tokenize media by modality (e.g. Gemini at ~32 tokens/sec of audio,
// ~258 tokens per 1024-pixel image tile). Counting the base64 bytes as
// if they were text explodes the estimate — a 5-minute audio clip
// ( ≈6 MB base64 ) gets estimated at ~1.5M tokens and blows the 1.5x
// safety threshold, so the request is falsely rejected with
// “input_too_large“ before the provider ever sees it.
//
// When we detect structured content (a JSON array of content parts),
// we tally text parts at 4 chars/token and attribute a modest fixed
// budget per media block instead of the base64 length. Legacy string
// content is handled via the old char-count path.
func estimateTokens(req *models.ChatCompletionRequest) int {
	totalBytes := 0
	for _, msg := range req.Messages {
		if len(msg.Content) > 0 {
			totalBytes += estimateContentBytes(msg.Content)
		}
		// Account for role + name overhead.
		totalBytes += len(msg.Role) + 4
	}
	// Add tool definitions overhead.
	if len(req.Tools) > 0 {
		toolBytes, _ := json.Marshal(req.Tools)
		totalBytes += len(toolBytes)
	}
	return totalBytes / 4
}

// Fixed per-block byte allotments for multimodal parts, expressed in
// the same 4-chars-per-token space as textual content so the caller's
// “/ 4“ step translates them into realistic token counts.
//   - image: ~258 tokens for a standard tile → 1032 char-equivalents
//   - audio: ~1500 tokens for up to ~45s (most eval clips) → 6000
//   - pdf  : ~2000 tokens / page, assume 1 page baseline → 8000
//
// These are intentionally generous so the gateway only blocks truly
// pathological payloads. The upstream provider runs the real tokenizer.
const (
	_multimodalImageBudget = 1032
	_multimodalAudioBudget = 6000
	_multimodalFileBudget  = 8000
)

// estimateContentBytes returns a character-count estimate of a single
// message's “content“ field that is safe for multimodal blocks.
func estimateContentBytes(content json.RawMessage) int {
	// Fast path: a plain JSON string → count its characters directly.
	trimmed := bytes.TrimSpace(content)
	if len(trimmed) == 0 {
		return 0
	}
	if trimmed[0] == '"' {
		var s string
		if err := json.Unmarshal(trimmed, &s); err == nil {
			return len(s)
		}
		return len(trimmed)
	}
	if trimmed[0] != '[' {
		// Unknown shape — fall back to raw length (matches prior behaviour).
		return len(trimmed)
	}
	// Structured multimodal array.
	var parts []struct {
		Type       string          `json:"type"`
		Text       string          `json:"text"`
		ImageURL   json.RawMessage `json:"image_url"`
		InputAudio json.RawMessage `json:"input_audio"`
		File       json.RawMessage `json:"file"`
	}
	if err := json.Unmarshal(trimmed, &parts); err != nil {
		return len(trimmed)
	}
	total := 0
	for _, p := range parts {
		switch p.Type {
		case "text":
			total += len(p.Text)
		case "image_url":
			total += _multimodalImageBudget
		case "input_audio":
			total += _multimodalAudioBudget
		case "file":
			total += _multimodalFileBudget
		default:
			// Unknown block type — be conservative and count ~1 token
			// so we still notice obvious bloat.
			total += 64
		}
	}
	return total
}
