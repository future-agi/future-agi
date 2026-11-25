package models

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
)

func supportedInspection() *InspectionState {
	s := NewInspectionState(InspectionContract{Operation: "test.chat", Input: InspectionSupported, Output: InspectionSupported})
	s.BindPrincipal("org", "reader")
	return s
}

func TestInspectionRequiresDistinctCompletenessAndDecision(t *testing.T) {
	s := supportedInspection()
	payload := map[string]any{"text": "safe"}
	s.Require(InspectionInput)
	if err := s.ReleaseError(); err == nil || err.Code != "input_guardrail_unsupported" {
		t.Fatalf("missing receipt was releasable: %v", err)
	}
	s.Decide(InspectionInput, payload)
	if s.IsDecided(InspectionInput, payload) {
		t.Fatal("decision without projection accepted")
	}
	s.Complete(InspectionInput, payload)
	if !s.IsComplete(InspectionInput, payload) || s.ReleaseError() == nil {
		t.Fatal("complete projection substituted for a policy decision")
	}
	s.Decide(InspectionInput, payload)
	if err := s.ReleaseError(); err != nil {
		t.Fatal(err)
	}
	payload["text"] = "changed"
	if s.IsComplete(InspectionInput, payload) || s.ReleaseError() == nil {
		t.Fatal("mutation retained the prior receipt")
	}
	s.Complete(InspectionInput, payload)
	if s.ReleaseError() == nil {
		t.Fatal("fresh projection retained a stale decision")
	}
	s.Decide(InspectionInput, payload)
	if err := s.ReleaseError(); err != nil {
		t.Fatal(err)
	}
	s.Require(InspectionOutput)
	if err := s.ReleaseError(); err == nil || err.Code != "output_guardrail_unsupported" {
		t.Fatalf("input decision covered output: %v", err)
	}
}

func TestInspectionPolicyRevisionIsScopedToAdmittedStage(t *testing.T) {
	for _, stage := range []InspectionStage{InspectionInput, InspectionOutput} {
		s := supportedInspection()
		payload := map[string]any{"text": "safe"}
		for _, current := range []InspectionStage{InspectionInput, InspectionOutput} {
			s.Require(current)
			s.Complete(current, payload)
			s.SetPolicyRevision(current, "old")
			s.Decide(current, payload)
		}
		s.SetPolicyRevision(stage, "old")
		if err := s.ReleaseError(); err != nil {
			t.Fatalf("same revision revoked a completed decision: %v", err)
		}
		other := InspectionOutput
		if stage == InspectionOutput {
			other = InspectionInput
		}
		for _, revision := range []string{"new", "", ""} {
			s.SetPolicyRevision(stage, revision)
			if s.IsDecided(stage, payload) || !s.IsComplete(stage, payload) || !s.IsDecided(other, payload) || s.ReleaseError() == nil {
				t.Fatalf("stage %d revision %q did not invalidate only its decision", stage, revision)
			}
			s.Decide(stage, payload)
			if err := s.ReleaseError(); err != nil {
				t.Fatalf("new stage decision cannot release: %v", err)
			}
		}
		s.SetPolicyRevision(InspectionStage(99), "invalid-stage")
		if err := s.ReleaseError(); err != nil {
			t.Fatalf("invalid stage affected valid receipts: %v", err)
		}
	}
	var absent *InspectionState
	absent.SetPolicyRevision(InspectionInput, "nil-state")
}

func TestInspectionPrincipalAndOperationCannotBeRebound(t *testing.T) {
	s := NewInspectionState(InspectionContract{Operation: "test.chat", Input: InspectionSupported})
	payload := &ChatCompletionRequest{}
	s.Complete(InspectionInput, payload)
	s.Require(InspectionInput)
	s.Decide(InspectionInput, payload)
	if s.ReleaseError() == nil || s.BindPrincipal("", "key") {
		t.Fatal("unauthenticated evidence was accepted")
	}
	if !s.BindPrincipal("org", "reader") || !s.BindPrincipal("org", "reader") || s.BindPrincipal("org", "creator") || s.BindPrincipal("other", "reader") {
		t.Fatal("principal binding was not immutable")
	}
	s.Decide(InspectionInput, payload)
	if err := s.ReleaseError(); err != nil {
		t.Fatal(err)
	}
	contract := s.Contract()
	contract.Operation = "another-operation"
	if s.Contract().Operation != "test.chat" {
		t.Fatal("operation contract changed externally")
	}
	encoded, err := json.Marshal(s)
	if err != nil || strings.Contains(string(encoded), "reader") || strings.Contains(string(encoded), "test.chat") {
		t.Fatalf("private evidence serialized: %s %v", encoded, err)
	}
}

func TestInspectionUnknownNilAndUnencodableCannotComplete(t *testing.T) {
	for _, contract := range []InspectionContract{{}, {Operation: "unknown"}, {Input: InspectionSupported}, {Operation: "supported", Input: InspectionSupported}} {
		for _, payload := range []any{nil, (*ChatCompletionRequest)(nil), make(chan int)} {
			s := NewInspectionState(contract)
			s.BindPrincipal("org", "reader")
			s.Require(InspectionInput)
			s.Complete(InspectionInput, payload)
			s.Decide(InspectionInput, payload)
			if s.IsComplete(InspectionInput, payload) || s.ReleaseError() == nil {
				t.Fatalf("invalid completeness: contract=%+v payload=%T", contract, payload)
			}
		}
	}
	s := NewInspectionState(InspectionContract{})
	s.Complete(InspectionInput, map[string]string{"forged": "receipt"})
	if s.IsComplete(InspectionInput, map[string]string{"forged": "receipt"}) {
		t.Fatal("unknown operation gained shape support from a payload")
	}
}

func TestInspectionNotApplicableRequiresExactReasonAndPrincipal(t *testing.T) {
	for _, tc := range []struct {
		operation string
		reason    string
		bound     bool
		wantOK    bool
	}{{"", "metadata only", true, false}, {"metadata.get", "", true, false}, {"metadata.get", "metadata only", false, false}, {"metadata.get", "metadata only", true, true}} {
		s := NewInspectionState(InspectionContract{Operation: tc.operation, Input: InspectionNotApplicable, Output: InspectionNotApplicable, Reason: tc.reason})
		if tc.bound {
			s.BindPrincipal("org", "reader")
		}
		s.Require(InspectionInput)
		s.Require(InspectionOutput)
		if (s.ReleaseError() == nil) != tc.wantOK {
			t.Fatalf("N/A contract=%+v error=%v", tc, s.ReleaseError())
		}
	}
}

func TestInspectionContextAndPoolDoNotCarryEvidence(t *testing.T) {
	s := supportedInspection()
	ctx := WithInspection(context.Background(), s)
	if InspectionFromContext(ctx) != s || InspectionFromContext(context.Background()) != nil {
		t.Fatal("context state was not request local")
	}
	rc := AcquireRequestContext()
	rc.Inspection = s
	encoded, err := json.Marshal(rc)
	if err != nil || strings.Contains(string(encoded), `"Inspection":`) {
		t.Fatalf("request context serialized evidence: %s %v", encoded, err)
	}
	rc.Release()
	if rc.Inspection != nil {
		t.Fatal("released context retained inspection evidence")
	}
	next := AcquireRequestContext()
	defer next.Release()
	if next.Inspection != nil {
		t.Fatal("pooled context inherited a prior verdict")
	}
}

type terminalErrorRecorder struct {
	*httptest.ResponseRecorder
	calls int
}

func (w *terminalErrorRecorder) GuardrailError(err *APIError) *APIError {
	w.calls++
	return ErrGuardrailBlocked("output_guardrail_unsupported", "safe terminal refusal")
}

func TestWriteErrorExplicitlyNotifiesReleaseGuard(t *testing.T) {
	w := &terminalErrorRecorder{ResponseRecorder: httptest.NewRecorder()}
	WriteError(w, &APIError{Status: 502, Message: "uninspected upstream content", Code: "upstream"})
	if w.calls != 1 || w.Code != 403 || strings.Contains(w.Body.String(), "uninspected") || !strings.Contains(w.Body.String(), "safe terminal refusal") {
		t.Fatalf("terminal notification failed: calls=%d status=%d body=%s", w.calls, w.Code, w.Body.String())
	}
}
