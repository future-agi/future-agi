package models

import (
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
)

// Error types matching OpenAI's error format.
const (
	ErrTypeInvalidRequest = "invalid_request_error"
	ErrTypeAuthentication = "authentication_error"
	ErrTypePermission     = "permission_error"
	ErrTypeNotFound       = "not_found"
	ErrTypeTimeout        = "timeout_error"
	ErrTypeRateLimit      = "rate_limit_error"
	ErrTypeGuardrail      = "guardrail_error"
	ErrTypeServer         = "server_error"
	ErrTypeUpstream       = "upstream_error"
	ErrTypeGatewayTimeout = "gateway_timeout"
)

// ErrorResponse is the OpenAI-compatible error response body.
type ErrorResponse struct {
	Error ErrorDetail `json:"error"`
}

type ErrorDetail struct {
	Message  string                 `json:"message"`
	Type     string                 `json:"type"`
	Param    *string                `json:"param"`
	Code     string                 `json:"code"`
	Metadata map[string]interface{} `json:"metadata,omitempty"`
}

// APIError is an error that carries HTTP status and OpenAI error details.
type APIError struct {
	Status   int
	Type     string
	Code     string
	Message  string
	Param    *string
	Metadata map[string]interface{}
}

func (e *APIError) Error() string {
	return fmt.Sprintf("%s: %s", e.Code, e.Message)
}

// Common error constructors.

func ErrBadRequest(code, message string) *APIError {
	return &APIError{Status: http.StatusBadRequest, Type: ErrTypeInvalidRequest, Code: code, Message: message}
}

func ErrUnauthorized(message string) *APIError {
	return &APIError{Status: http.StatusUnauthorized, Type: ErrTypeAuthentication, Code: "invalid_api_key", Message: message}
}

func ErrForbidden(message string) *APIError {
	return &APIError{Status: http.StatusForbidden, Type: ErrTypePermission, Code: "permission_denied", Message: message}
}

func ErrNotFound(code, message string) *APIError {
	return &APIError{Status: http.StatusNotFound, Type: ErrTypeNotFound, Code: code, Message: message}
}

func ErrRequestTimeout(message string) *APIError {
	return &APIError{Status: http.StatusRequestTimeout, Type: ErrTypeTimeout, Code: "request_timeout", Message: message}
}

func ErrTooManyRequests(message string) *APIError {
	return &APIError{Status: http.StatusTooManyRequests, Type: ErrTypeRateLimit, Code: "rate_limit_exceeded", Message: message}
}

func ErrServiceUnavailable(message string) *APIError {
	return &APIError{Status: http.StatusServiceUnavailable, Type: ErrTypeServer, Code: "all_providers_unavailable", Message: message}
}

func ErrInternal(message string) *APIError {
	return &APIError{Status: http.StatusInternalServerError, Type: ErrTypeServer, Code: "internal_error", Message: message}
}

func ErrUpstreamProvider(status int, message string) *APIError {
	return &APIError{Status: http.StatusBadGateway, Type: ErrTypeUpstream, Code: fmt.Sprintf("provider_%d", status), Message: message}
}

func ErrGatewayTimeout(message string) *APIError {
	return &APIError{Status: http.StatusGatewayTimeout, Type: ErrTypeGatewayTimeout, Code: "gateway_timeout", Message: message}
}

func ErrGuardrailBlocked(code, message string, attempts int) *APIError {
	return &APIError{
		Status:  http.StatusForbidden,
		Type:    ErrTypeGuardrail,
		Code:    code,
		Message: message,
		Metadata: map[string]interface{}{
			"reflexion_attempts": attempts,
		},
	}
}

// WriteError writes an OpenAI-compatible error JSON response.
func WriteError(w http.ResponseWriter, err *APIError) {
	resp := ErrorResponse{
		Error: ErrorDetail{
			Message:  err.Message,
			Type:     err.Type,
			Code:     err.Code,
			Param:    err.Param,
			Metadata: err.Metadata,
		},
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(err.Status)
	json.NewEncoder(w).Encode(resp)
}

// WriteErrorFromError writes an error response from a generic error.
// Uses errors.As for interface satisfaction and sanitizes non-APIError types
// to avoid leaking internal Go error details to clients.
func WriteErrorFromError(w http.ResponseWriter, err error) {
	var apiErr *APIError
	if errors.As(err, &apiErr) {
		WriteError(w, apiErr)
		return
	}
	// Log the real error server-side, return a generic message to the client.
	slog.Warn("internal error sanitized for client", "error", err)
	WriteError(w, ErrInternal("internal server error"))
}
