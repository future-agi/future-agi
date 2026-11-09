package models

import "encoding/json"

// Billing modes (rc.Billing.Mode and log-entry-v2 billing_mode).
const (
	BillingModeManaged = "managed"
	BillingModeBYOK    = "byok"
	BillingModeAny     = "any" // only as a restriction
)

// BillingState is how a request is billed, filled as it is routed, reserved,
// priced and settled. All money is in integer micros (decision 8). Fields
// read by more than one lane are named in plan section 5 (G0b contract b);
// each is written by the lane that owns its domain.
type BillingState struct {
	InclusiveIdentity InclusiveIdentity `json:"-"`
	Mode              string            // managed or byok
	CredentialType    CredentialType    // of the attempt that served
	// BusyCredential is the credential type of the last attempt while it
	// ended provider_busy (refused before dispatch), "" otherwise. A fallback
	// or failover hop that would change it is refused: a busy entry never
	// moves a request to the other billing mode.
	BusyCredential CredentialType
	// ChainMode is the billing mode (managed or byok) of the request's first
	// attempt, or of its route when a fallback hop is resolved before any
	// attempt starts. Unless the node allows mixed billing chains, every
	// later attempt of the request must be billed in it (the gateway's
	// mixedBillingRefusal). Empty until set.
	ChainMode CredentialType
	// ModeRestriction is the caller's billing-mode restriction (byok,
	// managed or any), from a header or a runtime claim. It only narrows.
	ModeRestriction string

	// EndpointID and UpstreamProvider are the endpoint chosen first;
	// EndpointSet is every eligible endpoint OpenRouter may serve from, with
	// its price card (at most 5, ranked).
	EndpointID       string
	UpstreamProvider string
	EndpointSet      []EndpointRef
	// ReservePrice is the card the hold was reserved at: the highest in the
	// endpoint set.
	ReservePrice *PriceCard
	// ServedUpstreamProvider is the upstream that actually served.
	ServedUpstreamProvider string

	// AttemptHoldMicros is the most recent reservation, not the request sum.
	AttemptHoldMicros   int64
	OwnedRequest        bool
	OwnedRequired       bool   // opt-in mixed catalogue: own even the first token hop
	OwnedAttemptID      string // immutable financial id of the pending reservation
	AdmissionExtra      map[string]json.RawMessage
	AdmissionCaptured   bool
	HoldMicros          int64
	Reserved            bool
	InclusiveHoldMicros int64 // current attempt's bound, not the request sum
	InclusiveOutcomes   []InclusiveAttempt
	InclusiveDispatched bool // cleanup must settle, never void, after dispatch
	UpstreamMicros      int64
	BilledMicros        int64 // sum over billable attempts, never re-rounded
	MarkupMicros        int64
	MarkupBps           int
	LandedMicros        int64 // never sent to customers
	UpstreamFeeBps      int
	ProcessingFeeBps    int
	// UpstreamKeyID is the key_hash of the per-org upstream key. Never the key.
	UpstreamKeyID string
	// UpstreamUser is the hashed org id sent upstream as `user` on managed
	// attempts.
	UpstreamUser   string
	CostSource     CostSource
	UsageEstimated bool

	CallCapUSD             *float64
	RunBudgetRemainingUSD  *float64
	CreditsRemainingMicros *int64

	IdempotencyKey   string
	IdempotentReplay bool
}

// EndpointRef is one endpoint in a managed request's endpoint set.
type EndpointRef struct {
	ID               string
	UpstreamProvider string
	ProviderName     string // admitted display name; never resolved from a later catalogue
	Price            PriceCard
}

// Clone returns a deep copy of b.
func (b BillingState) Clone() BillingState {
	if b.AdmissionExtra != nil {
		extras := map[string]json.RawMessage{}
		for k, v := range b.AdmissionExtra {
			extras[k] = append(json.RawMessage(nil), v...)
		}
		b.AdmissionExtra = extras
	}
	b.InclusiveOutcomes = append([]InclusiveAttempt(nil), b.InclusiveOutcomes...)
	if b.EndpointSet != nil {
		b.EndpointSet = append([]EndpointRef(nil), b.EndpointSet...)
	}
	if b.ReservePrice != nil {
		p := b.ReservePrice.Clone()
		b.ReservePrice = &p
	}
	b.CallCapUSD = cloneFloat(b.CallCapUSD)
	b.RunBudgetRemainingUSD = cloneFloat(b.RunBudgetRemainingUSD)
	b.CreditsRemainingMicros = cloneInt64(b.CreditsRemainingMicros)
	return b
}
