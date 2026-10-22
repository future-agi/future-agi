package models

import (
	"encoding/json"
	"net/http"
	"time"
)

// ErrTypePaymentRequired is the error type of a 402 refusal.
const ErrTypePaymentRequired = "payment_required"

// Limit sources: which limit refused a request (error-402-v1
// metadata.limit_source).
const (
	LimitSourceCredits     = "agentcc_credits"
	LimitSourceInFlight    = "agentcc_in_flight"
	LimitSourceBudget      = "agentcc_budget"
	LimitSourceAgentBudget = "agentcc_agent_budget"
	LimitSourceCallCap     = "agentcc_call_cap"
	LimitSourceKeyLimit    = "agentcc_key_limit"
	LimitSourceSpendGuard  = "agentcc_spend_guard"
	LimitSourceRunStop     = "agentcc_run_stop"
)

// Billing-unavailable reasons (metadata.reason of the 503
// managed_billing_unavailable).
const (
	ReasonRedisUnavailable    = "redis_unavailable"
	ReasonAccountStateMissing = "account_state_missing"
)

// ScopeOrg is the scope of an org-wide limit: credits, the spend guard, the
// budget pause.
const ScopeOrg = "org"

// ScopeKey, ScopeKeyParallel, ScopeTeam, ScopeUser and ScopeRun name the
// other limit scopes (metadata.scope).
func ScopeKey(keyID string) string         { return "key:" + keyID }
func ScopeKeyParallel(keyID string) string { return "key:" + keyID + ":parallel" }
func ScopeTeam(teamID string) string       { return "team:" + teamID }
func ScopeUser(userID string) string       { return "user:" + userID }
func ScopeRun(budgetScope string) string   { return "run:" + budgetScope }

// PaymentMeta is the metadata of a payment or limit refusal (error-402-v1). A
// refusal on a limit names its LimitSource, Scope and
// ResetsAt; ResetsAt is nil when the limit lifts only on a top-up, a grant
// or a new run, and is then written as null. RetryAfterSec is sent as the
// Retry-After header, never in the body. Reason is the 503
// managed_billing_unavailable's.
type PaymentMeta struct {
	LimitSource   string
	RemedyHint    string
	RequiredUSD   *float64
	AvailableUSD  *float64
	RetryAfterSec int
	Scope         string
	ResetsAt      *time.Time
	Reason        string
}

// paymentMetaJSON is PaymentMeta's body shape.
type paymentMetaJSON struct {
	LimitSource  string          `json:"limit_source,omitempty"`
	RemedyHint   string          `json:"remedy_hint,omitempty"`
	RequiredUSD  *float64        `json:"required_usd,omitempty"`
	AvailableUSD *float64        `json:"available_usd,omitempty"`
	Scope        string          `json:"scope,omitempty"`
	ResetsAt     json.RawMessage `json:"resets_at,omitempty"`
	Reason       string          `json:"reason,omitempty"`
}

// MarshalJSON writes the error-402-v1 metadata object. A limit refusal
// (LimitSource set) always carries resets_at: an RFC 3339 time, or null.
func (m PaymentMeta) MarshalJSON() ([]byte, error) {
	out := paymentMetaJSON{
		LimitSource:  m.LimitSource,
		RemedyHint:   m.RemedyHint,
		RequiredUSD:  m.RequiredUSD,
		AvailableUSD: m.AvailableUSD,
		Scope:        m.Scope,
		Reason:       m.Reason,
	}
	switch {
	case m.ResetsAt != nil:
		b, err := json.Marshal(m.ResetsAt.UTC().Format(time.RFC3339))
		if err != nil {
			return nil, err
		}
		out.ResetsAt = b
	case m.LimitSource != "":
		out.ResetsAt = json.RawMessage("null")
	}
	return json.Marshal(out)
}

// ErrPaymentRequired is a 402 refusal with its metadata (error-402-v1): the
// body carries meta as error.metadata, and Retry-After is sent when
// meta.RetryAfterSec is set.
func ErrPaymentRequired(errType, code, msg string, meta PaymentMeta) *APIError {
	return ErrLimit(http.StatusPaymentRequired, errType, code, msg, meta)
}

// ErrLimit is a refusal on a limit with another status, carrying the same
// metadata: the 429 managed_spend_velocity and the 503 limit refusals.
func ErrLimit(status int, errType, code, msg string, meta PaymentMeta) *APIError {
	return &APIError{Status: status, Type: errType, Code: code, Message: msg, Metadata: meta, RetryAfterSec: meta.RetryAfterSec}
}

// ErrIdempotencyConflict is the 409 for an Idempotency-Key whose first
// request is still running; the caller retries after a second.
func ErrIdempotencyConflict(message string) *APIError {
	return &APIError{Status: http.StatusConflict, Type: ErrTypeInvalidRequest, Code: "idempotency_in_progress", Message: message, RetryAfterSec: 1}
}

// ErrIdempotencyReused is the 422 for an Idempotency-Key reused with a
// different body.
func ErrIdempotencyReused(message string) *APIError {
	return &APIError{Status: http.StatusUnprocessableEntity, Type: ErrTypeInvalidRequest, Code: "idempotency_key_reused", Message: message}
}

// ErrRoutingField is the 400 for a caller routing field the gateway refuses
// (its codes: invalid_provider_preference, routing_field_not_yet_supported,
// unsupported_routing_field, unsupported_field_for_managed,
// extra_body_conflict, invalid_extra_body), naming the field in param.
func ErrRoutingField(code, param string) *APIError {
	p := param
	return &APIError{Status: http.StatusBadRequest, Type: ErrTypeInvalidRequest, Code: code,
		Message: "request field " + param + " cannot be used here (" + code + ")", Param: &p}
}
