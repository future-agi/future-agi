package budget

import (
	"context"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// NativePolicyPreparer applies effective native tool policy before quoting.
// A nil preparer cannot satisfy controlled native admission.
type NativePolicyPreparer interface {
	PrepareNative(context.Context, *models.RequestContext) *models.APIError
}

// NativeQuote is a package-issued handle. The private marker alone does not
// establish provenance: consumers must validate concrete state and request
// binding, rejecting nil, foreign and interface-embedding wrapper handles.
// Unavailable amounts return false, never a known zero.
type NativeQuote interface {
	CapMicros() (int64, bool)
	ManagedHoldMicros() (int64, bool)
	nativeQuote()
}

// NativeAttempt requires validated package-issued state and request binding;
// its private marker alone is not authority. Body returns a private copy of the
// exact admitted bytes, and Context remains bounded by the admitted lifetime.
type NativeAttempt interface {
	Context() context.Context
	Body() []byte
	FinancialID() string
	nativeAttempt()
}

// NativeUsageKind has no valid zero value.
type NativeUsageKind uint8

const (
	NativeCumulativeUsage NativeUsageKind = iota + 1
	NativeDeltaUsage
)

// NativeUsageEvent is private in-process evidence, not a caller DTO. Complete
// alone is not terminal proof: correlation, qualified decoder, generation and
// complete priced-dimension coverage must be verified before settlement.
type NativeUsageEvent struct {
	FinancialID string
	Generation  models.GenerationEvidence
	Usage       *models.Usage
	Kind        NativeUsageKind
	Sequence    uint64
	Complete    bool
}

// NativeEnd records a lifecycle reason, not terminal usage completeness.
// Zero is invalid; NativeCompleted alone cannot authorize settlement.
type NativeEnd uint8

const (
	NativeCompleted NativeEnd = iota + 1
	NativeAborted
	NativeTimeout
	NativeProviderDisconnected
	NativeProviderError
)

// NativeSettlementState has no successful zero value.
type NativeSettlementState uint8

const (
	NativeSettled NativeSettlementState = iota + 1
	NativeFenced
	NativeSettlementUnavailable
)

type NativeSettlement struct {
	State NativeSettlementState
	Error *models.APIError
}
