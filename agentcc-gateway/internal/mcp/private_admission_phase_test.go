package mcp

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
)

func TestAdmissionExactActionsAndPhases(t *testing.T) {
	want := []string{"LIST_RESOURCE_METADATA", "LIST_PROMPT_METADATA", "RESOURCE_CONTENT_READ", "PROMPT_GET_RENDER", "MANAGE_CONTENT_CONFIGURATION", "TOOL_INVOKE"}
	actions := []AdmissionAction{ActionListResourceMetadata, ActionListPromptMetadata, ActionReadResource, ActionGetPrompt, ActionManageContent, ActionInvokeTool}
	for i, action := range actions {
		if string(action) != want[i] {
			t.Fatal("action contract differs")
		}
	}
	for method, want := range map[string]AdmissionAction{"resources/list": ActionListResourceMetadata, "prompts/list": ActionListPromptMetadata} {
		if got, ok := admissionAction(method); !ok || got != want {
			t.Fatal("metadata actions conflated")
		}
	}
	for i, phase := range []AdmissionPhase{PhaseInitial, PhaseRelease, PhaseContinuation} {
		if string(phase) != []string{"INITIAL", "RELEASE", "CONTINUATION"}[i] {
			t.Fatal("phase contract differs")
		}
		r := admissionTestRequest()
		r.Phase = phase
		if !r.valid() {
			t.Fatal("canonical phase refused")
		}
	}
	r := admissionTestRequest()
	r.Phase = "client-phase"
	if r.valid() {
		t.Fatal("unknown phase accepted")
	}
}

func TestAdmissionTemporaryNullRevisionPhases(t *testing.T) {
	a := &admissionTestAuthorizer{decision: LiveDecision{Decision: LiveAllow, Reason: LiveReasonAllowed}}
	for _, phase := range []AdmissionPhase{PhaseInitial, PhaseRelease, PhaseContinuation} {
		r := admissionTestRequest()
		r.Phase = phase
		_, err := authorizeLive(context.Background(), a, r, MembershipRevision{})
		if phase == PhaseContinuation {
			if err != errAdmissionRevisionUnavailable {
				t.Fatal("null continuation did not terminate")
			}
		} else if err != nil {
			t.Fatal("temporary null first unit refused")
		}
	}
}

func TestAdmissionOpaqueRevisionRoundTrip(t *testing.T) {
	for _, raw := range [][]byte{nil, {}, {0, 255, 128, 1}} {
		r := NewMembershipRevision(raw)
		b, err := json.Marshal(r)
		if err != nil {
			t.Fatal(err)
		}
		var decoded MembershipRevision
		if json.Unmarshal(b, &decoded) != nil || decoded != r {
			t.Fatal("opaque revision changed during storage")
		}
	}
}

func admissionUnitFixture(t *testing.T, d LiveDecision) (*admissionWriter, *admissionRecordingWriter, *admissionTestAuthorizer) {
	t.Helper()
	a := &admissionTestAuthorizer{decision: d}
	r := admissionTestRequest()
	revision, err := authorizeLive(context.Background(), a, r, MembershipRevision{})
	if err != nil {
		t.Fatal(err)
	}
	watch := newAdmissionWatch(context.Background(), r, revision, func() LiveAuthorizer { return a }, newAdmissionFakeTicker(), nil)
	raw := &admissionRecordingWriter{ResponseRecorder: httptest.NewRecorder()}
	w, err := newAdmissionWriter(raw, httptest.NewRequest(http.MethodPost, "/mcp", nil), watch)
	if err != nil {
		watch.close()
		t.Fatal(err)
	}
	t.Cleanup(func() { w.close(); watch.close() })
	return w, raw, a
}

func TestAdmissionFirstUnitReleaseBeforeOutputAndNullContinuation(t *testing.T) {
	w, raw, a := admissionUnitFixture(t, LiveDecision{Decision: LiveAllow, Reason: LiveReasonAllowed})
	unit, err := w.BeginUnit()
	if err != nil {
		t.Fatal(err)
	}
	if raw.Body.Len() != 0 || a.calls != 1 {
		t.Fatal("claim emitted or authorized bytes")
	}
	if err := w.ReleaseUnit(unit, HostedUnit{Status: 200, MediaType: "application/json", Body: []byte("one")}); err != nil {
		t.Fatal(err)
	}
	if a.calls != 2 || a.request.Phase != PhaseRelease || a.request.UnitID != "unit-a" {
		t.Fatal("missing same-unit fresh RELEASE")
	}
	next, err := w.BeginUnit()
	if err != nil {
		t.Fatal(err)
	}
	if err := w.ReleaseUnit(next, HostedUnit{Status: 200, MediaType: "application/json", Body: []byte("two")}); err != errAdmissionRevisionUnavailable {
		t.Fatal("null next unit admitted")
	}
	if raw.Body.String() != "one" {
		t.Fatal("continuation content leaked")
	}
}

func TestAdmissionReleaseDenialDropsBufferedBody(t *testing.T) {
	for _, d := range []LiveDecision{{Decision: LiveDenied, Reason: LiveReasonDenied}, {Decision: LiveUnavailable, Reason: LiveReasonUnavailable}, {Decision: LiveAllow, Reason: LiveReasonAllowed, MembershipRevision: NewMembershipRevision([]byte("changed"))}} {
		w, raw, a := admissionUnitFixture(t, admissionAllow())
		unit, _ := w.BeginUnit()
		a.set(d)
		if w.ReleaseUnit(unit, HostedUnit{Status: 200, Body: []byte("buffered")}) == nil || raw.Body.Len() != 0 {
			t.Fatal("failed release emitted content")
		}
		if w.ReleaseUnit(unit, HostedUnit{Status: 200, Body: []byte("retry")}) == nil {
			t.Fatal("failed unit retried")
		}
	}
}

func TestAdmissionUnitReplayParallelReleaseAndForeignTicket(t *testing.T) {
	w, raw, _ := admissionUnitFixture(t, admissionAllow())
	unit, _ := w.BeginUnit()
	var wg sync.WaitGroup
	var mu sync.Mutex
	accepted := 0
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if w.ReleaseUnit(unit, HostedUnit{Status: 200, Body: []byte("once")}) == nil {
				mu.Lock()
				accepted++
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	if accepted != 1 || raw.Body.String() != "once" {
		t.Fatal("parallel ticket released more than once")
	}
	other, otherRaw, _ := admissionUnitFixture(t, admissionAllow())
	if other.ReleaseUnit(unit, HostedUnit{Status: 200, Body: []byte("foreign")}) == nil || otherRaw.Body.Len() != 0 {
		t.Fatal("different target/request ticket admitted")
	}
}

func TestAdmissionUnitBoundsZeroBodyAndEOFAreTerminal(t *testing.T) {
	w, raw, _ := admissionUnitFixture(t, admissionAllow())
	unit, _ := w.BeginUnit()
	if w.ReleaseUnit(unit, HostedUnit{Status: 200, Body: make([]byte, admissionUnitMaxBytes+1)}) == nil || raw.Body.Len() != 0 {
		t.Fatal("unbounded first unit accepted")
	}
	w2, _, a := admissionUnitFixture(t, LiveDecision{Decision: LiveAllow, Reason: LiveReasonAllowed})
	u2, _ := w2.BeginUnit()
	if w2.ReleaseUnit(u2, HostedUnit{Status: 204}) != nil || a.calls != 2 {
		t.Fatal("zero-body response bypassed RELEASE")
	}
	w2.close()
	if _, err := w2.BeginUnit(); err == nil {
		t.Fatal("EOF reopened delivery")
	}
}

func TestAdmissionReleaseBindingCannotChange(t *testing.T) {
	for _, mutate := range []func(*LiveAuthorizationRequest){
		func(r *LiveAuthorizationRequest) { r.Target = "other" },
		func(r *LiveAuthorizationRequest) { r.Action = ActionGetPrompt },
		func(r *LiveAuthorizationRequest) { r.Actor.PersonID = "other" },
		func(r *LiveAuthorizationRequest) { r.Scope.WorkspaceID = "other" },
		func(r *LiveAuthorizationRequest) { r.RequestID = "other" },
	} {
		w, raw, _ := admissionUnitFixture(t, admissionAllow())
		unit, _ := w.BeginUnit()
		mutate(&unit.binding)
		if w.ReleaseUnit(unit, HostedUnit{Status: 200, Body: []byte("wrong")}) == nil || raw.Body.Len() != 0 {
			t.Fatal("release binding changed")
		}
	}
}
