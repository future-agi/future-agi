package models

// Billing modes (rc.Billing.Mode and log-entry-v2 billing_mode).
const (
	BillingModeManaged = "managed"
	BillingModeBYOK    = "byok"
	BillingModeAny     = "any" // only as a restriction
)

// BillingState is how a request is billed, filled as it is routed, reserved,
// priced and settled. All money is in integer micros. Each field is written by
// the component that owns its domain.
type BillingState struct {
	Mode           string         // managed or byok
	CredentialType CredentialType // of the attempt that served
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

	HoldMicros       int64
	Reserved         bool
	UpstreamMicros   int64
	BilledMicros     int64 // sum over billable attempts, never re-rounded
	MarkupMicros     int64
	MarkupBps        int
	LandedMicros     int64 // never sent to customers
	UpstreamFeeBps   int
	ProcessingFeeBps int
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
	Price            PriceCard
}

// Clone returns a deep copy of b.
func (b BillingState) Clone() BillingState {
	if b.EndpointSet != nil {
		b.EndpointSet = append([]EndpointRef(nil), b.EndpointSet...)
	}
	if b.ReservePrice != nil {
		p := *b.ReservePrice
		b.ReservePrice = &p
	}
	b.CallCapUSD = cloneFloat(b.CallCapUSD)
	b.RunBudgetRemainingUSD = cloneFloat(b.RunBudgetRemainingUSD)
	b.CreditsRemainingMicros = cloneInt64(b.CreditsRemainingMicros)
	return b
}
