package genaifmt

import (
	"encoding/json"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// WriteError writes an error response in Google GenAI format.
func WriteError(w http.ResponseWriter, code int, status string, message string) {
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

// WriteAPIError converts a gateway APIError to Google error format.
func WriteAPIError(w http.ResponseWriter, apiErr *models.APIError) {
	status := MapStatusToGoogleStatus(apiErr.Status)
	WriteError(w, apiErr.Status, status, apiErr.Message)
}
