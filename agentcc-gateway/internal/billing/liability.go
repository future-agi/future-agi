package billing

// Liability is the resolved money for one owned attempt. A reported resolution
// may be zero; nil means unresolved, not free. This table is exclusive to owned
// catalogue settlement and does not change legacy or BYOK billing.
type Liability struct {
	UpstreamMicros int64
	BilledMicros   int64
	Estimated      bool
	Released       bool
}

// ResolveLiability is the single owned-attempt liability table, shared by live
// pricing/journaling and durable recovery. Callers normalize provider evidence
// and price complete reports before entering it; no caller invents a zero.
//
// Not dispatched                         -> release reservation
// Definitive provider rejection, no bill -> release attempt
// Complete reported usage/cost            -> reported exact charge
// Anything else                          -> full admitted hold
func ResolveLiability(dispatched, providerRejected, billingEvidence bool, reported *Liability, hold int64) Liability {
	if !dispatched || (providerRejected && !billingEvidence) {
		return Liability{Released: true}
	}
	if reported != nil {
		return *reported
	}
	return Liability{UpstreamMicros: hold * 100 / 105, BilledMicros: hold, Estimated: true}
}
