package anthropicfmt

import (
	"encoding/json"
	"fmt"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// timeoutErrorType is Anthropic's error type for a request that timed out,
// the type its SDKs give a 504.
const timeoutErrorType = "timeout_error"

// errorBody is an Anthropic error, as a response body and as a stream event.
// Code is the gateway's own error code, the one the OpenAI-format routes send;
// it is left out when there is none.
type errorBody struct {
	Type  string `json:"type"`
	Error struct {
		Type    string `json:"type"`
		Message string `json:"message"`
		Code    string `json:"code,omitempty"`
	} `json:"error"`
}

// newErrorBody redacts like the other dialect writers: an upstream message can
// carry a credential in a URL, and this is a boundary a response leaves through.
func newErrorBody(errorType, message, code string) errorBody {
	body := errorBody{Type: "error"}
	body.Error.Type = errorType
	body.Error.Message = models.RedactCredentials(message)
	body.Error.Code = code
	return body
}

// WriteError writes an error response in Anthropic format.
func WriteError(w http.ResponseWriter, statusCode int, errorType string, message string) {
	writeErrorBody(w, statusCode, newErrorBody(errorType, message, ""))
}

func writeErrorBody(w http.ResponseWriter, statusCode int, body errorBody) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)
	json.NewEncoder(w).Encode(body)
}

// StreamErrorEvent is an error as an Anthropic stream event. Once a stream has
// sent its 200 the status can no longer change, so this event is the only way
// left to tell the client the stream failed; the SDKs raise on it.
func StreamErrorEvent(apiErr *models.APIError) []byte {
	data, _ := json.Marshal(newErrorBody(ErrorType(apiErr), apiErr.Message, apiErr.Code))
	return fmt.Appendf(nil, "event: error\ndata: %s\n\n", data)
}

// ErrorType is the Anthropic error type for a gateway error. The gateway's
// own timeout, a 504 of type gateway_timeout, is timeout_error; anything else
// is typed by its status. So a 504 an upstream passed on stays api_error, and
// so does an upstream's own gateway_timeout, which reaches here as a 502.
func ErrorType(apiErr *models.APIError) string {
	if apiErr.Type == models.ErrTypeGatewayTimeout && apiErr.Status == http.StatusGatewayTimeout {
		return timeoutErrorType
	}
	return MapStatusToErrorType(apiErr.Status)
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
	writeErrorBody(w, apiErr.Status, newErrorBody(ErrorType(apiErr), apiErr.Message, apiErr.Code))
}
