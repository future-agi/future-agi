package mcp

import (
	"context"
	"encoding/json"
	"net/http"
)

func (s *Server) admissionRequest(r *http.Request, method string, params json.RawMessage) (*HostedAdmission, LiveAuthorizationRequest, MembershipRevision, error) {
	fail := func(err error) (*HostedAdmission, LiveAuthorizationRequest, MembershipRevision, error) {
		return nil, LiveAuthorizationRequest{}, MembershipRevision{}, err
	}
	h, err := s.admissionReady()
	if err != nil {
		return fail(err)
	}
	identity, ok := r.Context().Value(admissionIdentityKey{}).(admissionIdentity)
	action, mapped := admissionAction(method)
	if !ok || !mapped {
		return fail(errAdmissionDenied)
	}
	selector := params
	var args map[string]string
	if action == ActionGetPrompt {
		selector, _, args, err = admissionPromptShape(params)
		if err != nil {
			return fail(err)
		}
		if h.Prompts == nil {
			return fail(errAdmissionUnavailable)
		}
	}
	ctx, cancel := context.WithTimeout(r.Context(), admissionLookupTimeout)
	target, err := h.Targets.ResolveTarget(ctx, identity.scope, identity.actor, action, method, selector)
	late := ctx.Err() != nil
	cancel()
	if err != nil || late {
		return fail(errAdmissionUnavailable)
	}
	if !admissionOpaque(target) {
		return fail(errAdmissionDenied)
	}
	if action == ActionGetPrompt {
		ctx, cancel := context.WithTimeout(r.Context(), admissionLookupTimeout)
		err := h.Prompts.ValidatePrompt(ctx, identity.scope, identity.actor, target, args)
		late := ctx.Err() != nil
		cancel()
		if late || err != nil {
			if !late && err == errAdmissionDenied {
				return fail(errAdmissionDenied)
			}
			return fail(errAdmissionUnavailable)
		}
	}
	requestID, err := admissionRandomID()
	if err != nil {
		return fail(err)
	}
	unitID, err := admissionRandomID()
	if err != nil {
		return fail(err)
	}
	a := LiveAuthorizationRequest{Scope: identity.scope, Actor: identity.actor, Action: action, Target: target, Phase: PhaseContinuation, RequestID: requestID, UnitID: unitID}
	original := MembershipRevision{}
	if method == "initialize" {
		// An existing delivery cannot request a fresh first-unit allowance.
		if r.Header.Get("MCP-Session-Id") != "" || r.Header.Get("Last-Event-ID") != "" {
			return fail(errAdmissionDenied)
		}
		a.Phase = PhaseInitial
	} else {
		// All session RPCs/pages/replays/notifications are CONTINUATION, even if
		// a client omits a cursor, changes its RPC id or asserts phase=INITIAL.
		rec, _, err := s.loadAdmissionRecord(r.Context(), r, a)
		if err != nil {
			return fail(err)
		}
		original = rec.Revision
	}
	revision, err := authorizeLive(r.Context(), s.admissionAuthorizer(h), a, original)
	return h, a, revision, err
}
