package mcp

import (
	"context"
	"encoding/json"
	"net/http"
	"sync"
	"time"
)

// AdmissionScopeResolver resolves a workspace from trusted server-side routing
// and the verified person identity. No request header, role or scope claim may
// itself establish workspace membership. Unknown or foreign scope is refused.
type AdmissionScopeResolver interface {
	ResolveScope(context.Context, Principal) (ScopeKey, error)
}

// AdmissionTargetResolver resolves client selectors within scope to opaque
// handles. It must never perform a global lookup followed by an ownership test.
// Empty Target is not a wildcard. The returned target remains only a selector;
// the LiveAuthorizer makes the authoritative permission decision.
type AdmissionTargetResolver interface {
	ResolveTarget(context.Context, ScopeKey, AdmissionActor, AdmissionAction, string, json.RawMessage) (string, error)
}

// HostedMCPRuntime is a separately qualified workspace-aware protocol runtime.
// It must use workspace-bound pools, credential keys and cache namespaces and
// honor request cancellation. Dispatch must not call the legacy org-only chain.
// Only already-admitted application RPCs arrive here, not session lifecycle RPCs.
// It must not retain the sink or launch unjoined writes after Dispatch returns.
// Each complete bounded page/chunk/event/RPC must claim a separate sink unit;
// transport segmentation is NOT a unit boundary. It must never retrieve any
// prompt/resource/tool payload under either metadata action. The caller routes
// metadata separately so that an implementation cannot silently reuse its
// generic content dispatcher. No legacy or org-only runtime implements this.
type HostedMCPRuntime interface {
	ListMetadata(HostedUnitSink, *http.Request, *Message, LiveAuthorizationRequest)
	DispatchContent(HostedUnitSink, *http.Request, *Message, LiveAuthorizationRequest)
}

// HostedAdmission selects the strict path even while Enabled is false. A nil
// dependency means the separate legacy self-host path, NOT hosted permission.
// Wiring must install a non-nil value for every hosted MCP surface before any
// hosted feature may be enabled. Install immutable configuration before serving.
// No production adapter or hosted activation is supplied by this package.
type HostedAdmission struct {
	Enabled      bool
	Scopes       AdmissionScopeResolver
	Targets      AdmissionTargetResolver
	Runtime      HostedMCPRuntime
	Prompts      AdmissionPromptValidator
	Limits       AdmissionLimits
	mu           sync.Mutex
	sessions     map[string]*admissionSession
	counts       map[LeasePurpose]int
	ingress      map[string]int
	ingressCount int
}

// Process ceilings are required venue inputs. They bound local work but are
// not a substitute for SessionLeaseStore's atomic cross-replica tenant quotas.
type AdmissionLimits struct {
	ProcessSessions, ProcessCalls, ProcessStreams         int
	SessionsPerOrg, SessionsPerWorkspace                  int
	CallsPerOrg, CallsPerWorkspace                        int
	StreamsPerOrg, StreamsPerWorkspace, StreamsPerSession int
	Idle, Absolute                                        time.Duration
}

func (l AdmissionLimits) valid() bool {
	return l.ProcessSessions > l.SessionsPerOrg && l.ProcessCalls > l.CallsPerOrg && l.ProcessStreams > l.StreamsPerOrg &&
		l.SessionsPerOrg > 0 && l.SessionsPerOrg <= 16 && l.SessionsPerWorkspace > 0 && l.SessionsPerWorkspace <= min(l.SessionsPerOrg, 8) &&
		l.CallsPerOrg > 0 && l.CallsPerOrg <= 8 && l.CallsPerWorkspace > 0 && l.CallsPerWorkspace <= l.CallsPerOrg &&
		l.StreamsPerOrg > 0 && l.StreamsPerWorkspace > 0 && l.StreamsPerWorkspace <= l.StreamsPerOrg && l.StreamsPerSession > 0 && l.StreamsPerSession <= 2 &&
		l.Idle > 0 && l.Idle <= 300*time.Second && l.Absolute >= l.Idle && l.Absolute <= 1800*time.Second
}
func (h *HostedAdmission) reserve(p LeasePurpose) (func(), error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	limit := h.Limits.ProcessSessions
	if p == LeaseCall {
		limit = h.Limits.ProcessCalls
	}
	if p == LeaseStream {
		limit = h.Limits.ProcessStreams
	}
	if h.counts == nil {
		h.counts = map[LeasePurpose]int{}
	}
	if limit <= 0 || h.counts[p] >= limit {
		return nil, errAdmissionLimit
	}
	h.counts[p]++
	var once sync.Once
	return func() { once.Do(func() { h.mu.Lock(); h.counts[p]--; h.mu.Unlock() }) }, nil
}
func (h *HostedAdmission) leaseLimits(p LeasePurpose) LeaseLimits {
	l := h.Limits
	switch p {
	case LeaseSession:
		return LeaseLimits{Org: l.SessionsPerOrg, Workspace: l.SessionsPerWorkspace}
	case LeaseCall:
		return LeaseLimits{Org: l.CallsPerOrg, Workspace: l.CallsPerWorkspace}
	default:
		return LeaseLimits{Org: l.StreamsPerOrg, Workspace: l.StreamsPerWorkspace, Session: l.StreamsPerSession}
	}
}
func (s *Server) admissionReady() (*HostedAdmission, error) {
	select {
	case <-s.sessions.stopCh:
		return nil, errAdmissionUnavailable
	default:
	}
	d := s.Deps()
	h := d.HostedAdmission
	if h == nil || !h.Enabled || !h.Limits.valid() || h.Scopes == nil || h.Targets == nil || h.Runtime == nil || d.LiveAuthorizer == nil || d.SessionLeases == nil {
		return nil, errAdmissionUnavailable
	}
	if s.sessions.options().Codec == nil {
		return nil, errAdmissionUnavailable
	}
	if _, ok := s.sessionStore().(SessionRefresher); !ok {
		return nil, errAdmissionUnavailable
	}
	return h, nil
}
func (s *Server) admissionAuthorizer(h *HostedAdmission) LiveAuthorizer {
	current, err := s.admissionReady()
	if err != nil || current != h {
		return nil
	}
	return s.Deps().LiveAuthorizer
}

// reserveIngress bounds work before body allocation and authority lookup. A
// single authenticated org cannot occupy every local preflight slot. These
// counters are only local protection, never advertised as distributed quotas.
func (h *HostedAdmission) reserveIngress(org string) (func(), error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	limit := max(h.Limits.ProcessCalls, h.Limits.ProcessStreams)
	if limit < 2 || h.ingressCount >= limit || h.ingress[org] >= limit/2 {
		return nil, errAdmissionLimit
	}
	if h.ingress == nil {
		h.ingress = map[string]int{}
	}
	h.ingress[org]++
	h.ingressCount++
	var once sync.Once
	return func() {
		once.Do(func() {
			h.mu.Lock()
			defer h.mu.Unlock()
			h.ingressCount--
			h.ingress[org]--
			if h.ingress[org] == 0 {
				delete(h.ingress, org)
			}
		})
	}, nil
}
func releaseAdmissionIngress(r *http.Request) {
	if a, ok := r.Context().Value(admissionIdentityKey{}).(admissionIdentity); ok && a.release != nil {
		a.release()
	}
}

type admissionIdentity struct {
	scope   ScopeKey
	actor   AdmissionActor
	release func()
}
type admissionIdentityKey struct{}
type admissionRouteKey struct{}

func admissionRoute(r *http.Request) *http.Request {
	return r.WithContext(context.WithValue(r.Context(), admissionRouteKey{}, true))
}
func (s *Server) hostedAdmissionRequired() bool {
	return s.sessions.hosted.Load() || s.Deps().HostedAdmission != nil
}
func (s *Server) admissionAuthenticate(w http.ResponseWriter, r *http.Request) (*http.Request, bool) {
	if !s.hostedAdmissionRequired() {
		return r, true
	}
	if routed, _ := r.Context().Value(admissionRouteKey{}).(bool); !routed {
		writeAdmissionRefusal(w, errAdmissionUnavailable)
		return nil, false
	}
	h, err := s.admissionReady()
	if err != nil {
		writeAdmissionRefusal(w, err)
		return nil, false
	}
	p := principalOfRequest(r)
	a := AdmissionActor{Kind: p.Kind, PersonID: p.UserID, KeyID: p.KeyID, ClientID: p.ClientID}
	if !a.valid() || !admissionOpaque(p.OrgID) {
		writeAdmissionRefusal(w, errAdmissionDenied)
		return nil, false
	}
	release, err := h.reserveIngress(p.OrgID)
	if err != nil {
		writeAdmissionRefusal(w, err)
		return nil, false
	}
	keep := false
	defer func() {
		if !keep {
			release()
		}
	}()
	ctx, cancel := context.WithTimeout(r.Context(), admissionLookupTimeout)
	defer cancel()
	scope, err := h.Scopes.ResolveScope(ctx, p)
	if err != nil || ctx.Err() != nil {
		writeAdmissionRefusal(w, errAdmissionUnavailable)
		return nil, false
	}
	if !scope.valid() || scope.OrgID != p.OrgID {
		writeAdmissionRefusal(w, errAdmissionDenied)
		return nil, false
	}
	keep = true
	return r.WithContext(context.WithValue(r.Context(), admissionIdentityKey{}, admissionIdentity{scope: scope, actor: a, release: release})), true
}
func writeAdmissionRefusal(w http.ResponseWriter, err error) {
	status, code := http.StatusServiceUnavailable, "admission_unavailable"
	if err == errAdmissionDenied {
		status, code = http.StatusNotFound, "admission_denied"
	}
	if err == errAdmissionLimit {
		status, code = http.StatusTooManyRequests, "admission_capacity"
	}
	writeHTTPRefusal(w, status, code, "MCP request unavailable")
}
