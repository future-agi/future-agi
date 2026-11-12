package mcp

import (
	"context"
	"errors"
	"time"
)

const (
	admissionLookupTimeout = time.Second
	admissionTick          = time.Second
	admissionWriteTimeout  = 2 * time.Second
)

var (
	errAdmissionDenied              = errors.New("mcp: admission denied")
	errAdmissionUnavailable         = errors.New("mcp: admission unavailable")
	errAdmissionRevisionUnavailable = errors.New("mcp: admission unavailable: REVISION_UNAVAILABLE")
	errAdmissionLimit               = errors.New("mcp: admission capacity unavailable")
)

// ScopeKey is a real organization/workspace pair, never a synthetic org ID.
type ScopeKey struct{ OrgID, WorkspaceID string }

// AdmissionActor contains authenticated identity, never client roles/scopes.
type AdmissionActor struct {
	Kind                      PrincipalKind
	PersonID, KeyID, ClientID string
}

type AdmissionAction string

const (
	ActionListResourceMetadata AdmissionAction = "LIST_RESOURCE_METADATA"
	ActionListPromptMetadata   AdmissionAction = "LIST_PROMPT_METADATA"
	ActionReadResource         AdmissionAction = "RESOURCE_CONTENT_READ"
	ActionGetPrompt            AdmissionAction = "PROMPT_GET_RENDER"
	ActionManageContent        AdmissionAction = "MANAGE_CONTENT_CONFIGURATION"
	ActionInvokeTool           AdmissionAction = "TOOL_INVOKE"
)

type AdmissionPhase string

const (
	PhaseInitial      AdmissionPhase = "INITIAL"
	PhaseRelease      AdmissionPhase = "RELEASE"
	PhaseContinuation AdmissionPhase = "CONTINUATION"
)

type LiveDecisionKind string

const (
	LiveAllow       LiveDecisionKind = "ALLOW"
	LiveDenied      LiveDecisionKind = "DENIED"
	LiveUnavailable LiveDecisionKind = "UNAVAILABLE"
)

// Closed caller-side classes, not an assertion about unaccepted accounts DTOs.
// A future adapter must map canonical reasons explicitly; unknown reasons fail.
type LiveReason string

const (
	LiveReasonAllowed             LiveReason = "ALLOWED"
	LiveReasonDenied              LiveReason = "DENIED"
	LiveReasonUnavailable         LiveReason = "UNAVAILABLE"
	LiveReasonRevisionUnavailable LiveReason = "REVISION_UNAVAILABLE"
)

// Internal caller contract only. IDs are gateway-generated, not JSON-RPC IDs,
// client cursors or idempotency headers. Target is a scope-resolved opaque handle.
// No argument values, source addresses, roles or per-key capability map belong here.
type LiveAuthorizationRequest struct {
	Scope             ScopeKey
	Actor             AdmissionActor
	Action            AdmissionAction
	Target            string
	Phase             AdmissionPhase
	RequestID, UnitID string
}

type LiveDecision struct {
	Decision           LiveDecisionKind
	Reason             LiveReason
	MembershipRevision MembershipRevision
}

// LiveAuthorizer does complete fresh DB authorization on EVERY call, honoring
// the deadline. Main owns role decisions, workspace/server ownership, person-key
// policy and durable generation. No positive cache or gateway-local role map.
// Metadata authority cannot authorize resource/prompt/tool payload retrieval.
// TOOL_INVOKE requires the target's approved policy; a generic invocation denies.
type LiveAuthorizer interface {
	Authorize(context.Context, LiveAuthorizationRequest) LiveDecision
}

func (s ScopeKey) valid() bool      { return admissionOpaque(s.OrgID) && admissionOpaque(s.WorkspaceID) }
func admissionOpaque(s string) bool { return s != "" && len(s) <= 512 }
func (a AdmissionActor) valid() bool {
	if !admissionOpaque(a.PersonID) {
		return false
	}
	switch a.Kind {
	case PrincipalKey:
		return admissionOpaque(a.KeyID)
	case PrincipalOAuth:
		return admissionOpaque(a.ClientID)
	default:
		return false
	}
}
func (a AdmissionAction) valid() bool {
	switch a {
	case ActionListResourceMetadata, ActionListPromptMetadata, ActionReadResource, ActionGetPrompt, ActionManageContent, ActionInvokeTool:
		return true
	default:
		return false
	}
}
func (r LiveAuthorizationRequest) valid() bool {
	phase := r.Phase == PhaseInitial || r.Phase == PhaseRelease || r.Phase == PhaseContinuation
	return r.Scope.valid() && r.Actor.valid() && r.Action.valid() && admissionOpaque(r.Target) && phase && admissionOpaque(r.RequestID) && admissionOpaque(r.UnitID)
}

func authorizeLive(ctx context.Context, a LiveAuthorizer, r LiveAuthorizationRequest, revision MembershipRevision) (MembershipRevision, error) {
	empty := MembershipRevision{}
	if !r.valid() {
		return empty, errAdmissionDenied
	}
	if a == nil {
		return empty, errAdmissionUnavailable
	}
	ctx, cancel := context.WithTimeout(ctx, admissionLookupTimeout)
	defer cancel()
	if ctx.Err() != nil {
		return empty, errAdmissionUnavailable
	}
	d := a.Authorize(ctx, r)
	if ctx.Err() != nil {
		return empty, errAdmissionUnavailable
	}
	switch d.Decision {
	case LiveAllow:
		if d.Reason != LiveReasonAllowed {
			return empty, errAdmissionUnavailable
		}
		if r.Phase == PhaseContinuation && (!revision.present || !d.MembershipRevision.present) {
			return empty, errAdmissionRevisionUnavailable
		}
		if revision.present && revision != d.MembershipRevision {
			return empty, errAdmissionDenied
		}
		return d.MembershipRevision, nil
	case LiveDenied:
		if d.Reason == LiveReasonDenied {
			return empty, errAdmissionDenied
		}
	case LiveUnavailable:
		if d.Reason == LiveReasonRevisionUnavailable {
			return empty, errAdmissionRevisionUnavailable
		}
	}
	return empty, errAdmissionUnavailable
}

// Lifecycle uses resource inventory admission. Prompt inventory/events always
// receive their distinct action. Unmapped methods never borrow metadata access.
func admissionAction(method string) (AdmissionAction, bool) {
	switch method {
	case "initialize", "ping", "resources/list", "session/resume", "session/delete", "notifications/initialized":
		return ActionListResourceMetadata, true
	case "prompts/list":
		return ActionListPromptMetadata, true
	case "resources/read":
		return ActionReadResource, true
	case "prompts/get":
		return ActionGetPrompt, true
	case "tools/call":
		return ActionInvokeTool, true
	default:
		return "", false
	}
}
