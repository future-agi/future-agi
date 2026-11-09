package anthropicfmt

import (
	"encoding/json"
	"fmt"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// errorBody is an Anthropic error, as a response body and as a stream event.
type errorBody struct {
	Type  string `json:"type"`
	Error struct {
		Type    string `json:"type"`
		Message string `json:"message"`
	} `json:"error"`
}

// newErrorBody redacts like the other dialect writers: an upstream message can
// carry a credential in a URL, and this is a boundary a response leaves through.
func newErrorBody(errorType, message string) errorBody {
	body := errorBody{Type: "error"}
	body.Error.Type = errorType
	body.Error.Message = models.RedactCredentials(message)
	return body
}

// WriteError writes an error response in Anthropic format.
func WriteError(w http.ResponseWriter, statusCode int, errorType string, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)
	json.NewEncoder(w).Encode(newErrorBody(errorType, message))
}

// StreamErrorEvent is an error as an Anthropic stream event. Once a stream has
// sent its 200 the status can no longer change, so this event is the only way
// left to tell the client the stream failed; the SDKs raise on it.
func StreamErrorEvent(apiErr *models.APIError) []byte {
	data, _ := json.Marshal(newErrorBody(MapStatusToErrorType(apiErr.Status), apiErr.Message))
	return fmt.Appendf(nil, "event: error\ndata: %s\n\n", data)
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
