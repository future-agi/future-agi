package models

import (
	"strconv"
	"time"
)

// MaxAttempts is the most attempts one request records.
// RequestContext.AddAttempt drops any past it.
const MaxAttempts = 16

// AttemptKind is why an attempt was made, relative to the request's first.
type AttemptKind string

const (
	AttemptPrimary         AttemptKind = "primary"
	AttemptRetry           AttemptKind = "retry"
	AttemptFailover        AttemptKind = "failover"
	AttemptModelFallback   AttemptKind = "model_fallback"
	AttemptAutoFallback    AttemptKind = "auto_fallback"
	AttemptRequestFallback AttemptKind = "request_fallback"
	AttemptHedge           AttemptKind = "hedge"
	AttemptSelector        AttemptKind = "selector"
)

// Attempt reasons: what triggered an attempt. The primary has none.
const (
	ReasonNone                       = ""
	ReasonStatus429                  = "status_429"
	ReasonStatus5xx                  = "status_5xx"
	ReasonTimeout                    = "timeout"
	ReasonCircuitOpen                = "circuit_open"
	ReasonContextLength              = "context_length"
	ReasonContentFilter              = "content_filter"
	ReasonNetwork                    = "network"
	ReasonSkippedInsufficientCredits = "skipped_insufficient_credits"
	ReasonRetryBudget                = "retry_budget"
	ReasonAttemptBudget              = "attempt_budget"
	ReasonDeadline                   = "deadline"
	ReasonPolicy                     = "policy"
	ReasonToolHistoryIncompatible    = "tool_history_incompatible"
)

// AttemptOutcome is how an attempt ended.
type AttemptOutcome string

const (
	OutcomeSuccess     AttemptOutcome = "success"
	OutcomeError       AttemptOutcome = "error"
	OutcomeTimeout     AttemptOutcome = "timeout"
	OutcomeCancelled   AttemptOutcome = "cancelled"
	OutcomeDiscarded   AttemptOutcome = "discarded"
	OutcomeSkipped     AttemptOutcome = "skipped"
	OutcomeCircuitOpen AttemptOutcome = "circuit_open"
)

// ErrorClass classifies a failed attempt.
type ErrorClass string

const (
	ErrClassRateLimited         ErrorClass = "rate_limited"
	ErrClassTimeout             ErrorClass = "timeout"
	ErrClassUpstream5xx         ErrorClass = "upstream_5xx"
	ErrClassUpstream4xx         ErrorClass = "upstream_4xx"
	ErrClassContextLength       ErrorClass = "context_length"
	ErrClassContentFilter       ErrorClass = "content_filter"
	ErrClassAuth                ErrorClass = "auth"
	ErrClassInvalidRequest      ErrorClass = "invalid_request"
	ErrClassNetwork             ErrorClass = "network"
	ErrClassCircuitOpen         ErrorClass = "circuit_open"
	ErrClassPolicy              ErrorClass = "policy"
	ErrClassBudget              ErrorClass = "budget"
	ErrClassInsufficientCredits ErrorClass = "insufficient_credits"
	ErrClassCancelled           ErrorClass = "cancelled"
	ErrClassUnknown             ErrorClass = "unknown"
)

// ErrorOrigin says which side a failed attempt's error came from.
type ErrorOrigin string

const (
	OriginUpstream ErrorOrigin = "upstream"
	OriginGateway  ErrorOrigin = "gateway"
	OriginClient   ErrorOrigin = "client"
)

// CredentialType is whose credential paid for an attempt.
type CredentialType string

const (
	CredentialManaged CredentialType = "managed"
	CredentialBYOK    CredentialType = "byok"
)

// CostSource says where an attempt's (or a request's) price came from.
// Request-level billing may also say CostSourceMixed or CostSourceCacheHit.
type CostSource string

const (
	CostSourceProviderReported CostSource = "provider_reported"
	CostSourceCatalog          CostSource = "catalog"
	CostSourceCatalogMax       CostSource = "catalog_max"
	CostSourcePriceCard        CostSource = "price_card"
	CostSourceModelDB          CostSource = "modeldb"
	CostSourceOrgCustom        CostSource = "org_custom"
	CostSourceUnpriced         CostSource = "unpriced"
	CostSourceMixed            CostSource = "mixed"
	CostSourceCacheHit         CostSource = "cache_hit"
)

// Attempt is one upstream call made for a request. It is
// the shape of log-entry-v2 attempts[] and of the AgentccRequestAttempt row.
// Empty values are omitted when marshalled; the identity fields are not.
type Attempt struct {
	AttemptID string      `json:"attempt_id"`
	Index     int         `json:"index"`
	Kind      AttemptKind `json:"kind"`
	Reason    string      `json:"reason,omitempty"`

	Provider               string `json:"provider"`
	Model                  string `json:"model"`
	EndpointID             string `json:"endpoint_id,omitempty"`
	UpstreamProvider       string `json:"upstream_provider,omitempty"`
	ServedUpstreamProvider string `json:"served_upstream_provider,omitempty"`
	// UpstreamKeyID is the key_hash of the per-org upstream key. Never the key.
	UpstreamKeyID  string         `json:"upstream_key_id,omitempty"`
	CredentialType CredentialType `json:"credential_type"`

	StatusCode   int            `json:"status_code,omitempty"`
	Outcome      AttemptOutcome `json:"outcome"`
	ErrorClass   ErrorClass     `json:"error_class,omitempty"`
	ErrorOrigin  ErrorOrigin    `json:"error_origin,omitempty"`
	ErrorMessage string         `json:"error_message,omitempty"` // at most 512 characters, credentials scrubbed

	StartedAt time.Time `json:"started_at"`
	LatencyMs int64     `json:"latency_ms,omitempty"`
	TTFBMs    int64     `json:"ttfb_ms,omitempty"`

	PromptTokens     int `json:"prompt_tokens,omitempty"`
	CompletionTokens int `json:"completion_tokens,omitempty"`
	CachedTokens     int `json:"cached_tokens,omitempty"`
	CacheWriteTokens int `json:"cache_write_tokens,omitempty"`
	ReasoningTokens  int `json:"reasoning_tokens,omitempty"`

	UpstreamCostMicros int64 `json:"upstream_cost_micros,omitempty"`
	BilledCostMicros   int64 `json:"billed_cost_micros,omitempty"`
	// LandedCostMicros and the fee rates are set for managed attempts only,
	// and are never sent to customers.
	LandedCostMicros int64      `json:"landed_cost_micros,omitempty"`
	UpstreamFeeBps   int        `json:"upstream_fee_bps,omitempty"`
	ProcessingFeeBps int        `json:"processing_fee_bps,omitempty"`
	CostSource       CostSource `json:"cost_source,omitempty"`
	UsageEstimated   bool       `json:"usage_estimated,omitempty"`
	Billable         bool       `json:"billable,omitempty"`
}

// AttemptID is the id of a request's index-th attempt: "<request_id>.<index>".
func AttemptID(requestID string, index int) string {
	return requestID + "." + strconv.Itoa(index)
}

// AddAttempt records a new attempt on rc and returns it with its Index (from
// 1) and AttemptID set. Past MaxAttempts it records nothing and returns a
// with Index 0 and an empty AttemptID.
func (rc *RequestContext) AddAttempt(a Attempt) Attempt {
	rc.mu.Lock()
	defer rc.mu.Unlock()
	if len(rc.attempts) >= MaxAttempts {
		a.Index, a.AttemptID = 0, ""
		return a
	}
	if rc.attempts == nil {
		rc.attempts = make([]Attempt, 0, 4)
	}
	a.Index = len(rc.attempts) + 1
	a.AttemptID = AttemptID(rc.RequestID, a.Index)
	rc.attempts = append(rc.attempts, a)
	return a
}

// AttemptsSnapshot returns a copy of rc's attempts, in order, or nil when
// there are none.
func (rc *RequestContext) AttemptsSnapshot() []Attempt {
	rc.mu.Lock()
	defer rc.mu.Unlock()
	if len(rc.attempts) == 0 {
		return nil
	}
	return append([]Attempt(nil), rc.attempts...)
}

// AttemptCount returns how many attempts rc has recorded.
func (rc *RequestContext) AttemptCount() int {
	rc.mu.Lock()
	defer rc.mu.Unlock()
	return len(rc.attempts)
}

// UpdateAttempt applies fn to the attempt with id, under rc's lock, and
// reports whether there was one. fn must not change the attempt's identity
// (AttemptID, Index) and must not call back into rc.
func (rc *RequestContext) UpdateAttempt(id string, fn func(*Attempt)) bool {
	rc.mu.Lock()
	defer rc.mu.Unlock()
	for i := range rc.attempts {
		if rc.attempts[i].AttemptID == id {
			fn(&rc.attempts[i])
			return true
		}
	}
	return false
}
