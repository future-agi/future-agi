package mcp

import (
	"context"
	"encoding/json"
	"strings"
)

// ApprovalDecision is MCPApprovalDecision. The admin HTTP gate, not actor,
// authenticates the request; actor/note are bounded decision metadata only.
type ApprovalDecision struct {
	OrgID    string `json:"org_id"`
	Decision string `json:"decision"`
	Actor    string `json:"actor"`
	Note     string `json:"note,omitempty"`
}

func (d ApprovalDecision) Valid() bool {
	return d.OrgID != "" && len(d.OrgID) <= 256 && !strings.ContainsAny(d.OrgID, "{}") && (d.Decision == "approve" || d.Decision == "deny") && strings.TrimSpace(d.Actor) != "" && len(d.Actor) <= 256 && len(d.Note) <= 1024
}
func (m *ApprovalManager) Decide(ctx context.Context, id string, d ApprovalDecision) (ApprovalRecord, error) {
	if !d.Valid() {
		return ApprovalRecord{}, ErrApprovalInvalid
	}
	for attempt := 0; attempt < 8; attempt++ {
		state, raw, key, err := m.load(ctx, d.OrgID, id)
		if err != nil {
			return ApprovalRecord{}, err
		}
		if m.public(state).Status != "pending" {
			return ApprovalRecord{}, ErrApprovalConflict
		}
		state.Record.Status = "approved"
		if d.Decision == "deny" {
			state.Record.Status = "denied"
		}
		now := m.now().UTC()
		state.Record.DecidedAt = &now
		state.Record.DecidedBy = d.Actor
		state.Record.Note = d.Note
		next, _ := json.Marshal(state)
		ok, err := m.store.CompareSwap(ctx, key, raw, next, state.Record.ExpiresAt)
		if err != nil {
			return ApprovalRecord{}, ErrApprovalUnavailable
		}
		if ok {
			return state.Record, nil
		}
	}
	return ApprovalRecord{}, ErrApprovalConflict
}
func (m *ApprovalManager) List(ctx context.Context, org, status string) ([]ApprovalRecord, error) {
	if org == "" || len(org) > 256 || strings.ContainsAny(org, "{}") {
		return nil, ErrApprovalInvalid
	}
	switch status {
	case "", "pending", "approved", "denied", "expired", "used":
	default:
		return nil, ErrApprovalInvalid
	}
	if m == nil || m.store == nil {
		return nil, ErrApprovalUnavailable
	}
	// Keep the existing response bound, but apply it after status selection.
	// Bounded keyset pages avoid one unbounded Redis script and keep an older
	// pending approval reachable behind any number of later terminal rows.
	const limit = 1000
	out := []ApprovalRecord{}
	cursor := ""
	for {
		if ctx.Err() != nil {
			return nil, ErrApprovalUnavailable
		}
		rows, next, err := m.store.ListPage(ctx, approvalIndex(org), cursor, limit)
		if err != nil {
			return nil, ErrApprovalUnavailable
		}
		for _, raw := range rows {
			var state approvalState
			if json.Unmarshal(raw, &state) != nil || state.Record.OrgID != org {
				return nil, ErrApprovalUnavailable
			}
			rec := m.public(state)
			if status == "" || rec.Status == status {
				out = append(out, rec)
				if len(out) == limit {
					return out, nil
				}
			}
		}
		if next == "" {
			return out, nil
		}
		if next == cursor {
			return nil, ErrApprovalUnavailable
		}
		cursor = next // Even a page empty after filtering must advance.
	}
}
func (m *ApprovalManager) Poll(ctx context.Context, id string, p Principal, vs string) (ApprovalRecord, error) {
	if !validApprovalPrincipal(p) {
		return ApprovalRecord{}, ErrApprovalNotFound
	}
	state, _, _, err := m.load(ctx, p.OrgID, id)
	if err != nil {
		return ApprovalRecord{}, err
	}
	if state.Owner != approvalOwner(p) || state.Record.VirtualServerID != vs {
		return ApprovalRecord{}, ErrApprovalNotFound
	}
	return m.public(state), nil
}

// Late-bound admin/poll seam: the executable installs one provider shared by
// hooks and routes, even when its Redis client is unavailable.
type ApprovalService interface {
	Approvals
	List(context.Context, string, string) ([]ApprovalRecord, error)
	Decide(context.Context, string, ApprovalDecision) (ApprovalRecord, error)
	Poll(context.Context, string, Principal, string) (ApprovalRecord, error)
}

func (s *Server) ApprovalService() ApprovalService {
	service, _ := s.Deps().Approvals.(ApprovalService)
	return service
}
