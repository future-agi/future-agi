package models

// MaxSkippedHops is how many cross-mode hop refusals one request keeps in
// full. Past it RequestContext.AddSkippedHop only counts them.
const MaxSkippedHops = 8

// SkippedHop is a hop the gateway refused before it was priced, held or sent
// because it would have moved the request between Future AGI credits
// (managed) and the org's own key (byok) on a node that does not allow mixed
// billing chains (MIXED-HOP-GUARD-1). It names the candidate and why it was
// refused, never a credential, a body or an upstream answer.
//
// It is not an attempt. It is kept apart from the request's attempts, so it
// never takes one of their MaxAttempts slots: a chain with many refused
// candidates still records the attempt that served it, and the cost plugin,
// the credit settlement and every other reader of the attempts never see it.
type SkippedHop struct {
	Kind     AttemptKind `json:"kind"`
	Reason   string      `json:"reason"`
	Provider string      `json:"provider"`
	Model    string      `json:"model"`
	// CredentialType is the billing mode the hop would have run in.
	CredentialType CredentialType `json:"credential_type"`
}

// AddSkippedHop records s as one of rc's skipped hops and returns how many
// rc has skipped so far, s included. Only the first MaxSkippedHops are kept;
// the count goes on past them.
func (rc *RequestContext) AddSkippedHop(s SkippedHop) int {
	rc.mu.Lock()
	defer rc.mu.Unlock()
	rc.skippedHopCount++
	if len(rc.skippedHops) < MaxSkippedHops {
		rc.skippedHops = append(rc.skippedHops, s)
	}
	return rc.skippedHopCount
}

// SkippedHops returns how many hops rc skipped and a copy of the first
// MaxSkippedHops of them, in order (nil when none).
func (rc *RequestContext) SkippedHops() (int, []SkippedHop) {
	rc.mu.Lock()
	defer rc.mu.Unlock()
	if len(rc.skippedHops) == 0 {
		return rc.skippedHopCount, nil
	}
	return rc.skippedHopCount, append([]SkippedHop(nil), rc.skippedHops...)
}

// SkippedHopCount returns how many hops rc skipped.
func (rc *RequestContext) SkippedHopCount() int {
	rc.mu.Lock()
	defer rc.mu.Unlock()
	return rc.skippedHopCount
}
