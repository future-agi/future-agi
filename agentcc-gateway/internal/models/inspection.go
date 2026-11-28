package models

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"sync"
)

// InspectionStage identifies the content boundary, not a pipeline plugin stage.
type InspectionStage uint8

const (
	InspectionInput InspectionStage = iota
	InspectionOutput
)

// InspectionSupport is an operation declaration. Supported does not itself
// attest that a handler projected the whole payload or that policy ran.
type InspectionSupport uint8

const (
	InspectionUnknown InspectionSupport = iota
	InspectionSupported
	InspectionNotApplicable
)

type InspectionContract struct {
	Operation string
	Input     InspectionSupport
	Output    InspectionSupport
	Reason    string
}

type inspectionReceipt struct {
	payload  any
	digest   [sha256.Size]byte
	complete bool
	decided  bool
	required bool
	// Policy revision belongs to this stage's admission invocation. Resolving
	// output policy must not revoke an already completed input decision.
	policyRevision string
}

// InspectionState is request-local evidence. It must never be restored from
// metadata, a cached response, or a stored creator's policy verdict. Its fields
// are private so only the typed server/auth/policy seams can advance it.
type InspectionState struct {
	mu       sync.Mutex
	contract InspectionContract
	orgID    string
	keyID    string
	bound    bool
	receipts [2]inspectionReceipt
}

func NewInspectionState(contract InspectionContract) *InspectionState {
	return &InspectionState{contract: contract}
}

// BindPrincipal is called with the authenticated principal, never caller map
// fields. Binding is immutable, including across creator accounting changes.
func (s *InspectionState) BindPrincipal(orgID, keyID string) bool {
	if s == nil || orgID == "" || keyID == "" {
		return false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.bound {
		return s.orgID == orgID && s.keyID == keyID
	}
	s.orgID, s.keyID, s.bound = orgID, keyID, true
	// A projection may precede authentication, but a decision may not.
	for i := range s.receipts {
		s.receipts[i].decided = false
	}
	return true
}

func (s *InspectionState) Principal() (orgID, keyID string) {
	if s == nil {
		return "", ""
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.orgID, s.keyID
}

func (s *InspectionState) Contract() InspectionContract {
	if s == nil {
		return InspectionContract{}
	}
	return s.contract // immutable after construction
}

func (s *InspectionState) support(stage InspectionStage) InspectionSupport {
	if s.contract.Operation == "" {
		return InspectionUnknown
	}
	switch stage {
	case InspectionInput:
		return s.contract.Input
	case InspectionOutput:
		return s.contract.Output
	default:
		return InspectionUnknown
	}
}

func (s *InspectionState) fingerprint(stage InspectionStage, payload any) ([sha256.Size]byte, bool) {
	// JSON null includes typed nil pointers. Neither a nil projection nor an
	// unencodable payload is evidence of complete content.
	body, err := json.Marshal(payload)
	if err != nil || string(body) == "null" {
		return [sha256.Size]byte{}, false
	}
	b, err := json.Marshal(struct {
		Operation string
		Stage     InspectionStage
		Payload   json.RawMessage
	}{s.contract.Operation, stage, body})
	if err != nil {
		return [sha256.Size]byte{}, false
	}
	return sha256.Sum256(b), true
}

// Complete attests only shape completeness. Any new projection invalidates the
// previous decision, even if its serialized bytes happen to be identical.
func (s *InspectionState) Complete(stage InspectionStage, payload any) {
	if s == nil || stage > InspectionOutput {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	r := &s.receipts[stage]
	r.decided = false
	r.complete = false
	r.payload = payload
	if s.support(stage) != InspectionSupported {
		return
	}
	r.digest, r.complete = s.fingerprint(stage, payload)
}

func (s *InspectionState) complete(stage InspectionStage, payload any) bool {
	r := &s.receipts[stage]
	if !r.complete || s.support(stage) != InspectionSupported {
		return false
	}
	digest, ok := s.fingerprint(stage, payload)
	return ok && digest == r.digest
}

func (s *InspectionState) IsComplete(stage InspectionStage, payload any) bool {
	if s == nil || stage > InspectionOutput {
		return false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.complete(stage, payload)
}

// SetPolicyRevision scopes decision reuse to the current stage's resolved policy.
// A later stage may resolve a newer config without invalidating the completed
// invocation of another stage. Independent admissions of the same stage still
// invalidate its old decision when policy changes. Shape evidence is unaffected;
// an empty revision always invalidates reuse, never creates evidence.
func (s *InspectionState) SetPolicyRevision(stage InspectionStage, revision string) {
	if s == nil || stage > InspectionOutput {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	r := &s.receipts[stage]
	if revision != "" && revision == r.policyRevision {
		return
	}
	r.policyRevision = revision
	r.decided = false
}

// IsDecided permits repeated pipeline seams in the same request to reuse only
// a completed decision for the unchanged, fully projected payload.
func (s *InspectionState) IsDecided(stage InspectionStage, payload any) bool {
	if s == nil || stage > InspectionOutput {
		return false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.bound && s.receipts[stage].decided && s.complete(stage, payload)
}

// Decide records a completed selected-policy decision, not a check's start.
// A legitimate remediator must refresh Complete before deciding the new shape.
func (s *InspectionState) Decide(stage InspectionStage, payload any) {
	if s == nil || stage > InspectionOutput {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.receipts[stage].decided = s.bound && s.complete(stage, payload)
}

func (s *InspectionState) Require(stage InspectionStage) {
	if s == nil || stage > InspectionOutput {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.receipts[stage].required = true
}

// Required reports whether selected blocking policy made this stage a release
// obligation. Error writers use this to avoid treating upstream errors as safe.
func (s *InspectionState) Required(stage InspectionStage) bool {
	if s == nil || stage > InspectionOutput {
		return false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.receipts[stage].required
}

// ReleaseError holds content until every applicable selected Block decision is
// complete and the retained payload still matches what was inspected. Exact N/A
// requires a named operation and an explicit reason; unknown is never N/A.
func (s *InspectionState) ReleaseError() *APIError {
	if s == nil {
		return nil // no selected policy has required a decision
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	for stage := InspectionInput; stage <= InspectionOutput; stage++ {
		r := &s.receipts[stage]
		if !r.required {
			continue
		}
		if s.bound && s.support(stage) == InspectionNotApplicable && s.contract.Reason != "" {
			continue
		}
		if s.bound && r.decided && s.complete(stage, r.payload) {
			continue
		}
		code := "input_guardrail_unsupported"
		if stage == InspectionOutput {
			code = "output_guardrail_unsupported"
		}
		return ErrGuardrailBlocked(code, code+": complete selected-policy inspection decision required")
	}
	return nil
}

type inspectionContextKey struct{}

func WithInspection(ctx context.Context, state *InspectionState) context.Context {
	return context.WithValue(ctx, inspectionContextKey{}, state)
}

func InspectionFromContext(ctx context.Context) *InspectionState {
	state, _ := ctx.Value(inspectionContextKey{}).(*InspectionState)
	return state
}
