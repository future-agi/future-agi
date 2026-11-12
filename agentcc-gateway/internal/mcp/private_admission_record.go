package mcp

import (
	"context"
	"encoding/json"
	"net/http"
)

// The authenticated stored record, not a client phase/header/cursor, supplies
// the original revision. Lookup is not authorization and never emits content.
// Version 3 is incompatible with old phase-less records; no silent migration.
func (s *Server) loadAdmissionRecord(ctx context.Context, r *http.Request, a LiveAuthorizationRequest) (admissionRecord, string, error) {
	empty := admissionRecord{}
	token := r.Header.Get("MCP-Session-Id")
	codec, err := s.sessions.codec()
	if err != nil {
		return empty, "", errAdmissionUnavailable
	}
	data, err := codec.Open(StatePurposeSession, token)
	if err != nil {
		return empty, "", errAdmissionDenied
	}
	var tok admissionToken
	if json.Unmarshal(data, &tok) != nil || tok.Version != 3 || tok.Scope != a.Scope {
		return empty, "", errAdmissionDenied
	}
	hash := SessionHash(token)
	ctx, cancel := context.WithTimeout(ctx, admissionLookupTimeout)
	defer cancel()
	data, ok, err := s.sessionStore().Load(ctx, admissionSessionKey(a.Scope, hash))
	if err != nil || ctx.Err() != nil {
		return empty, "", errAdmissionUnavailable
	}
	var rec admissionRecord
	if !ok || json.Unmarshal(data, &rec) != nil || !rec.matches(a, rec.Revision) || rec.Route != virtualServerOf(r) ||
		!rec.Authority.valid() || rec.Authority.Scope != rec.Scope || rec.Authority.Actor != rec.Actor || rec.Authority.Target != rec.Target || rec.Authority.Phase != PhaseInitial || rec.Authority.Action != ActionListResourceMetadata ||
		rec.LeaseRequest.Scope != a.Scope || rec.LeaseRequest.Actor != a.Actor || rec.LeaseRequest.SessionHash != hash || rec.LeaseRequest.Purpose != LeaseSession || !validAdmissionLease(rec.Lease, rec.LeaseRequest.ID) {
		return empty, "", errAdmissionDenied
	}
	return rec, hash, nil
}
