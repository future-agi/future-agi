package server

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// Approval admin routes are internal and admin-token authenticated. The
// approve_url is a staged console contract, not an exposed customer UI.
func (s *Server) registerMCPApprovalsRoutes(router *Router) {
	if s.mcp == nil {
		return
	}
	router.Handle("GET", "/-/mcp/approvals", func(w http.ResponseWriter, r *http.Request) {
		if !s.checkAdminAuth(w, r) {
			return
		}
		org := r.URL.Query().Get("org_id")
		if org == "" {
			writeApprovalAdmin(w, nil, mcp.ErrApprovalInvalid)
			return
		}
		service := s.mcp.ApprovalService()
		if service == nil {
			writeApprovalAdmin(w, nil, mcp.ErrApprovalUnavailable)
			return
		}
		rows, err := service.List(r.Context(), org, r.URL.Query().Get("status"))
		writeApprovalAdmin(w, map[string]any{"approvals": rows}, err)
	})
	router.Handle("POST", "/-/mcp/approvals/{approval_id}/decision", func(w http.ResponseWriter, r *http.Request) {
		if !s.checkAdminAuth(w, r) {
			return
		}
		var decision mcp.ApprovalDecision
		dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, 8192))
		dec.DisallowUnknownFields()
		if err := dec.Decode(&decision); err != nil || !decision.Valid() {
			writeApprovalAdmin(w, nil, mcp.ErrApprovalInvalid)
			return
		}
		var extra any
		if dec.Decode(&extra) != io.EOF {
			writeApprovalAdmin(w, nil, mcp.ErrApprovalInvalid)
			return
		}
		service := s.mcp.ApprovalService()
		if service == nil {
			writeApprovalAdmin(w, nil, mcp.ErrApprovalUnavailable)
			return
		}
		rec, err := service.Decide(r.Context(), r.URL.Query().Get("approval_id"), decision)
		writeApprovalAdmin(w, rec, err)
	})
	poll := func(w http.ResponseWriter, r *http.Request) {
		s.mcp.HandleApprovalPoll(w, r, r.URL.Query().Get("approval_id"))
	}
	router.Handle("GET", mcpEndpoint(s.cfg)+"/approvals/{approval_id}", poll)
	router.Handle("GET", mcpEndpoint(s.cfg)+"/v/{vs_id}/approvals/{approval_id}", virtualServerHandler(poll))
}
func writeApprovalAdmin(w http.ResponseWriter, body any, err error) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	if err != nil {
		status, code := http.StatusServiceUnavailable, "approval_unavailable"
		switch {
		case errors.Is(err, mcp.ErrApprovalNotFound):
			status, code = http.StatusNotFound, "approval_not_found"
		case errors.Is(err, mcp.ErrApprovalInvalid):
			status, code = http.StatusBadRequest, "invalid_approval_request"
		case errors.Is(err, mcp.ErrApprovalConflict):
			status, code = http.StatusConflict, "approval_not_pending"
		}
		w.WriteHeader(status)
		body = map[string]string{"error": code}
	}
	_ = json.NewEncoder(w).Encode(body)
}
