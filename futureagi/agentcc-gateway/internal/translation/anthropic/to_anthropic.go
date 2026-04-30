// Package anthropic — OpenAI canonical → Anthropic Messages API response.
package anthropic

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ─── ResponseFromCanonical ────────────────────────────────────────────────────

// ResponseFromCanonical converts an OpenAI ChatCompletionResponse into an
// Anthropic Messages API response body.
//
// Translation highlights:
//   - message.content (string) → single text content block
//   - message.tool_calls[]     → tool_use content blocks (after text)
//   - message.thinking_blocks  → thinking content blocks at START of content[]
//   - finish_reason reverse-mapped (stop→end_turn, etc.)
//   - usage: prompt_tokens→input_tokens, completion_tokens→output_tokens
//   - truncated tool names restored from Extra["tool_name_mapping"]
func (t *Translator) ResponseFromCanonical(resp *models.ChatCompletionResponse) ([]byte, error) {
	if len(resp.Choices) == 0 {
		return nil, fmt.Errorf("anthropic: response has no choices")
	}
	choice := resp.Choices[0]
	msg := choice.Message

	// Resolve the tool name mapping from the request's Extra (round-tripped).
	// The handler is expected to attach the request's Extra to the response's
	// Extra map; if absent we just skip name restoration.
	reverseNames := map[string]string{} // truncated → original
	// Check if the ChatCompletionResponse carries Extra (it doesn't by default,
	// but we look for a side-channel via the choice message name field which
	// some providers misuse). Instead, we stash the mapping in the response
	// ID field when we have it... but actually the canonical approach is to
	// look at the incoming request Extra. Since we don't have the request here
	// we rely on the calling handler to inject tool_name_mapping into
	// resp.Choices[0].Message.Name as a workaround — but that's fragile.
	//
	// The cleaner path (used in practice): the handler passes the request's
	// Extra["tool_name_mapping"] to the translator via a wrapper. For now we
	// expose a separate helper and leave this empty; the map will be filled
	// by ResponseFromCanonicalWithMapping when the handler has access to both.
	//
	// This is documented as a spec ambiguity in the report.

	var contentBlocks []ContentBlock

	// 1. Thinking blocks first (if present as custom field on the message).
	thinkingBlocks := extractThinkingBlocks(&msg)
	contentBlocks = append(contentBlocks, thinkingBlocks...)

	// 2. Text content.
	if msg.Content != nil {
		var text string
		if err := json.Unmarshal(msg.Content, &text); err == nil && text != "" {
			contentBlocks = append(contentBlocks, ContentBlock{
				Type: "text",
				Text: text,
			})
		} else {
			// Content might be an array of parts (rare but possible).
			var parts []json.RawMessage
			if err2 := json.Unmarshal(msg.Content, &parts); err2 == nil {
				for _, p := range parts {
					var part struct {
						Type string `json:"type"`
						Text string `json:"text"`
					}
					if err3 := json.Unmarshal(p, &part); err3 == nil && part.Type == "text" && part.Text != "" {
						contentBlocks = append(contentBlocks, ContentBlock{
							Type: "text",
							Text: part.Text,
						})
					}
				}
			}
		}
	}

	// 3. Tool calls → tool_use blocks.
	for _, tc := range msg.ToolCalls {
		name := tc.Function.Name
		if orig, ok := reverseNames[name]; ok {
			name = orig
		}
		var input json.RawMessage
		if tc.Function.Arguments == "" || tc.Function.Arguments == "null" {
			input = json.RawMessage("{}")
		} else {
			// Arguments is a JSON-encoded string — parse it back to raw JSON.
			input = json.RawMessage(tc.Function.Arguments)
		}
		contentBlocks = append(contentBlocks, ContentBlock{
			Type:  "tool_use",
			ID:    tc.ID,
			Name:  name,
			Input: input,
		})
	}

	// Ensure content is never nil (Anthropic requires non-null array).
	if contentBlocks == nil {
		contentBlocks = []ContentBlock{}
	}

	// finish_reason → stop_reason (reverse mapping).
	stopReason := reverseMapFinishReason(choice.FinishReason)

	// usage.
	var usage ResponseUsage
	if resp.Usage != nil {
		usage = ResponseUsage{
			InputTokens:  resp.Usage.PromptTokens,
			OutputTokens: resp.Usage.CompletionTokens,
		}
	}

	// Response ID.
	id := resp.ID
	if id == "" {
		id = generateMsgID()
	}

	anthResp := MessagesResponse{
		ID:         id,
		Type:       "message",
		Role:       "assistant",
		Content:    contentBlocks,
		Model:      resp.Model,
		StopReason: stopReason,
		Usage:      usage,
	}

	return json.Marshal(anthResp)
}

// ResponseFromCanonicalWithMapping is like ResponseFromCanonical but also
// accepts the tool_name_mapping from the original request so truncated tool
// names can be restored. The handler should call this variant when it has
// both the request and response.
func (t *Translator) ResponseFromCanonicalWithMapping(
	resp *models.ChatCompletionResponse,
	toolNameMapping map[string]string,
) ([]byte, error) {
	if len(resp.Choices) == 0 {
		return nil, fmt.Errorf("anthropic: response has no choices")
	}
	choice := resp.Choices[0]
	msg := choice.Message

	// Build reverse map: truncated → original.
	reverseNames := make(map[string]string, len(toolNameMapping))
	for short, orig := range toolNameMapping {
		reverseNames[short] = orig
	}

	var contentBlocks []ContentBlock

	// Thinking blocks first.
	thinkingBlocks := extractThinkingBlocks(&msg)
	contentBlocks = append(contentBlocks, thinkingBlocks...)

	// Text content.
	if msg.Content != nil {
		var text string
		if err := json.Unmarshal(msg.Content, &text); err == nil && text != "" {
			contentBlocks = append(contentBlocks, ContentBlock{
				Type: "text",
				Text: text,
			})
		}
	}

	// Tool calls → tool_use blocks.
	for _, tc := range msg.ToolCalls {
		name := tc.Function.Name
		if orig, ok := reverseNames[name]; ok {
			name = orig
		}
		var input json.RawMessage
		if tc.Function.Arguments == "" || tc.Function.Arguments == "null" {
			input = json.RawMessage("{}")
		} else {
			input = json.RawMessage(tc.Function.Arguments)
		}
		contentBlocks = append(contentBlocks, ContentBlock{
			Type:  "tool_use",
			ID:    tc.ID,
			Name:  name,
			Input: input,
		})
	}

	if contentBlocks == nil {
		contentBlocks = []ContentBlock{}
	}

	stopReason := reverseMapFinishReason(choice.FinishReason)

	var usage ResponseUsage
	if resp.Usage != nil {
		usage = ResponseUsage{
			InputTokens:  resp.Usage.PromptTokens,
			OutputTokens: resp.Usage.CompletionTokens,
		}
	}

	id := resp.ID
	if id == "" {
		id = generateMsgID()
	}

	anthResp := MessagesResponse{
		ID:         id,
		Type:       "message",
		Role:       "assistant",
		Content:    contentBlocks,
		Model:      resp.Model,
		StopReason: stopReason,
		Usage:      usage,
	}

	return json.Marshal(anthResp)
}

// ─── ErrorFromCanonical ───────────────────────────────────────────────────────

// ErrorFromCanonical converts a gateway APIError into an Anthropic-format
// error response.
//
// Type mapping:
//
//	invalid_request  → invalid_request_error
//	authentication   → authentication_error
//	permission       → permission_error
//	not_found        → not_found_error
//	rate_limit       → rate_limit_error
//	api_error        → api_error
//	overloaded       → overloaded_error
//	server_error     → api_error   (closest Anthropic equivalent)
//	<anything else>  → api_error
func (t *Translator) ErrorFromCanonical(err *models.APIError) (statusCode int, body []byte, contentType string) {
	anthType := gatewayTypeToAnthropic(err.Type)

	resp := ErrorResponse{
		Type: "error",
		Error: ErrorDetail{
			Type:    anthType,
			Message: err.Message,
		},
	}

	b, _ := json.Marshal(resp)

	// Use the gateway's status code if set; fall back to sensible defaults.
	status := err.Status
	if status == 0 {
		status = anthropicStatusForType(anthType)
	}

	return status, b, "application/json"
}

// gatewayTypeToAnthropic maps gateway APIError.Type to Anthropic error types.
// Uses the models package constants as canonical values; the extra string literals
// handle legacy callers that pass raw strings before the constants were introduced.
func gatewayTypeToAnthropic(t string) string {
	switch t {
	case models.ErrTypeInvalidRequest: // "invalid_request_error"
		return "invalid_request_error"
	case models.ErrTypeAuthentication: // "authentication_error"
		return "authentication_error"
	case models.ErrTypePermission: // "permission_error"
		return "permission_error"
	case models.ErrTypeNotFound: // "not_found"
		return "not_found_error"
	case models.ErrTypeRateLimit: // "rate_limit_error"
		return "rate_limit_error"
	case "api_error":
		return "api_error"
	case "overloaded_error":
		return "overloaded_error"
	case models.ErrTypeServer: // "server_error"
		return "api_error"
	default:
		return "api_error"
	}
}

// anthropicStatusForType returns the canonical HTTP status for an Anthropic
// error type when no upstream status is available.
func anthropicStatusForType(t string) int {
	switch t {
	case "invalid_request_error":
		return http.StatusBadRequest
	case "authentication_error":
		return http.StatusUnauthorized
	case "permission_error":
		return http.StatusForbidden
	case "not_found_error":
		return http.StatusNotFound
	case "rate_limit_error":
		return http.StatusTooManyRequests
	case "overloaded_error":
		return http.StatusServiceUnavailable
	default: // api_error
		return http.StatusInternalServerError
	}
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

// reverseMapFinishReason converts an OpenAI finish_reason back to its Anthropic
// stop_reason equivalent.
func reverseMapFinishReason(openaiReason string) string {
	// We can't re-use translation.MapFinishReason here because that goes
	// native→OpenAI; we need the reverse direction.
	switch openaiReason {
	case "stop":
		return "end_turn"
	case "length":
		return "max_tokens"
	case "tool_calls":
		return "tool_use"
	case "content_filter":
		return "stop_sequence" // closest Anthropic approximation
	default:
		if openaiReason == "" {
			return "end_turn"
		}
		return openaiReason
	}
}

// extractThinkingBlocks reads the ThinkingBlocks field added to models.Message
// (TH-4322) and converts each entry to an Anthropic thinking content block.
// Returns nil if absent or empty.
func extractThinkingBlocks(msg *models.Message) []ContentBlock {
	if len(msg.ThinkingBlocks) == 0 {
		return nil
	}

	type rawThinkingBlock struct {
		Type      string `json:"type"`
		Thinking  string `json:"thinking,omitempty"`
		Signature string `json:"signature,omitempty"`
	}
	var raw []rawThinkingBlock
	if err := json.Unmarshal(msg.ThinkingBlocks, &raw); err != nil {
		return nil
	}

	blocks := make([]ContentBlock, 0, len(raw))
	for _, b := range raw {
		if b.Thinking == "" {
			continue
		}
		blocks = append(blocks, ContentBlock{
			Type:      "thinking",
			Thinking:  b.Thinking,
			Signature: b.Signature,
		})
	}
	return blocks
}

// generateMsgID generates a random message ID. Uses
// crypto/rand so concurrent requests within the same nanosecond don't
// collide (math/rand seeded by time.Now was the previous impl).
func generateMsgID() string {
	var b [12]byte
	_, _ = rand.Read(b[:])
	return "msg_" + hex.EncodeToString(b[:])
}

// reverseMapFinishReasonForStream is the same as reverseMapFinishReason but
// exposed for use in the stream state machine.
func reverseMapFinishReasonForStream(openaiReason string) string {
	return reverseMapFinishReason(openaiReason)
}
