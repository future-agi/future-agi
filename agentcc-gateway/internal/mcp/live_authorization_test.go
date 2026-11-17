package mcp

import (
	"context"
	"strings"
	"sync"
	"testing"
	"time"
)

type admissionTestAuthorizer struct {
	mu       sync.Mutex
	decision LiveDecision
	calls    int
	request  LiveAuthorizationRequest
}

func (a *admissionTestAuthorizer) Authorize(ctx context.Context, r LiveAuthorizationRequest) LiveDecision {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.calls++
	a.request = r
	if _, ok := ctx.Deadline(); !ok {
		panic("missing authorization deadline")
	}
	return a.decision
}
func (a *admissionTestAuthorizer) set(d LiveDecision) { a.mu.Lock(); a.decision = d; a.mu.Unlock() }
func admissionTestRequest() LiveAuthorizationRequest {
	return LiveAuthorizationRequest{Scope: ScopeKey{OrgID: "org-a", WorkspaceID: "workspace-a"}, Actor: AdmissionActor{Kind: PrincipalKey, PersonID: "person-a", KeyID: "key-a"}, Action: ActionListResourceMetadata, Target: "opaque-a", Phase: PhaseInitial, RequestID: "request-a", UnitID: "unit-a"}
}
func admissionAllow() LiveDecision {
	return LiveDecision{Decision: LiveAllow, Reason: LiveReasonAllowed, MembershipRevision: NewMembershipRevision([]byte("revision-a"))}
}

func TestAdmissionLiveFailClosedAndFresh(t *testing.T) {
	r := admissionTestRequest()
	if _, err := authorizeLive(context.Background(), nil, r, MembershipRevision{}); err != errAdmissionUnavailable {
		t.Fatal("absent authorizer admitted")
	}
	a := &admissionTestAuthorizer{decision: admissionAllow()}
	for i := 0; i < 3; i++ {
		if _, err := authorizeLive(context.Background(), a, r, NewMembershipRevision([]byte("revision-a"))); err != nil {
			t.Fatal(err)
		}
	}
	if a.calls != 3 {
		t.Fatal("positive decision was cached")
	}
	for _, d := range []LiveDecision{{}, {Decision: LiveDenied, Reason: LiveReasonDenied}, {Decision: LiveUnavailable, Reason: LiveReasonUnavailable}, {Decision: LiveAllow}, {Decision: LiveAllow, Reason: LiveReasonAllowed, MembershipRevision: NewMembershipRevision([]byte("different"))}} {
		a.set(d)
		if _, err := authorizeLive(context.Background(), a, r, NewMembershipRevision([]byte("revision-a"))); err == nil {
			t.Fatal("invalid continued decision admitted")
		}
	}
}

func TestAdmissionActorScopeMatrix(t *testing.T) {
	a := &admissionTestAuthorizer{decision: admissionAllow()}
	for _, mutate := range []func(*LiveAuthorizationRequest){
		func(r *LiveAuthorizationRequest) { r.Scope.OrgID = "" },
		func(r *LiveAuthorizationRequest) { r.Scope.WorkspaceID = "" },
		func(r *LiveAuthorizationRequest) { r.Actor.PersonID = "" },
		func(r *LiveAuthorizationRequest) { r.Actor.KeyID = "" },
		func(r *LiveAuthorizationRequest) { r.Actor.Kind = PrincipalAdmin },
		func(r *LiveAuthorizationRequest) { r.Actor.Kind = PrincipalCallback },
		func(r *LiveAuthorizationRequest) { r.Action = "UNKNOWN" },
		func(r *LiveAuthorizationRequest) { r.Target = "" },
		func(r *LiveAuthorizationRequest) { r.RequestID = "" },
		func(r *LiveAuthorizationRequest) { r.UnitID = "" },
		func(r *LiveAuthorizationRequest) { r.Phase = "" },
	} {
		r := admissionTestRequest()
		mutate(&r)
		if _, err := authorizeLive(context.Background(), a, r, MembershipRevision{}); err != errAdmissionDenied {
			t.Fatal("invalid actor or scope reached authorizer")
		}
	}
	if a.calls != 0 {
		t.Fatal("invalid input called authority")
	}
	for _, action := range []AdmissionAction{ActionListResourceMetadata, ActionListPromptMetadata, ActionReadResource, ActionGetPrompt, ActionManageContent, ActionInvokeTool} {
		r := admissionTestRequest()
		r.Action = action
		if _, err := authorizeLive(context.Background(), a, r, MembershipRevision{}); err != nil {
			t.Fatal(err)
		}
	}
	if _, ok := admissionAction("tools/list"); ok {
		t.Fatal("unmapped action must remain closed")
	}
}

type admissionDeadlineAuthorizer struct{ deadline time.Duration }

func (a *admissionDeadlineAuthorizer) Authorize(ctx context.Context, _ LiveAuthorizationRequest) LiveDecision {
	d, _ := ctx.Deadline()
	a.deadline = time.Until(d)
	<-ctx.Done()
	return admissionAllow()
}
func TestAdmissionLookupDeadlineAndLateAllow(t *testing.T) {
	a := &admissionDeadlineAuthorizer{}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Millisecond)
	defer cancel()
	if _, err := authorizeLive(ctx, a, admissionTestRequest(), MembershipRevision{}); err != errAdmissionUnavailable {
		t.Fatal("late allow accepted")
	}
	if a.deadline <= 0 || a.deadline > admissionLookupTimeout {
		t.Fatal("lookup deadline was not clamped")
	}
}

func TestAdmissionValueFreeErrors(t *testing.T) {
	for _, err := range []error{errAdmissionDenied, errAdmissionUnavailable, errAdmissionRevisionUnavailable, errAdmissionLimit} {
		for _, value := range []string{"org-a", "person-a", "workspace-a", "key-a", "revision-a", "opaque-a"} {
			if strings.Contains(err.Error(), value) {
				t.Fatal("private value in refusal")
			}
		}
	}
}
