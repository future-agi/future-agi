package gemini

import (
	"encoding/json"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ResponseFromCanonical converts an OpenAI ChatCompletionResponse back to
// the Gemini GenerateContentResponse wire format.
func (t *Translator) ResponseFromCanonical(resp *models.ChatCompletionResponse) ([]byte, error) {
	gr := geminiResponse{}

	// modelVersion: use the model string; strip leading "publishers/google/models/"
	// prefix if present, since Gemini clients expect the bare model ID.
	gr.ModelVersion = strings.TrimPrefix(resp.Model, "publishers/google/models/")

	if len(resp.Choices) > 0 {
		choice := resp.Choices[0]
		msg := choice.Message

		var parts []geminiPart

		// Text content → single text part at the front.
		if len(msg.Content) > 0 {
			var textStr string
			if err := json.Unmarshal(msg.Content, &textStr); err == nil && textStr != "" {
				parts = append(parts, geminiPart{Text: textStr})
			}
		}

		// tool_calls → functionCall parts (after any text).
		for _, tc := range msg.ToolCalls {
			var args json.RawMessage
			if tc.Function.Arguments != "" {
				// Validate / re-marshal the arguments so they're proper JSON.
				var parsed interface{}
				if err := json.Unmarshal([]byte(tc.Function.Arguments), &parsed); err == nil {
					args, _ = json.Marshal(parsed)
				} else {
					args = json.RawMessage(`{}`)
				}
			} else {
				args = json.RawMessage(`{}`)
			}
			parts = append(parts, geminiPart{
				FunctionCall: &geminiFunctionCall{
					Name: tc.Function.Name,
					Args: args,
				},
			})
		}

		if len(parts) == 0 {
			// Ensure at least one part so the response is well-formed.
			parts = append(parts, geminiPart{Text: ""})
		}

		candidate := geminiCandidate{
			Content: geminiContent{
				Role:  "model",
				Parts: parts,
			},
			FinishReason: mapFinishReasonToGemini(choice.FinishReason),
		}
		gr.Candidates = []geminiCandidate{candidate}
	}

	if resp.Usage != nil {
		gr.UsageMetadata = &geminiUsageMetadata{
			PromptTokenCount:     resp.Usage.PromptTokens,
			CandidatesTokenCount: resp.Usage.CompletionTokens,
			TotalTokenCount:      resp.Usage.TotalTokens,
		}
	}

	return json.Marshal(gr)
}

// mapFinishReasonToGemini reverses the OpenAI→Gemini mapping for finish_reason.
//
//	stop          → STOP
//	length        → MAX_TOKENS
//	tool_calls    → STOP  (Gemini has no TOOL_USE; clients check for functionCall parts)
//	content_filter → SAFETY
func mapFinishReasonToGemini(reason string) string {
	switch reason {
	case "stop":
		return "STOP"
	case "length":
		return "MAX_TOKENS"
	case "tool_calls":
		return "STOP"
	case "content_filter":
		return "SAFETY"
	default:
		return "STOP"
	}
}

// ErrorFromCanonical converts a gateway APIError into the Gemini error body.
//
// Gemini error shape: {"error":{"code":<http>,"message":"...","status":"..."}}
func (t *Translator) ErrorFromCanonical(err *models.APIError) (statusCode int, body []byte, contentType string) {
	statusCode = err.Status
	if statusCode == 0 {
		statusCode = http.StatusInternalServerError
	}

	resp := geminiErrorResponse{
		Error: geminiErrorDetail{
			Code:    statusCode,
			Message: err.Message,
			Status:  mapErrorTypeToGeminiStatus(err.Type),
		},
	}
	body, _ = json.Marshal(resp)
	contentType = "application/json"
	return statusCode, body, contentType
}

// mapErrorTypeToGeminiStatus converts gateway error type strings to Gemini
// status strings (mirroring the reverse of providers/gemini/translate.go's
// mapGeminiErrorType).
func mapErrorTypeToGeminiStatus(errType string) string {
	switch errType {
	case models.ErrTypeInvalidRequest:
		return "INVALID_ARGUMENT"
	case models.ErrTypeAuthentication:
		return "UNAUTHENTICATED"
	case models.ErrTypePermission:
		return "PERMISSION_DENIED"
	case models.ErrTypeNotFound:
		return "NOT_FOUND"
	case models.ErrTypeRateLimit:
		return "RESOURCE_EXHAUSTED"
	case models.ErrTypeServer:
		return "INTERNAL"
	case models.ErrTypeUpstream:
		return "INTERNAL"
	default:
		return "UNAVAILABLE"
	}
}
