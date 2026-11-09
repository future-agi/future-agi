package models

import (
	"strconv"
	"time"
)

// MaxAttempts is the most attempts one request records (plan section 6.1),
// the bound log-entry-v2 holds attempts[], attempt_count and every attempt
// index to. A request may send more: once its record is full, AddAttempt
// gives an attempt that was sent the slot of the oldest one that carries no
// charge (Attempt.reclaimable), so the attempt that serves a request, and
// every attempt that may be charged, is always recorded and billed. When no
// slot can be reclaimed the attempt is not sent at all (CanRecordAttempt).
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

// Attempt is one upstream call made for a request (plan section 6.1). It is
// the shape of log-entry-v2 attempts[] and of the AgentccRequestAttempt row.
// Empty values are omitted when marshalled; the identity fields are not.
type Attempt struct {
	InclusiveIdentity InclusiveIdentity `json:"-"`
	AttemptID         string            `json:"attempt_id"`
	// FinancialID is request id + monotonic dispatch sequence, allocated before
	// reservation. Unlike the public slot identity, compaction never changes it.
	FinancialID string      `json:"financial_id,omitempty"`
	Index       int         `json:"index"`
	Kind        AttemptKind `json:"kind"`
	Reason      string      `json:"reason,omitempty"`

	Provider               string `json:"provider"`
	Model                  string `json:"model"`
	EndpointID             string `json:"endpoint_id,omitempty"`
	UpstreamProvider       string `json:"upstream_provider,omitempty"`
	ServedUpstreamProvider string `json:"served_upstream_provider,omitempty"`
	// UpstreamKeyID is the key_hash of the per-org upstream key. Never the key.
	UpstreamKeyID  string         `json:"upstream_key_id,omitempty"`
	CredentialType CredentialType `json:"credential_type"`

	// ProviderRejected is explicit response provenance, never inferred from 502.
	ProviderRejected bool           `json:"-"`
	StatusCode       int            `json:"status_code,omitempty"`
	Outcome          AttemptOutcome `json:"outcome"`
	ErrorClass       ErrorClass     `json:"error_class,omitempty"`
	ErrorOrigin      ErrorOrigin    `json:"error_origin,omitempty"`
	ErrorMessage     string         `json:"error_message,omitempty"` // at most 512 characters, credentials scrubbed

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
	// and are never sent to customers (decision 18).
	LandedCostMicros int64      `json:"landed_cost_micros,omitempty"`
	UpstreamFeeBps   int        `json:"upstream_fee_bps,omitempty"`
	ProcessingFeeBps int        `json:"processing_fee_bps,omitempty"`
	CostSource       CostSource `json:"cost_source,omitempty"`
	UsageEstimated   bool       `json:"usage_estimated,omitempty"`
	Billable         bool       `json:"billable,omitempty"`
	// Admitted prices stay with the attempt across fallback hops and refreshes.
	Pricing *AttemptPricing `json:"-"`
	// NativeTokenUsage selects the conservative incomplete-metering policy
	// for native adapters, without changing OpenAI/Azure wire settlement.
	NativeTokenUsage bool `json:"-"`
	// Usage retains an attempt's reported charge even when it failed before
	// output and a later attempt served. It is an immutable internal copy.
	Usage                *Usage `json:"-"`
	ServedModel          string `json:"-"`
	UpstreamGenerationID string `json:"-"`
	// Generation is response-bound identity, distinct from the requested Model.
	Generation GenerationEvidence `json:"-"`
	// InFlight marks an attempt that was sent and has not finished: the
	// gateway records it before its upstream call and clears it when the
	// call returns. A full record never gives its slot away nor renumbers
	// it, so the call's finish still finds it by its AttemptID.
	InFlight bool `json:"-"`
}

// AttemptPricing is an immutable snapshot of the admitted cards. Keeping it
// behind a pointer also keeps Attempt a comparable value for pool hygiene.
type AttemptPricing struct {
	HoldMicros          int64
	ReservePrice        *PriceCard
	EndpointSet         []EndpointRef
	InclusiveHoldMicros int64
}

// NewAttemptPricing snapshots b's admitted cards for one attempt. It copies
// them, so no later change to b, in place or by a new hop, reaches an attempt
// already made.
func NewAttemptPricing(b *BillingState) *AttemptPricing {
	p := &AttemptPricing{InclusiveHoldMicros: b.InclusiveHoldMicros, HoldMicros: b.AttemptHoldMicros}
	if b.ReservePrice != nil {
		card := b.ReservePrice.Clone()
		p.ReservePrice = &card
	}
	if b.EndpointSet != nil {
		p.EndpointSet = append([]EndpointRef(nil), b.EndpointSet...)
	}
	return p
}

// AttemptID is the id of a request's index-th attempt: "<request_id>.<index>".
func AttemptID(requestID string, index int) string {
	return requestID + "." + strconv.Itoa(index)
}

// NewFinancialAttemptID allocates a request-wide identity before reservation or
// dispatch. Neither model/provider changes nor public-log compaction reuse it.
// Reservation consumes the sequence even if the caller crashes before AddAttempt.
func (rc *RequestContext) NewFinancialAttemptID() string {
	rc.mu.Lock()
	defer rc.mu.Unlock()
	return rc.nextFinancialIDLocked()
}

func (rc *RequestContext) nextFinancialIDLocked() string {
	rc.financialSeq++
	return rc.RequestID + "." + strconv.FormatUint(rc.financialSeq, 10)
}

// AddAttempt records a new attempt on rc and returns it with its Index (from
// 1) and AttemptID set. A record holds at most MaxAttempts. When it is full,
// an attempt that was sent takes the slot of the oldest recorded attempt that
// carries no charge (reclaimable): that one is dropped, the attempts after it
// move up one index (their AttemptIDs with it), and the new one is the last.
// A new skipped attempt, never sent and never charged, is not recorded once
// the record is full, nor is any attempt when no slot can be reclaimed: then
// a is returned with Index 0 and an empty AttemptID. The gateway asks
// CanRecordAttempt before it sends an attempt, so an attempt that was sent
// always gets a slot.
func (rc *RequestContext) AddAttempt(a Attempt) Attempt {
	rc.mu.Lock()
	defer rc.mu.Unlock()
	if a.FinancialID == "" {
		a.FinancialID = rc.nextFinancialIDLocked()
	}
	if len(rc.attempts) >= MaxAttempts && (a.Outcome == OutcomeSkipped || !rc.reclaimSlotLocked()) {
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

// CanRecordAttempt reports whether AddAttempt would record one more attempt
// that is sent: the record has a free slot, or one it can reclaim. An
// attempt it cannot record must not be sent, as its answer or its charge
// would go unbilled.
func (rc *RequestContext) CanRecordAttempt() bool {
	rc.mu.Lock()
	defer rc.mu.Unlock()
	return len(rc.attempts) < MaxAttempts || rc.reclaimableLocked() >= 0
}

// reclaimable reports whether a recorded attempt may give its slot to an
// attempt that was sent: one never sent (skipped), or one that failed and
// carries no charge. An attempt that served, one still in flight, one whose
// outcome is unknown or that the gateway discarded, and one with any usage,
// token count, cost or billable verdict is kept, as the cost plugin may
// charge it (an upstream that reported usage before it failed, a cancelled
// call's estimate).
func (a *Attempt) reclaimable() bool {
	if a.InFlight {
		return false
	}
	switch a.Outcome {
	case OutcomeSkipped, OutcomeError, OutcomeTimeout, OutcomeCancelled, OutcomeCircuitOpen:
		return !a.carriesCharge()
	}
	return false
}

// carriesCharge reports whether a holds any evidence that it was or may be
// charged.
func (a *Attempt) carriesCharge() bool {
	return a.Billable || a.Usage != nil ||
		a.PromptTokens != 0 || a.CompletionTokens != 0 || a.CachedTokens != 0 || a.CacheWriteTokens != 0 || a.ReasoningTokens != 0 ||
		a.UpstreamCostMicros != 0 || a.BilledCostMicros != 0 || a.LandedCostMicros != 0
}

// reclaimableLocked is the index of the oldest reclaimable attempt that no
// attempt in flight follows, or -1. Dropping it moves up only the attempts
// after it, so no attempt in flight is renumbered. rc.mu must be held.
func (rc *RequestContext) reclaimableLocked() int {
	first := 0
	for i := len(rc.attempts) - 1; i >= 0; i-- {
		if rc.attempts[i].InFlight {
			first = i + 1
			break
		}
	}
	for i := first; i < len(rc.attempts); i++ {
		if rc.attempts[i].reclaimable() {
			return i
		}
	}
	return -1
}

// reclaimSlotLocked drops the reclaimable attempt reclaimableLocked picks
// and renumbers the attempts after it, keeping their order, so the record's
// indexes stay 1 to len. It reports whether it freed a slot. rc.mu must be
// held.
func (rc *RequestContext) reclaimSlotLocked() bool {
	i := rc.reclaimableLocked()
	if i < 0 {
		return false
	}
	rc.attempts = append(rc.attempts[:i], rc.attempts[i+1:]...)
	for j := i; j < len(rc.attempts); j++ {
		rc.attempts[j].Index = j + 1
		rc.attempts[j].AttemptID = AttemptID(rc.RequestID, j+1)
	}
	return true
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
// (AttemptID, Index) and must not call back into rc. An attempt that is not
// in flight moves up one index when a full record reclaims an earlier slot
// (AddAttempt), so a caller reads its id from a snapshot taken after the
// request's last attempt was added.
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
