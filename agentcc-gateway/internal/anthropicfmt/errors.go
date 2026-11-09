package anthropicfmt

import (
	"encoding/json"
	"net/http"

	contract "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// WriteError writes an error response in Anthropic format.
// Redacted like the other dialect writers: an upstream message can carry a
// credential in a URL, and this is a boundary a response leaves through.
func WriteError(w http.ResponseWriter, statusCode int, errorType string, message string) {
	message = models.RedactCredentials(message)
	resp := struct {
		Type  string `json:"type"`
		Error struct {
			Type    string `json:"type"`
			Message string `json:"message"`
		} `json:"error"`
	}{
		Type: "error",
	}
	resp.Error.Type = errorType
	resp.Error.Message = message

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)
	json.NewEncoder(w).Encode(resp)
}

// MapStatusToErrorType maps HTTP status codes to Anthropic error type strings.
func MapStatusToErrorType(statusCode int) string {
	switch statusCode {
	case 400, 413, 446:
		return "invalid_request_error"
	case 401:
		return "authentication_error"
	case 402:
		// Anthropic's documented type for a 402 (billing or payment).
		return "billing_error"
	case 403:
		return "permission_error"
	case 404:
		return "not_found_error"
	case 429:
		return "rate_limit_error"
	case 503, 529:
		return "overloaded_error"
	default:
		return "api_error"
	}
}

// WriteAPIError converts a gateway APIError to Anthropic error format. Its
// Retry-After is sent as models.WriteError sends it.
func WriteAPIError(w http.ResponseWriter, apiErr *models.APIError) {
	models.SetRetryAfter(w, apiErr)
	errType := MapStatusToErrorType(apiErr.Status)
	meta, _ := apiErr.Metadata.(map[string]any)
	if _, attributed := meta["provider"]; attributed {
		// Keep Anthropic's envelope and type while exposing the gateway's
		// stable provider code and the original upstream evidence.
		resp := struct {
			Type  string `json:"type"`
			Error struct {
				Type     string `json:"type"`
				Message  string `json:"message"`
				Code     string `json:"code"`
				Metadata any    `json:"metadata"`
			} `json:"error"`
		}{Type: "error"}
		resp.Error.Type, resp.Error.Message = errType, models.RedactCredentials(apiErr.Message)
		resp.Error.Code, resp.Error.Metadata = apiErr.Code, apiErr.Metadata
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(apiErr.Status)
		json.NewEncoder(w).Encode(resp)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(apiErr.Status)
	json.NewEncoder(w).Encode(apiErrorResponse(apiErr))
}

type errorResponse struct {
	Type  string `json:"type"`
	Error struct {
		Type     string `json:"type"`
		Message  string `json:"message"`
		Code     string `json:"code,omitempty"`
		Metadata any    `json:"metadata,omitempty"`
	} `json:"error"`
}

func apiErrorResponse(apiErr *models.APIError) errorResponse {
	resp := errorResponse{Type: "error"}
	resp.Error.Type = MapStatusToErrorType(apiErr.Status)
	resp.Error.Message = models.RedactCredentials(apiErr.Message)
	// Only admission-cap refusals extend the established Messages shape.
	// Legacy payment errors (including credits refusals) remain type/message.
	if metadata, ok := apiErr.Metadata.(contract.AdmissionCapRefusalMetadata); ok {
		resp.Error.Code, resp.Error.Metadata = apiErr.Code, metadata
	}
	return resp
}

// StreamErrorEvent is apiErr as an Anthropic stream's error event, the frame
// Anthropic sends when a stream fails:
//
//	event: error
//	data: {"type":"error","error":{"type":...,"message":...}}
func StreamErrorEvent(apiErr *models.APIError) []byte {
	data, _ := json.Marshal(apiErrorResponse(apiErr))
	return []byte("event: error\ndata: " + string(data) + "\n\n")
}
