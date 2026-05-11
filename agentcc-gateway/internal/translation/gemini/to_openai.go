package gemini

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// RequestToCanonical parses a Gemini generateContent / streamGenerateContent
// request body and returns an equivalent OpenAI ChatCompletionRequest.
//
// Drops (non-fatal omissions) are returned as a string slice — the caller
// logs them into rc.Metadata["translation_drops"].
func (t *Translator) RequestToCanonical(body []byte) (*models.ChatCompletionRequest, []string, error) {
	var req geminiRequest
	if err := json.Unmarshal(body, &req); err != nil {
		return nil, nil, fmt.Errorf("gemini inbound: failed to parse request: %w", err)
	}

	var drops []string
	out := &models.ChatCompletionRequest{}

	// ── 1. systemInstruction → role:"system" at index 0 ──────────────────────
	if req.SystemInstruction != nil {
		text := flattenPartsText(req.SystemInstruction.Parts)
		if text != "" {
			textBytes, _ := json.Marshal(text)
			out.Messages = append(out.Messages, models.Message{
				Role:    "system",
				Content: json.RawMessage(textBytes),
			})
		}
	}

	// ── 2. Pre-assign per-call IDs ────────────────────────────────────────────
	// Gemini's functionCall/functionResponse parts carry only a name — no
	// correlation ID. OpenAI canonical requires a unique tool_call_id per call.
	// If the model calls the same function twice, keying a map by name collapses
	// the pair into a single ID (both calls get the same id, both responses
	// bind to it) and the conversation becomes ambiguous downstream.
	//
	// Pre-assign IDs in call-order, grouped by name. Translation then consumes
	// calls and responses in order via per-name cursors: the Nth functionCall
	// for a given name pairs with the Nth functionResponse for that name.
	callIDsByName := make(map[string][]string)
	for _, content := range req.Contents {
		for _, part := range content.Parts {
			if part.FunctionCall != nil {
				name := part.FunctionCall.Name
				callIDsByName[name] = append(callIDsByName[name], newCallID())
			}
		}
	}
	callCursor := make(map[string]int) // name → next unclaimed call index
	respCursor := make(map[string]int) // name → next unclaimed response index

	// ── 3. Convert contents[] ────────────────────────────────────────────────
	for _, content := range req.Contents {
		msgs, partDrops := translateContent(content, callIDsByName, callCursor, respCursor)
		drops = append(drops, partDrops...)
		out.Messages = append(out.Messages, msgs...)
	}

	// Store the per-name ID lists in Extra so a response path can reconstruct
	// the same correlation if needed.
	if len(callIDsByName) > 0 {
		mapBytes, _ := json.Marshal(callIDsByName)
		if out.Extra == nil {
			out.Extra = make(map[string]json.RawMessage)
		}
		out.Extra["gemini_tool_call_id_map"] = json.RawMessage(mapBytes)
	}

	// ── 4. generationConfig ──────────────────────────────────────────────────
	if cfg := req.GenerationConfig; cfg != nil {
		out.Temperature = cfg.Temperature
		out.TopP = cfg.TopP

		if cfg.TopK != nil {
			drops = append(drops, "top_k_unsupported")
		}
		if cfg.MaxOutputTokens != nil {
			out.MaxTokens = cfg.MaxOutputTokens
		}
		if cfg.CandidateCount != nil {
			out.N = cfg.CandidateCount
		}
		if len(cfg.StopSequences) > 0 {
			stopBytes, _ := json.Marshal(cfg.StopSequences)
			out.Stop = json.RawMessage(stopBytes)
		}
		if len(cfg.ResponseModalities) > 0 {
			drops = append(drops, "response_modalities_unsupported")
		}
		// responseMimeType="application/json" → ResponseFormat
		if cfg.ResponseMimeType == "application/json" {
			if len(cfg.ResponseSchema) > 0 {
				// json_schema variant: wrap schema into OpenAI's json_schema format
				schemaWrapper, _ := json.Marshal(map[string]json.RawMessage{
					"schema": cfg.ResponseSchema,
				})
				out.ResponseFormat = &models.ResponseFormat{
					Type:       "json_schema",
					JSONSchema: json.RawMessage(schemaWrapper),
				}
			} else {
				out.ResponseFormat = &models.ResponseFormat{Type: "json_object"}
			}
		}
	}

	// ── 5. tools[] ───────────────────────────────────────────────────────────
	for _, toolDecl := range req.Tools {
		for _, fn := range toolDecl.FunctionDeclarations {
			out.Tools = append(out.Tools, models.Tool{
				Type: "function",
				Function: models.ToolFunction{
					Name:        fn.Name,
					Description: fn.Description,
					Parameters:  fn.Parameters,
				},
			})
		}
	}

	// ── 6. toolConfig → tool_choice ──────────────────────────────────────────
	// Gemini expresses tool constraints via mode + allowedFunctionNames.
	// OpenAI tool_choice is coarser (auto|none|required|specific-function), so
	// some Gemini configurations can't be represented 1:1 — those are dropped.
	if req.ToolConfig != nil && req.ToolConfig.FunctionCallingConfig != nil {
		fc := req.ToolConfig.FunctionCallingConfig
		mode := fc.Mode
		allowed := fc.AllowedFunctionNames

		switch mode {
		case "NONE":
			choice, _ := json.Marshal("none")
			out.ToolChoice = choice
		case "ANY":
			switch len(allowed) {
			case 0:
				// Force *some* tool.
				choice, _ := json.Marshal("required")
				out.ToolChoice = choice
			case 1:
				// Force *this* tool — expressible as OpenAI specific-function.
				choice, err := json.Marshal(map[string]any{
					"type":     "function",
					"function": map[string]string{"name": allowed[0]},
				})
				if err == nil {
					out.ToolChoice = choice
				}
			default:
				// Multiple allowed names — OpenAI can't express "force one of these
				// specific tools". Fall back to "required" and flag the loss.
				choice, _ := json.Marshal("required")
				out.ToolChoice = choice
				drops = append(drops, "allowed_function_names_subset_unsupported")
			}
		case "AUTO", "":
			choice, _ := json.Marshal("auto")
			out.ToolChoice = choice
			// AUTO + allowedFunctionNames can't be expressed via tool_choice;
			// the upstream would need the tools[] list itself filtered, which
			// we don't do here.
			if len(allowed) > 0 {
				drops = append(drops, "allowed_function_names_in_auto_unsupported")
			}
		default:
			choice, _ := json.Marshal("auto")
			out.ToolChoice = choice
		}
	}

	// ── 7. safetySettings → drop ─────────────────────────────────────────────
	if len(req.SafetySettings) > 0 {
		drops = append(drops, "safety_settings_unsupported")
	}

	// ── 8. cachedContent → drop ──────────────────────────────────────────────
	if req.CachedContent != "" {
		drops = append(drops, "cached_content_unsupported")
	}

	return out, drops, nil
}

// translateContent converts a single Gemini content object into one or more
// OpenAI messages. A "model" turn with functionCall parts yields an assistant
// message; functionResponse parts in a "user" turn each become role:"tool"
// messages.
func translateContent(
	content geminiContent,
	callIDsByName map[string][]string,
	callCursor map[string]int,
	respCursor map[string]int,
) ([]models.Message, []string) {
	var drops []string

	// Map Gemini role to OpenAI role.
	openAIRole := "user"
	if content.Role == "model" {
		openAIRole = "assistant"
	}

	// Separate parts by type.
	var textParts []string
	var toolCalls []models.ToolCall
	var toolMsgs []models.Message // role:"tool" messages from functionResponse parts

	for _, part := range content.Parts {
		switch {
		case part.Text != "":
			textParts = append(textParts, part.Text)

		case part.InlineData != nil:
			// → image_url data URI. OpenAI only accepts image_url parts on
			// user messages; if this part is on a "model" (assistant) turn,
			// drop it with a flag rather than leaking a literal sentinel into
			// the assistant text (OpenAI would either reject or silently
			// stringify the sentinel as text).
			if openAIRole != "user" {
				drops = append(drops, "inline_image_on_non_user_turn")
				continue
			}
			dataURL := fmt.Sprintf("data:%s;base64,%s", part.InlineData.MimeType, part.InlineData.Data)
			// Accumulate via the sentinel; the multipart content rebuild below
			// parses sentinels and emits image_url parts.
			textParts = append(textParts, "<<<image:"+dataURL+">>>")

		case part.FileData != nil:
			mime := part.FileData.MimeType
			if !isImageMimeType(mime) {
				drops = append(drops, "non_image_filedata_unsupported")
				continue
			}
			if openAIRole != "user" {
				drops = append(drops, "file_image_on_non_user_turn")
				continue
			}
			textParts = append(textParts, "<<<image:"+part.FileData.FileURI+">>>")

		case part.FunctionCall != nil:
			name := part.FunctionCall.Name
			id := nextCallID(callIDsByName, callCursor, name)
			args := string(part.FunctionCall.Args)
			if args == "" || args == "null" {
				args = "{}"
			}
			toolCalls = append(toolCalls, models.ToolCall{
				ID:   id,
				Type: "function",
				Function: models.FunctionCall{
					Name:      name,
					Arguments: args,
				},
			})

		case part.FunctionResponse != nil:
			id := nextCallID(callIDsByName, respCursor, part.FunctionResponse.Name)
			respStr := responseToString(part.FunctionResponse.Response)
			contentBytes, _ := json.Marshal(respStr)
			toolMsgs = append(toolMsgs, models.Message{
				Role:       "tool",
				ToolCallID: id,
				Name:       part.FunctionResponse.Name,
				Content:    json.RawMessage(contentBytes),
			})
		}
	}

	// Build the primary message for this content turn.
	// Check for sentinel image parts and build multipart content if needed.
	var primaryMsg *models.Message

	hasImages := false
	for _, s := range textParts {
		if strings.HasPrefix(s, "<<<image:") {
			hasImages = true
			break
		}
	}

	if len(toolCalls) > 0 || len(textParts) > 0 {
		msg := models.Message{Role: openAIRole}

		if hasImages {
			// Build multipart content array.
			var contentParts []map[string]interface{}
			for _, s := range textParts {
				if strings.HasPrefix(s, "<<<image:") {
					rawURL := strings.TrimPrefix(strings.TrimSuffix(s, ">>>"), "<<<image:")
					contentParts = append(contentParts, map[string]interface{}{
						"type":      "image_url",
						"image_url": map[string]string{"url": rawURL},
					})
				} else {
					contentParts = append(contentParts, map[string]interface{}{
						"type": "text",
						"text": s,
					})
				}
			}
			contentBytes, _ := json.Marshal(contentParts)
			msg.Content = json.RawMessage(contentBytes)
		} else if len(textParts) > 0 {
			combined := strings.Join(textParts, "")
			contentBytes, _ := json.Marshal(combined)
			msg.Content = json.RawMessage(contentBytes)
		}

		if len(toolCalls) > 0 {
			msg.ToolCalls = toolCalls
		}
		primaryMsg = &msg
	}

	var result []models.Message
	// functionResponse parts → prepend role:"tool" messages (they occur before
	// whatever else is in this turn, which in Gemini is always a "user" turn
	// containing function results).
	result = append(result, toolMsgs...)
	if primaryMsg != nil && (len(primaryMsg.ToolCalls) > 0 || primaryMsg.Content != nil) {
		result = append(result, *primaryMsg)
	}

	return result, drops
}

// ── helpers ──────────────────────────────────────────────────────────────────

// newCallID generates a stable random call ID in the format call_{hex16}.
func newCallID() string {
	b := make([]byte, 8)
	_, _ = rand.Read(b)
	return "call_" + hex.EncodeToString(b)
}

// nextCallID returns the next unclaimed call ID for the given function name,
// advancing the cursor. Falls back to a fresh ID if the pre-computed list is
// exhausted (defensive: a functionResponse without a matching functionCall).
func nextCallID(callIDsByName map[string][]string, cursor map[string]int, name string) string {
	idx := cursor[name]
	if ids, ok := callIDsByName[name]; ok && idx < len(ids) {
		cursor[name] = idx + 1
		return ids[idx]
	}
	return newCallID()
}

// flattenPartsText concatenates the Text fields of all parts.
func flattenPartsText(parts []geminiPart) string {
	var sb strings.Builder
	for _, p := range parts {
		sb.WriteString(p.Text)
	}
	return sb.String()
}

// responseToString converts a Gemini functionResponse.response to a string.
// If it's already a plain JSON string, unwrap it. Otherwise, JSON-encode it.
func responseToString(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		return s
	}
	return string(raw)
}

// isImageMimeType returns true for image/* MIME types.
func isImageMimeType(mime string) bool {
	return strings.HasPrefix(strings.ToLower(mime), "image/")
}
