package mcp

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func admissionSessionRequest(t *testing.T, s *Server, h *HostedAdmission, revision MembershipRevision) *http.Request {
	t.Helper()
	token, sess, err := s.createAdmissionSession(context.Background(), h, admissionTestRequest(), revision, "", "2025-03-26")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.endAdmissionSession(context.Background(), sess); sess.watch.close() })
	w := &admissionRecordingWriter{ResponseRecorder: httptest.NewRecorder()}
	r, ok := s.admissionAuthenticate(w, admissionTrustedRequest(http.MethodPost))
	if !ok {
		t.Fatal("trusted identity refused")
	}
	t.Cleanup(func() { releaseAdmissionIngress(r) })
	r.Header.Set("MCP-Session-Id", token)
	r.Header.Set("Accept", "application/json, text/event-stream")
	return r
}

func TestAdmissionNullSessionCannotRestartAsInitial(t *testing.T) {
	s, h, _, a := admissionTestServer(t)
	a.set(LiveDecision{Decision: LiveAllow, Reason: LiveReasonAllowed})
	r := admissionSessionRequest(t, s, h, MembershipRevision{})
	r.Header.Set("X-MCP-Phase", "INITIAL")
	for _, method := range []string{"resources/list", "prompts/list", "resources/read", "tools/call", "session/resume", "session/delete", "ping"} {
		if _, _, _, err := s.admissionRequest(r, method, nil); err != errAdmissionRevisionUnavailable {
			t.Fatal("resumed null session obtained new first-unit allowance")
		}
	}
	if _, _, _, err := s.admissionRequest(r, "initialize", nil); err != errAdmissionDenied {
		t.Fatal("existing session reinitialized")
	}
	// Landing a generation cannot promote an old null session's binding.
	a.set(admissionAllow())
	if _, _, _, err := s.admissionRequest(r, "resources/list", nil); err != errAdmissionRevisionUnavailable {
		t.Fatal("original null revision replaced")
	}
}

func TestAdmissionResumedRPCUsesContinuationAndOriginalRevision(t *testing.T) {
	s, h, _, auth := admissionTestServer(t)
	r := admissionSessionRequest(t, s, h, admissionAllow().MembershipRevision)
	for _, method := range []string{"resources/list", "prompts/list", "resources/read", "tools/call", "session/resume"} {
		_, a, revision, err := s.admissionRequest(r, method, json.RawMessage(`{"phase":"INITIAL","unit":"client","cursor":"client"}`))
		if err != nil || a.Phase != PhaseContinuation || a.RequestID == "client" || a.UnitID == "client" || revision != admissionAllow().MembershipRevision {
			t.Fatal("client state reclassified continuation")
		}
	}
	auth.set(LiveDecision{Decision: LiveAllow, Reason: LiveReasonAllowed, MembershipRevision: NewMembershipRevision([]byte("changed"))})
	if _, _, _, err := s.admissionRequest(r, "resources/list", nil); err != errAdmissionDenied {
		t.Fatal("changed stored revision accepted")
	}
}

func TestAdmissionInitializeNullReleaseAndFailureDropToken(t *testing.T) {
	for _, denyRelease := range []bool{false, true} {
		s, h, leases, _ := admissionTestServer(t)
		a := admissionAuthorizerFunc(func(_ context.Context, r LiveAuthorizationRequest) LiveDecision {
			if denyRelease && r.Phase == PhaseRelease {
				return LiveDecision{Decision: LiveDenied, Reason: LiveReasonDenied}
			}
			return LiveDecision{Decision: LiveAllow, Reason: LiveReasonAllowed}
		})
		s.SetDeps(Deps{HostedAdmission: h, SessionLeases: leases, LiveAuthorizer: a})
		w := &admissionRecordingWriter{ResponseRecorder: httptest.NewRecorder()}
		r, ok := s.admissionAuthenticate(w, admissionTrustedRequest(http.MethodPost))
		if !ok {
			t.Fatal("authentication failed")
		}
		r.Header.Set("Accept", "application/json, text/event-stream")
		s.handleAdmittedPost(w, r, &Message{JSONRPC: "2.0", ID: json.RawMessage("1"), Method: "initialize", Params: json.RawMessage(`{"protocolVersion":"2025-03-26"}`)})
		releaseAdmissionIngress(r)
		if denyRelease {
			if w.Body.Len() != 0 || w.Header().Get("MCP-Session-Id") != "" {
				t.Fatal("token or body escaped before RELEASE")
			}
		} else if w.Body.Len() == 0 || w.Header().Get("MCP-Session-Id") == "" {
			t.Fatal("null first bounded initialization refused")
		}
	}
}

type admissionAuthorizerFunc func(context.Context, LiveAuthorizationRequest) LiveDecision

func (f admissionAuthorizerFunc) Authorize(ctx context.Context, r LiveAuthorizationRequest) LiveDecision {
	return f(ctx, r)
}

func TestAdmissionMetadataEventsUseSeparateScopedActions(t *testing.T) {
	s, h, leases, _ := admissionTestServer(t)
	r := admissionSessionRequest(t, s, h, admissionAllow().MembershipRevision)
	s.SetDeps(Deps{HostedAdmission: h, SessionLeases: leases, LiveAuthorizer: admissionAuthorizerFunc(func(_ context.Context, a LiveAuthorizationRequest) LiveDecision {
		if a.Scope != admissionTestRequest().Scope || a.Phase != PhaseContinuation {
			return LiveDecision{}
		}
		if a.Action == ActionListPromptMetadata {
			return LiveDecision{Decision: LiveDenied, Reason: LiveReasonDenied}
		}
		return admissionAllow()
	})})
	allowed := &admissionRecordingWriter{ResponseRecorder: httptest.NewRecorder()}
	if s.releaseAdmissionEvent(allowed, r, h, admissionAllow().MembershipRevision, "0-1", []byte(`{"jsonrpc":"2.0","method":"notifications/resources/list_changed"}`)) != nil {
		t.Fatal("resource metadata refused")
	}
	denied := &admissionRecordingWriter{ResponseRecorder: httptest.NewRecorder()}
	if s.releaseAdmissionEvent(denied, r, h, admissionAllow().MembershipRevision, "0-2", []byte(`{"jsonrpc":"2.0","method":"notifications/prompts/list_changed"}`)) != errAdmissionDenied || denied.Body.Len() != 0 {
		t.Fatal("prompt event borrowed resource authority")
	}
}
