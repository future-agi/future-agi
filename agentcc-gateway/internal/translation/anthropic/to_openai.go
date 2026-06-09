// Package anthropic — Anthropic Messages API request → OpenAI canonical.
package anthropic

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// RequestToCanonical converts a raw Anthropic Messages API request body into
// the gateway's canonical OpenAI ChatCompletionRequest.
//
// Translation highlights:
//   - system parameter (string or []TextBlockParam) → role:"system" message at index 0
//   - tool_result user blocks  → separate role:"tool" messages emitted before the user message
//   - tool_use assistant blocks → message.tool_calls[]
//   - base64 images            → data: URLs
//   - thinking config          → Extra["anthropic_thinking_config"]
//   - top_k                    → dropped, recorded in drops
//   - tool names > 64 chars    → truncated, mapping stored in Extra["tool_name_mapping"]
func (t *Translator) RequestToCanonical(body []byte) (*models.ChatCompletionRequest, []string, error) {
	var req MessagesRequest
	if err := json.Unmarshal(body, &req); err != nil {
		return nil, nil, fmt.Errorf("anthropic: unmarshal request: %w", err)
	}

	out := &models.ChatCompletionRequest{
		Model: req.Model,
		Extra: make(map[string]json.RawMessage),
	}

	var drops []string

	// ── Sampling params ────────────────────────────────────────────────────────
	out.Temperature = req.Temperature
	out.TopP = req.TopP
	if req.MaxTokens > 0 {
		mt := req.MaxTokens
		out.MaxTokens = &mt
	}
	if req.TopK != nil {
		drops = append(drops, "top_k_unsupported")
	}

	// ── stop_sequences → stop ──────────────────────────────────────────────────
	if len(req.StopSequences) > 0 {
		stopJSON, err := json.Marshal(req.StopSequences)
		if err != nil {
			return nil, nil, fmt.Errorf("anthropic: marshal stop_sequences: %w", err)
		}
		out.Stop = stopJSON
	}

	// ── Tools + tool name truncation ───────────────────────────────────────────
	toolNameMapping := map[string]string{} // short → original
	if len(req.Tools) > 0 {
		oaiTools := make([]models.Tool, 0, len(req.Tools))
		for _, at := range req.Tools {
			name := at.Name
			if len(name) > 64 {
				short := name[:64]
				toolNameMapping[short] = name
				name = short
			}
			oaiTools = append(oaiTools, models.Tool{
				Type: "function",
				Function: models.ToolFunction{
					Name:        name,
					Description: at.Description,
					Parameters:  at.InputSchema,
				},
			})
		}
		out.Tools = oaiTools
	}
	if len(toolNameMapping) > 0 {
		b, err := json.Marshal(toolNameMapping)
		if err != nil {
			return nil, nil, fmt.Errorf("anthropic: marshal tool_name_mapping: %w", err)
		}
		out.Extra["tool_name_mapping"] = b
	}

	// ── tool_choice translation ────────────────────────────────────────────────
	// Anthropic and OpenAI disagree on the wire shape:
	//   Anthropic: {"type":"auto"} | {"type":"any"} | {"type":"none"} |
	//              {"type":"tool","name":"foo"} (+ optional disable_parallel_tool_use)
	//   OpenAI:    "auto" | "none" | "required" |
	//              {"type":"function","function":{"name":"foo"}}
	if req.ToolChoice != nil {
		translated, tcDrops, err := translateAnthropicToolChoice(req.ToolChoice, toolNameMapping)
		if err != nil {
			return nil, nil, fmt.Errorf("anthropic: tool_choice: %w", err)
		}
		if translated != nil {
			out.ToolChoice = translated
		}
		drops = append(drops, tcDrops...)
	}

	// ── thinking config → Extra ────────────────────────────────────────────────
	if req.Thinking != nil {
		b, err := json.Marshal(req.Thinking)
		if err != nil {
			return nil, nil, fmt.Errorf("anthropic: marshal thinking_config: %w", err)
		}
		out.Extra["anthropic_thinking_config"] = b
	}

	// ── Build messages slice ───────────────────────────────────────────────────
	var msgs []models.Message

	// System: flatten before all conversation turns.
	if req.System != nil {
		sysMsg, err := flattenSystem(req.System)
		if err != nil {
			return nil, nil, fmt.Errorf("anthropic: system param: %w", err)
		}
		msgs = append(msgs, sysMsg)
	}

	// Conversation turns.
	for _, am := range req.Messages {
		converted, err := convertMessage(am, toolNameMapping)
		if err != nil {
			return nil, nil, fmt.Errorf("anthropic: convert message role=%q: %w", am.Role, err)
		}
		msgs = append(msgs, converted...)
	}

	out.Messages = msgs

	// Clean up Extra map if nothing was added.
	if len(out.Extra) == 0 {
		out.Extra = nil
	}

	return out, drops, nil
}

// flattenSystem converts the Anthropic system parameter (string or array of
// TextBlockParam) into a single role:"system" Message.
// cache_control annotations are passed through as OpenAI content-part metadata
// by encoding the full block array as JSON content (not just a bare string)
// when array form is used — downstream providers that don't understand it will
// just see the text parts.
func flattenSystem(raw json.RawMessage) (models.Message, error) {
	// Try plain string first.
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		content, _ := json.Marshal(s)
		return models.Message{Role: "system", Content: content}, nil
	}

	// Try []TextBlockParam.
	var blocks []TextBlockParam
	if err := json.Unmarshal(raw, &blocks); err != nil {
		return models.Message{}, fmt.Errorf("system must be string or []TextBlockParam: %w", err)
	}

	// Build a plain text concatenation for the content field (safest for all
	// downstream providers). If there is exactly one block and no cache_control
	// we emit a bare string; otherwise we emit the full block array so that
	// cache_control metadata isn't lost for Claude-backed routes.
	if len(blocks) == 1 && blocks[0].CacheControl == nil {
		content, _ := json.Marshal(blocks[0].Text)
		return models.Message{Role: "system", Content: content}, nil
	}

	// Multiple blocks or cache_control present: keep the richer form.
	// We encode as an OpenAI-compatible content-parts array; text parts only
	// since system messages don't carry images.
	type textPart struct {
		Type         string        `json:"type"`
		Text         string        `json:"text"`
		CacheControl *CacheControl `json:"cache_control,omitempty"`
	}
	parts := make([]textPart, 0, len(blocks))
	for _, b := range blocks {
		parts = append(parts, textPart{
			Type:         "text",
			Text:         b.Text,
			CacheControl: b.CacheControl,
		})
	}
	content, err := json.Marshal(parts)
	if err != nil {
		return models.Message{}, err
	}
	return models.Message{Role: "system", Content: content}, nil
}

// convertMessage converts one Anthropic message into zero or more OpenAI
// messages. The toolNameMapping is used for any tool_result blocks whose
// function name needs to be truncated (lookup is by tool_use_id, not name,
// so no name substitution is needed here).
//
// Returns multiple messages when the Anthropic turn contains tool_result
// blocks, which must become separate role:"tool" messages emitted BEFORE
// the main user message.
func convertMessage(am AnthropicMsg, _ map[string]string) ([]models.Message, error) {
	// Fast path: content is a plain string.
	var plain string
	if err := json.Unmarshal(am.Content, &plain); err == nil {
		content, _ := json.Marshal(plain)
		return []models.Message{{Role: am.Role, Content: content}}, nil
	}

	// Content is an array of ContentBlock.
	var blocks []ContentBlock
	if err := json.Unmarshal(am.Content, &blocks); err != nil {
		return nil, fmt.Errorf("content must be string or []ContentBlock: %w", err)
	}

	switch am.Role {
	case "user":
		return convertUserMessage(blocks)
	case "assistant":
		return convertAssistantMessage(blocks)
	default:
		// Unknown role: pass through with plain text concatenation.
		text := extractText(blocks)
		content, _ := json.Marshal(text)
		return []models.Message{{Role: am.Role, Content: content}}, nil
	}
}

// convertUserMessage handles role:"user" turns. It:
//   - emits role:"tool" messages (one per unique tool_use_id) for tool_result blocks
//   - collects remaining blocks (text/image) into the user message content parts
//
// Role:"tool" messages are emitted BEFORE the user message to satisfy OpenAI's
// expectation that tool results appear immediately after the assistant turn that
// requested them.
func convertUserMessage(blocks []ContentBlock) ([]models.Message, error) {
	// Separate tool_result blocks from regular content.
	toolResultsByID := map[string][]ContentBlock{}
	toolResultOrder := []string{} // preserve ordering
	var regularBlocks []ContentBlock

	for _, b := range blocks {
		if b.Type == "tool_result" {
			if _, seen := toolResultsByID[b.ToolUseID]; !seen {
				toolResultOrder = append(toolResultOrder, b.ToolUseID)
			}
			toolResultsByID[b.ToolUseID] = append(toolResultsByID[b.ToolUseID], b)
		} else {
			regularBlocks = append(regularBlocks, b)
		}
	}

	var msgs []models.Message

	// Emit role:"tool" messages first.
	for _, id := range toolResultOrder {
		results := toolResultsByID[id]
		toolMsg, err := buildToolMessage(id, results)
		if err != nil {
			return nil, fmt.Errorf("tool_result tool_use_id=%q: %w", id, err)
		}
		msgs = append(msgs, toolMsg)
	}

	// Emit the user message if there are non-tool blocks.
	if len(regularBlocks) > 0 {
		content, err := convertContentParts(regularBlocks)
		if err != nil {
			return nil, err
		}
		msgs = append(msgs, models.Message{Role: "user", Content: content})
	}

	return msgs, nil
}

// buildToolMessage creates a single role:"tool" Message for all tool_result
// blocks that share a tool_use_id. Multiple results are collapsed into one
// message with an array content body.
func buildToolMessage(toolUseID string, results []ContentBlock) (models.Message, error) {
	if len(results) == 1 {
		// Simple case: single tool result.
		content, err := toolResultContent(results[0])
		if err != nil {
			return models.Message{}, err
		}
		return models.Message{
			Role:       "tool",
			ToolCallID: toolUseID,
			Content:    content,
		}, nil
	}

	// Multiple results collapsed into one content string (concatenation).
	var sb strings.Builder
	for i, r := range results {
		if i > 0 {
			sb.WriteString("\n")
		}
		if r.Content != nil {
			var s string
			if err := json.Unmarshal(r.Content, &s); err == nil {
				sb.WriteString(s)
				continue
			}
		}
		if r.Text != "" {
			sb.WriteString(r.Text)
		}
	}
	content, _ := json.Marshal(sb.String())
	return models.Message{
		Role:       "tool",
		ToolCallID: toolUseID,
		Content:    content,
	}, nil
}

// toolResultContent extracts the OpenAI content from a single tool_result block.
func toolResultContent(b ContentBlock) (json.RawMessage, error) {
	if b.Content != nil {
		// content can be a string or an array of blocks.
		var s string
		if err := json.Unmarshal(b.Content, &s); err == nil {
			return json.Marshal(s)
		}
		// Array of nested blocks — extract text.
		var nested []ContentBlock
		if err := json.Unmarshal(b.Content, &nested); err == nil {
			return json.Marshal(extractText(nested))
		}
		// Fall through: return raw.
		return b.Content, nil
	}
	if b.Text != "" {
		return json.Marshal(b.Text)
	}
	empty, _ := json.Marshal("")
	return empty, nil
}

// convertAssistantMessage handles role:"assistant" turns. It:
//   - collects text blocks into message.content
//   - converts tool_use blocks into message.tool_calls
//   - skips thinking blocks (those are already in the response path)
func convertAssistantMessage(blocks []ContentBlock) ([]models.Message, error) {
	var textParts []string
	var toolCalls []models.ToolCall

	for _, b := range blocks {
		switch b.Type {
		case "text":
			if b.Text != "" {
				textParts = append(textParts, b.Text)
			}
		case "tool_use":
			args, err := toolUseArguments(b.Input)
			if err != nil {
				return nil, fmt.Errorf("tool_use id=%q: marshal arguments: %w", b.ID, err)
			}
			toolCalls = append(toolCalls, models.ToolCall{
				ID:   b.ID,
				Type: "function",
				Function: models.FunctionCall{
					Name:      b.Name,
					Arguments: args,
				},
			})
		case "thinking":
			// thinking blocks in assistant messages are carried forward via the
			// response path; skip silently in the request direction.
		}
	}

	text := strings.Join(textParts, "")
	var content json.RawMessage
	if text != "" {
		content, _ = json.Marshal(text)
	}

	msg := models.Message{
		Role:      "assistant",
		Content:   content,
		ToolCalls: toolCalls,
	}
	return []models.Message{msg}, nil
}

// toolUseArguments marshals the input JSON into a compact string form suitable
// for OpenAI's function.arguments field (which is a JSON-encoded string, not
// a raw object).
func toolUseArguments(input json.RawMessage) (string, error) {
	if input == nil || string(input) == "null" {
		return "{}", nil
	}
	// input is already valid JSON; compact and return as string.
	return string(input), nil
}

// oaiImageURL is the OpenAI image_url sub-object used in content parts.
type oaiImageURL struct {
	URL string `json:"url"`
}

// oaiImageURLPart is a vision content part for OpenAI.
type oaiImageURLPart struct {
	Type     string      `json:"type"`
	ImageURL oaiImageURL `json:"image_url"`
}

// oaiTextPart is a text content part for OpenAI.
type oaiTextPart struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

// convertContentParts converts a slice of non-tool_result ContentBlocks into
// an OpenAI content field. Returns a JSON string if there's only plain text;
// returns a JSON array of content parts when images are present.
func convertContentParts(blocks []ContentBlock) (json.RawMessage, error) {

	// Check if we have any image blocks.
	hasImage := false
	for _, b := range blocks {
		if b.Type == "image" {
			hasImage = true
			break
		}
	}

	if !hasImage {
		// Plain text only — return a bare string.
		var sb strings.Builder
		for _, b := range blocks {
			if b.Type == "text" {
				sb.WriteString(b.Text)
			}
		}
		return json.Marshal(sb.String())
	}

	// Mixed content — return parts array.
	var parts []interface{}
	for _, b := range blocks {
		switch b.Type {
		case "text":
			if b.Text != "" {
				parts = append(parts, oaiTextPart{Type: "text", Text: b.Text})
			}
		case "image":
			if b.Source == nil {
				continue
			}
			url, err := imageSourceToURL(b.Source)
			if err != nil {
				return nil, err
			}
			parts = append(parts, oaiImageURLPart{
				Type:     "image_url",
				ImageURL: oaiImageURL{URL: url},
			})
		}
	}
	return json.Marshal(parts)
}

// imageSourceToURL converts an Anthropic ImageSource to an OpenAI image_url string.
// base64 sources become data: URIs; URL sources pass through unchanged.
func imageSourceToURL(src *ImageSource) (string, error) {
	switch src.Type {
	case "base64":
		mediaType := src.MediaType
		if mediaType == "" {
			mediaType = "image/jpeg"
		}
		// Validate that the data field is valid base64.
		if _, err := base64.StdEncoding.DecodeString(src.Data); err != nil {
			// Try URL-safe base64 before giving up.
			if _, err2 := base64.URLEncoding.DecodeString(src.Data); err2 != nil {
				return "", fmt.Errorf("image source base64 data is not valid base64: %w", err)
			}
		}
		return fmt.Sprintf("data:%s;base64,%s", mediaType, src.Data), nil
	case "url":
		return src.URL, nil
	default:
		return "", fmt.Errorf("unsupported image source type: %q", src.Type)
	}
}

// extractText concatenates the text from all text-type ContentBlocks.
func extractText(blocks []ContentBlock) string {
	var sb strings.Builder
	for _, b := range blocks {
		if b.Type == "text" {
			sb.WriteString(b.Text)
		}
	}
	return sb.String()
}

// translateAnthropicToolChoice converts an Anthropic tool_choice object into the
// OpenAI equivalent. Anthropic uses {"type":"auto|any|none|tool", "name":...};
// OpenAI uses the strings "auto"/"none"/"required" or a function-selector object.
//
// toolNameMapping maps truncated (short) → original; we invert it so a request
// that selects a tool by its original (long) name still maps to the truncated
// name that was actually sent to the upstream as a tool definition.
func translateAnthropicToolChoice(
	raw json.RawMessage,
	toolNameMapping map[string]string,
) (json.RawMessage, []string, error) {
	var obj struct {
		Type                   string `json:"type"`
		Name                   string `json:"name,omitempty"`
		DisableParallelToolUse *bool  `json:"disable_parallel_tool_use,omitempty"`
	}
	if err := json.Unmarshal(raw, &obj); err != nil {
		return nil, nil, fmt.Errorf("unmarshal: %w", err)
	}

	var drops []string
	if obj.DisableParallelToolUse != nil && *obj.DisableParallelToolUse {
		// OpenAI expresses parallelism via top-level parallel_tool_calls, not
		// inside tool_choice. We don't currently forward it end-to-end, so drop.
		drops = append(drops, "disable_parallel_tool_use_unsupported")
	}

	switch obj.Type {
	case "auto":
		return json.RawMessage(`"auto"`), drops, nil
	case "none":
		return json.RawMessage(`"none"`), drops, nil
	case "any":
		return json.RawMessage(`"required"`), drops, nil
	case "tool":
		if obj.Name == "" {
			return nil, drops, fmt.Errorf("tool_choice type=tool requires name")
		}
		name := obj.Name
		// Invert short→original so selection by original name resolves back to
		// the truncated name actually forwarded to the upstream.
		for short, orig := range toolNameMapping {
			if orig == name {
				name = short
				break
			}
		}
		wrapped, err := json.Marshal(map[string]any{
			"type":     "function",
			"function": map[string]string{"name": name},
		})
		if err != nil {
			return nil, drops, err
		}
		return wrapped, drops, nil
	default:
		// Unknown type — best-effort drop rather than crash.
		drops = append(drops, "unknown_tool_choice_type:"+obj.Type)
		return nil, drops, nil
	}
}
