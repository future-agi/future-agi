package anthropicfmt

import (
	"encoding/json"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// WriteError writes an error response in Anthropic format.
func WriteError(w http.ResponseWriter, statusCode int, errorType string, message string) {
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

// WriteAPIError converts a gateway APIError to Anthropic error format.
func WriteAPIError(w http.ResponseWriter, apiErr *models.APIError) {
	errType := MapStatusToErrorType(apiErr.Status)
	WriteError(w, apiErr.Status, errType, apiErr.Message)
}
