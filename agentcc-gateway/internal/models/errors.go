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
	Message string  `json:"message"`
	Type    string  `json:"type"`
	Param   *string `json:"param"`
	Code    string  `json:"code"`
}

// APIError is an error that carries HTTP status and OpenAI error details.
type APIError struct {
	Status  int
	Type    string
	Code    string
	Message string
	Param   *string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("%s: %s", e.Code, e.Message)
}

// Common error constructors.
//
// Every one goes through newAPIError, so no message reaches a caller, a
// request log or an operator's log with a credential still in it. See
// RedactCredentials.

func newAPIError(status int, errType, code, message string) *APIError {
	return &APIError{Status: status, Type: errType, Code: code, Message: RedactCredentials(message)}
}

func ErrBadRequest(code, message string) *APIError {
	return newAPIError(http.StatusBadRequest, ErrTypeInvalidRequest, code, message)
}

func ErrUnauthorized(message string) *APIError {
	return newAPIError(http.StatusUnauthorized, ErrTypeAuthentication, "invalid_api_key", message)
}

func ErrForbidden(message string) *APIError {
	return newAPIError(http.StatusForbidden, ErrTypePermission, "permission_denied", message)
}

func ErrNotFound(code, message string) *APIError {
	return newAPIError(http.StatusNotFound, ErrTypeNotFound, code, message)
}

func ErrRequestTimeout(message string) *APIError {
	return newAPIError(http.StatusRequestTimeout, ErrTypeTimeout, "request_timeout", message)
}

func ErrTooManyRequests(message string) *APIError {
	return newAPIError(http.StatusTooManyRequests, ErrTypeRateLimit, "rate_limit_exceeded", message)
}

func ErrServiceUnavailable(message string) *APIError {
	return newAPIError(http.StatusServiceUnavailable, ErrTypeServer, "all_providers_unavailable", message)
}

func ErrInternal(message string) *APIError {
	return newAPIError(http.StatusInternalServerError, ErrTypeServer, "internal_error", message)
}

func ErrUpstreamProvider(status int, message string) *APIError {
	return newAPIError(http.StatusBadGateway, ErrTypeUpstream, fmt.Sprintf("provider_%d", status), message)
}

func ErrGatewayTimeout(message string) *APIError {
	return newAPIError(http.StatusGatewayTimeout, ErrTypeGatewayTimeout, "gateway_timeout", message)
}

func ErrGuardrailBlocked(code, message string) *APIError {
	return newAPIError(http.StatusForbidden, ErrTypeGuardrail, code, message)
}

// WriteError writes an OpenAI-compatible error JSON response.
//
// The message is redacted here as well as at construction. Most of this
// package's errors are built by the constructors above, but 78 sites build an
// APIError directly — a provider parsing an upstream error body, for one — and
// those never pass through newAPIError. Redacting at the boundary every
// response leaves through needs no site to remember.
func WriteError(w http.ResponseWriter, err *APIError) {
	resp := ErrorResponse{
		Error: ErrorDetail{
			Message: RedactCredentials(err.Message),
			Type:    err.Type,
			Code:    err.Code,
			Param:   err.Param,
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
	slog.Warn("internal error sanitized for client", "error", RedactCredentials(err.Error()))
	WriteError(w, ErrInternal("internal server error"))
}
