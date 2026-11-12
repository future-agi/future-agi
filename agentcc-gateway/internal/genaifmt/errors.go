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
	message = models.RedactCredentials(message)
	resp := struct {
		Error struct {
			Code    int    `json:"code"`
			Message string `json:"message"`
			Status  string `json:"status"`
		} `json:"error"`
	}{}
	resp.Error.Code = code
	resp.Error.Message = message
	resp.Error.Status = status

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(resp)
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

// WriteAPIError converts a gateway APIError to Google error format. Its
// Retry-After is sent as models.WriteError sends it.
func WriteAPIError(w http.ResponseWriter, apiErr *models.APIError) {
	models.SetRetryAfter(w, apiErr)
	status := MapStatusToGoogleStatus(apiErr.Status)
	meta, _ := apiErr.Metadata.(map[string]any)
	if _, attributed := meta["provider"]; attributed {
		// Preserve native RPC semantics (e.g. FAILED_PRECONDITION versus
		// INVALID_ARGUMENT). A provider credential failure still describes a
		// gateway-side failure, not unauthenticated access to this gateway.
		if upstream, ok := meta["provider_type"].(string); ok && validRPCErrorStatus(upstream) && apiErr.Code != "provider_auth_failed" {
			status = upstream
		}
		// Google reserves error.code for the numeric HTTP status. Its
		// standard ErrorInfo detail carries the stable gateway reason.
		metadata := make(map[string]string, len(meta))
		for k, v := range meta {
			metadata[k] = models.RedactCredentials(fmt.Sprint(v))
		}
		resp := map[string]any{"error": map[string]any{
			"code": apiErr.Status, "message": models.RedactCredentials(apiErr.Message), "status": status,
			"details": []any{map[string]any{
				"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": apiErr.Code,
				"domain": "agentcc-gateway", "metadata": metadata,
			}},
		}}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(apiErr.Status)
		json.NewEncoder(w).Encode(resp)
		return
	}
	WriteError(w, apiErr.Status, status, apiErr.Message)
}

// validRPCErrorStatus accepts google.rpc error codes, not arbitrary provider
// strings (nor OK, which cannot describe an error response).
func validRPCErrorStatus(status string) bool {
	switch status {
	case "CANCELLED", "UNKNOWN", "INVALID_ARGUMENT", "DEADLINE_EXCEEDED",
		"NOT_FOUND", "ALREADY_EXISTS", "PERMISSION_DENIED", "RESOURCE_EXHAUSTED",
		"FAILED_PRECONDITION", "ABORTED", "OUT_OF_RANGE", "UNIMPLEMENTED",
		"INTERNAL", "UNAVAILABLE", "DATA_LOSS", "UNAUTHENTICATED":
		return true
	default:
		return false
	}
}

// StreamErrorEvent is apiErr as a GenAI stream (alt=sse) event: the error
// body a request would get, as one data frame.
func StreamErrorEvent(apiErr *models.APIError) []byte {
	resp := struct {
		Error struct {
			Code    int    `json:"code"`
			Message string `json:"message"`
			Status  string `json:"status"`
		} `json:"error"`
	}{}
	resp.Error.Code = apiErr.Status
	resp.Error.Message = apiErr.Message
	resp.Error.Status = MapStatusToGoogleStatus(apiErr.Status)
	data, _ := json.Marshal(resp)
	return []byte("data: " + string(data) + "\n\n")
}
