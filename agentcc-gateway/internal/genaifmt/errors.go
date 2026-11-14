package genaifmt

import (
	"encoding/json"
	"fmt"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// WriteError writes an error response in Google GenAI format.
//
// Redacted like the OpenAI-shaped writer: a dialect response is the same
// boundary, and an upstream message can carry a credential in a URL.
func WriteError(w http.ResponseWriter, code int, status string, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(newErrorBody(code, status, message))
}

// errorBody is a Google error, as a response body and as a stream chunk.
type errorBody struct {
	Error struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
		Status  string `json:"status"`
	} `json:"error"`
}

func newErrorBody(code int, status, message string) errorBody {
	var body errorBody
	body.Error.Code = code
	body.Error.Message = models.RedactCredentials(message)
	body.Error.Status = status
	return body
}

// StreamErrorEvent is an error as a GenAI stream chunk. Once a stream has
// sent its 200 the status can no longer change, so this chunk is the only way
// left to tell the client the stream failed; the Google GenAI SDK raises on a
// chunk that is an error object.
func StreamErrorEvent(apiErr *models.APIError) []byte {
	data, _ := json.Marshal(newErrorBody(apiErr.Status, MapStatusToGoogleStatus(apiErr.Status), apiErr.Message))
	return fmt.Appendf(nil, "data: %s\n\n", data)
}

// MapStatusToGoogleStatus maps HTTP status codes to Google error status strings.
func MapStatusToGoogleStatus(statusCode int) string {
	switch statusCode {
	case 400, 413, 446:
		return "INVALID_ARGUMENT"
	case 401:
		return "UNAUTHENTICATED"
	case 403:
		return "PERMISSION_DENIED"
	case 404:
		return "NOT_FOUND"
	case 408, 504:
		return "DEADLINE_EXCEEDED"
	case 429:
		return "RESOURCE_EXHAUSTED"
	case 503:
		return "UNAVAILABLE"
	default:
		return "INTERNAL"
	}
}

// WriteAPIError converts a gateway APIError to Google error format.
func WriteAPIError(w http.ResponseWriter, apiErr *models.APIError) {
	status := MapStatusToGoogleStatus(apiErr.Status)
	WriteError(w, apiErr.Status, status, apiErr.Message)
}
