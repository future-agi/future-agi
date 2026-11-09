package models

import (
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
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
	// Metadata is the error's machine-readable detail, omitted when nil.
	Metadata any `json:"metadata,omitempty"`
}

// APIError is an error that carries HTTP status and OpenAI error details.
type APIError struct {
	origin            ErrorOrigin    // constructor-set, never serialized
	originText        string         // binds a gateway grant to its constructed text
	publicationOwner  CredentialType // source-bound, never serialized
	clientPublication string         // integrity-bound completed publication, never serialized
	Status            int
	Type              string
	Code              string
	Message           string
	Param             *string
	// Metadata is written as error.metadata: the PaymentMeta of a payment
	// or limit refusal (error-402-v1).
	Metadata any
	// RetryAfterSec, when positive, is sent as the Retry-After header.
	RetryAfterSec int
	// UpstreamStatus is the HTTP status the error was made with, when that
	// is set: the openai and azure adapters put the provider's own status
	// here when they parse its JSON error (parseProviderError), and
	// ErrUpstreamProvider puts here the status it is given. That is the
	// provider's status on an adapter's raw-body and unreadable-response
	// paths and on the capability core's vendor 4xx, but a fixed 502 on
	// some of the gateway's own failures (the provider returned no stream
	// or closed it before any chunk, a provider or failover target is not
	// found, a speech dial got no answer, a gemini speech answer has no
	// audio the gateway can decode). It is 0 when the provider
	// never answered (ErrUpstreamProvider(0, ...)), on the gateway's other
	// errors, and also on the errors the anthropic, gemini, bedrock and
	// cohere adapters parse from a provider's JSON body and on the speech
	// relay's handshake refusals: those carry the provider's answer only in
	// Code (provider_<status>, or the provider's error type or RPC status),
	// from which routing's upstreamStatusOf reads a status when this is 0.
	// Status is what the client gets (an upstream 5xx is a 502); retry,
	// failover and circuit breaker status lists match either one. Never
	// written to the client.
	UpstreamStatus int
	// ProviderStatus is the HTTP status of the provider's own error answer
	// that an adapter parsed into this error (the adapters' parse*Error);
	// 0 on every other error. It marks the error as the provider's, so the
	// answer the client gets can say which provider failed (the server's
	// providerFailure); retry, failover and classification never read it.
	// Never written to the client.
	ProviderStatus int
	// Provider is the ID of the provider whose answer this error is, set
	// where the gateway called that provider; "" when unknown. Never written
	// to the client.
	Provider string
	// ProviderRejected is set only by an adapter reading a real provider error
	// response/envelope. UpstreamStatus alone also includes synthesized errors.
	ProviderRejected bool `json:"-"`
	// BillingEvidence is internal upstream evidence, not client error metadata.
	BillingEvidence *ProviderBillingEvidence `json:"-"`
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

func newOriginAPIError(origin ErrorOrigin, status int, errType, code, message string) *APIError {
	e := newAPIError(status, errType, code, message)
	e.origin = origin
	if origin == OriginGateway {
		e.originText = e.Error()
	}
	return e
}

// Origin grants the gateway exception only to unchanged text from a gateway
// constructor. APIError's public diagnostic fields remain mutable for legacy
// callers; replacing/embedding provider text cannot inherit that exception.
// The equality check verifies construction integrity, never message meaning.
func (e *APIError) Origin() ErrorOrigin {
	if e == nil {
		return ""
	}
	if e.origin == OriginGateway && e.originText != e.Error() {
		return ""
	}
	return e.origin
}

func ErrBadRequest(code, message string) *APIError {
	return newAPIError(http.StatusBadRequest, ErrTypeInvalidRequest, code, message)
}

// ErrUnauthorized is a gateway authentication refusal, never a provider's
// authentication response. Upstream diagnostics use ErrUpstreamProvider.
func ErrUnauthorized(message string) *APIError {
	return newOriginAPIError(OriginGateway, http.StatusUnauthorized, ErrTypeAuthentication, "invalid_api_key", message)
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

// ErrTooManyRequests is a gateway rate-limit refusal. A provider's 429 uses
// ErrUpstreamProvider instead, even when its text looks like a gateway refusal.
func ErrTooManyRequests(message string) *APIError {
	return newOriginAPIError(OriginGateway, http.StatusTooManyRequests, ErrTypeRateLimit, "rate_limit_exceeded", message)
}

// ErrGatewayRefusal is for gateway-authored policy/admission explanations only.
// Never pass provider bodies, transport diagnostics, credentials, or err.Error().
// Callers must supply the final code and message: later text edits lose origin.
func ErrGatewayRefusal(status int, errType, code, message string) *APIError {
	return newOriginAPIError(OriginGateway, status, errType, code, message)
}

// ErrGatewayUpstream reports a gateway-detected failure (such as an empty
// stream), not a provider diagnostic. It preserves the established 502 routing
// shape without granting gateway origin to an existing upstream error.
func ErrGatewayUpstream(message string) *APIError {
	e := newOriginAPIError(OriginGateway, http.StatusBadGateway, ErrTypeUpstream, "provider_502", message)
	e.UpstreamStatus = http.StatusBadGateway
	return e
}

func ErrServiceUnavailable(message string) *APIError {
	return newAPIError(http.StatusServiceUnavailable, ErrTypeServer, "all_providers_unavailable", message)
}

// ErrInternal accepts wrapped/embedded provider diagnostics at gateway call
// sites. Conservatively retain provider origin, even for a local-only message.
func ErrInternal(message string) *APIError {
	return newOriginAPIError(OriginUpstream, http.StatusInternalServerError, ErrTypeServer, "internal_error", message)
}

// ErrUpstreamProvider is a provider's failure the gateway answers as a 502:
// status is the provider's own HTTP status (0 when it never answered), kept
// in the code ("provider_<status>") and as UpstreamStatus. Some of the
// gateway's own failures pass a fixed 502 instead (see UpstreamStatus).
func ErrUpstreamProvider(status int, message string) *APIError {
	e := newOriginAPIError(OriginUpstream, http.StatusBadGateway, ErrTypeUpstream, fmt.Sprintf("provider_%d", status), message)
	e.UpstreamStatus = status
	return e
}

func ErrGatewayTimeout(message string) *APIError {
	return newOriginAPIError(OriginUpstream, http.StatusGatewayTimeout, ErrTypeGatewayTimeout, "gateway_timeout", message)
}

func ErrGuardrailBlocked(code, message string) *APIError {
	return newAPIError(http.StatusForbidden, ErrTypeGuardrail, code, message)
}

// CodeProviderBusy is the code of the 503 the gateway answers when every
// upstream slot of a provider entry (its max_concurrent) stayed in use for the
// entry's queue wait (queue_timeout). Nothing was sent upstream.
const CodeProviderBusy = "provider_busy"

// ProviderBusyRetryAfterSec is the Retry-After of a provider_busy refusal: a
// slot frees as soon as any call on the entry ends, so the wait is short.
const ProviderBusyRetryAfterSec = 1

// ErrProviderBusy is the refusal of a call that found no free upstream slot on
// provider entry within its queue wait. The message names the entry and
// carries no request or configuration value; the code is repeated in it so
// the Anthropic and Google dialects, whose bodies have no code field, still
// show it.
func ErrProviderBusy(entry string) *APIError {
	e := newAPIError(http.StatusServiceUnavailable, ErrTypeServer, CodeProviderBusy,
		fmt.Sprintf("%s: provider entry %q has no free upstream slot; the request was not sent upstream, retry after the Retry-After interval", CodeProviderBusy, entry))
	e.RetryAfterSec = ProviderBusyRetryAfterSec
	return e
}

// IsProviderBusy reports whether err is a provider_busy refusal the gateway
// made (ErrProviderBusy): a call that was never dispatched.
func IsProviderBusy(err error) bool {
	var apiErr *APIError
	return errors.As(err, &apiErr) && apiErr.Code == CodeProviderBusy &&
		apiErr.Status == http.StatusServiceUnavailable && apiErr.UpstreamStatus == 0
}

// BearerChallenge is the WWW-Authenticate challenge on every 401 the gateway
// writes. RFC 7235 requires a challenge on a 401, and MCP clients read it to
// learn that the endpoint wants a bearer token.
const BearerChallenge = `Bearer realm="agentcc-gateway"`

// WriteError writes an OpenAI-compatible error JSON response. A 401 carries
// BearerChallenge unless the caller set a challenge of its own.
//
// The message is redacted here as well as at construction. Most of this
// package's errors are built by the constructors above, but many sites build
// an APIError directly — a provider parsing an upstream error body, for one —
// and those never pass through newAPIError. Redacting at the boundary every
// response leaves through needs no site to remember.
func WriteError(w http.ResponseWriter, err *APIError) {
	resp := ErrorResponse{
		Error: ErrorDetail{
			Message:  RedactCredentials(err.Message),
			Type:     err.Type,
			Code:     err.Code,
			Param:    err.Param,
			Metadata: err.Metadata,
		},
	}
	SetRetryAfter(w, err)
	w.Header().Set("Content-Type", "application/json")
	if err.Status == http.StatusUnauthorized && w.Header().Get("WWW-Authenticate") == "" {
		w.Header().Set("WWW-Authenticate", BearerChallenge)
	}
	w.WriteHeader(err.Status)
	json.NewEncoder(w).Encode(resp)
}

// SetRetryAfter sets err's Retry-After header when it has one. Every writer
// of an APIError calls it before writing the status, whatever the dialect of
// the body.
func SetRetryAfter(w http.ResponseWriter, err *APIError) {
	if err != nil && err.RetryAfterSec > 0 {
		w.Header().Set("Retry-After", strconv.Itoa(err.RetryAfterSec))
	}
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
