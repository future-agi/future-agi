package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type admissionTestScopes struct {
	scope ScopeKey
	err   error
}

func (f admissionTestScopes) ResolveScope(context.Context, Principal) (ScopeKey, error) {
	return f.scope, f.err
}

type admissionTestTargets struct{}

func (admissionTestTargets) ResolveTarget(_ context.Context, _ ScopeKey, _ AdmissionActor, _ AdmissionAction, _ string, _ json.RawMessage) (string, error) {
	return "opaque-a", nil
}

type admissionTestRuntime struct{ calls int }

func (r *admissionTestRuntime) ListMetadata(w HostedUnitSink, _ *http.Request, _ *Message, _ LiveAuthorizationRequest) {
	r.calls++
	unit, err := w.BeginUnit()
	if err == nil {
		_ = w.ReleaseUnit(unit, HostedUnit{Status: 200, MediaType: "application/json", Body: []byte(`{}`)})
	}
}
func (r *admissionTestRuntime) DispatchContent(w HostedUnitSink, req *http.Request, msg *Message, a LiveAuthorizationRequest) {
	r.ListMetadata(w, req, msg, a)
}
func admissionTestLimits() AdmissionLimits {
	return AdmissionLimits{ProcessSessions: 8, ProcessCalls: 8, ProcessStreams: 16, SessionsPerOrg: 4, SessionsPerWorkspace: 2, CallsPerOrg: 4, CallsPerWorkspace: 2, StreamsPerOrg: 8, StreamsPerWorkspace: 4, StreamsPerSession: 2, Idle: 5 * time.Second, Absolute: 10 * time.Second}
}
func admissionTestServer(t *testing.T) (*Server, *HostedAdmission, *admissionFakeLeases, *admissionTestAuthorizer) {
	t.Helper()
	s := NewServer(ServerConfig{})
	t.Cleanup(s.Close)
	codec, err := NewStateCodec(nil, true)
	if err != nil {
		t.Fatal(err)
	}
	s.ConfigureSessions(SessionOptions{Codec: codec})
	request := admissionTestRequest()
	f := newAdmissionFakeLeases()
	a := &admissionTestAuthorizer{decision: admissionAllow()}
	h := &HostedAdmission{Enabled: true, Scopes: admissionTestScopes{scope: request.Scope}, Targets: admissionTestTargets{}, Runtime: &admissionTestRuntime{}, Limits: admissionTestLimits()}
	s.SetDeps(Deps{HostedAdmission: h, LiveAuthorizer: a, SessionLeases: f})
	return s, h, f, a
}
func admissionTrustedRequest(method string) *http.Request {
	r := httptest.NewRequest(method, "/mcp", nil)
	p := Principal{Kind: PrincipalKey, OrgID: "org-a", UserID: "person-a", KeyID: "key-a", Scopes: []string{"untrusted-admin"}}
	return admissionRoute(r.WithContext(context.WithValue(r.Context(), sessionPrincipalKey{}, p)))
}
func TestAdmissionHostedSelectionCannotFallBack(t *testing.T) {
	s := NewServer(ServerConfig{})
	defer s.Close()
	s.SetDeps(Deps{HostedAdmission: &HostedAdmission{}})
	for _, deps := range []Deps{{HostedAdmission: &HostedAdmission{}}, {}} {
		s.SetDeps(deps)
		for _, handler := range []func(http.ResponseWriter, *http.Request){s.HandlePost, s.HandleSSE, s.HandleDelete} {
			w := httptest.NewRecorder()
			handler(w, httptest.NewRequest(http.MethodPost, "/mcp", strings.NewReader("not JSON")))
			if w.Code != http.StatusServiceUnavailable {
				t.Fatal("hosted dependency loss selected legacy")
			}
		}
	}
}
func TestAdmissionScopeResolverAndUnqualifiedRoutesFailClosed(t *testing.T) {
	s, h, _, _ := admissionTestServer(t)
	w := httptest.NewRecorder()
	if _, ok := s.admissionAuthenticate(w, httptest.NewRequest(http.MethodGet, "/mcp", nil)); ok {
		t.Fatal("unqualified route accepted")
	}
	for _, scope := range []admissionTestScopes{{scope: ScopeKey{OrgID: "foreign", WorkspaceID: "workspace-a"}}, {err: errors.New("private lookup failure")}} {
		h.Scopes = scope
		w := httptest.NewRecorder()
		if _, ok := s.admissionAuthenticate(w, admissionTrustedRequest(http.MethodPost)); ok {
			t.Fatal("invalid workspace accepted")
		}
		if strings.Contains(w.Body.String(), "private") || strings.Contains(w.Body.String(), "foreign") {
			t.Fatal("private value escaped")
		}
	}
}
func TestAdmissionActualSessionResumeAndSiblingReplay(t *testing.T) {
	s, h, _, _ := admissionTestServer(t)
	a := admissionTestRequest()
	token, sess, err := s.createAdmissionSession(context.Background(), h, a, NewMembershipRevision([]byte("revision-a")), "", "2025-03-26")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.endAdmissionSession(context.Background(), sess); sess.watch.close() })
	r := httptest.NewRequest(http.MethodGet, "/mcp", nil)
	r.Header.Set("MCP-Session-Id", token)
	if _, err := s.resumeAdmissionSession(r.Context(), h, r, a, NewMembershipRevision([]byte("revision-a"))); err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"org", "workspace", "person", "key", "revision"} {
		other := a
		revision := NewMembershipRevision([]byte("revision-a"))
		switch field {
		case "org":
			other.Scope.OrgID = "org-b"
		case "workspace":
			other.Scope.WorkspaceID = "workspace-b"
		case "person":
			other.Actor.PersonID = "person-b"
		case "key":
			other.Actor.KeyID = "key-b"
		case "revision":
			revision = NewMembershipRevision([]byte("revision-b"))
		}
		if _, err := s.resumeAdmissionSession(r.Context(), h, r, other, revision); err != errAdmissionDenied {
			t.Fatal("foreign session did not receive uniform refusal")
		}
	}
	r.Header.Set("MCP-Session-Id", "missing")
	if _, err := s.resumeAdmissionSession(r.Context(), h, r, a, NewMembershipRevision([]byte("revision-a"))); err != errAdmissionDenied {
		t.Fatal("missing session differed from foreign")
	}
	if sess.watch.ctx.Err() != nil {
		t.Fatal("foreign requests cancelled owner")
	}
}

type admissionFailSave struct{ *memorySessionStore }

func (admissionFailSave) Save(context.Context, string, []byte, time.Duration) error {
	return errors.New("private backend failure")
}
func TestAdmissionFailedInitializationReleasesLease(t *testing.T) {
	s, h, f, a := admissionTestServer(t)
	s.SetDeps(Deps{HostedAdmission: h, LiveAuthorizer: a, SessionLeases: f, SessionStore: admissionFailSave{newMemorySessionStore()}})
	if _, _, err := s.createAdmissionSession(context.Background(), h, admissionTestRequest(), NewMembershipRevision([]byte("revision-a")), "", "2025-03-26"); err != errAdmissionUnavailable {
		t.Fatal("save failure accepted")
	}
	f.mu.Lock()
	count := len(f.rows)
	f.mu.Unlock()
	if count != 0 {
		t.Fatal("failed initialization leaked lease")
	}
	h.mu.Lock()
	count = h.counts[LeaseSession]
	h.mu.Unlock()
	if count != 0 {
		t.Fatal("failed initialization leaked worker")
	}
}
func TestAdmissionLegacyRecordCannotResumeHosted(t *testing.T) {
	s, h, _, _ := admissionTestServer(t)
	b := sessionBinding{Kind: PrincipalKey, OrgID: "org-a", UserID: "person-a", KeyID: "key-a"}
	legacy, err := s.sessions.create(context.Background(), s.sessionStore(), b, "2025-03-26", Implementation{}, ClientCapabilities{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	r := httptest.NewRequest(http.MethodGet, "/mcp", nil)
	r.Header.Set("MCP-Session-Id", legacy.ID)
	if _, err := s.resumeAdmissionSession(r.Context(), h, r, admissionTestRequest(), NewMembershipRevision([]byte("revision-a"))); err != errAdmissionDenied {
		t.Fatal("legacy org-only session was upgraded")
	}
}
