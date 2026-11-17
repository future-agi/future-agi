package mcp

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"
)

// HandleApprovalPoll authenticates the subresource against precisely its MCP
// resource, not the approval URL and not any other resource. Only this handler
// clones the URL for authentication; bearer verification is not broadened.
func (s *Server) HandleApprovalPoll(w http.ResponseWriter, r *http.Request, id string) {
	suffix := "/approvals/" + id
	if id == "" || len(id) > 4096 || !strings.HasSuffix(r.URL.Path, suffix) {
		writeApprovalHTTPError(w, ErrApprovalNotFound)
		return
	}
	resource := strings.TrimSuffix(r.URL.Path, suffix)
	if resource == "" {
		writeApprovalHTTPError(w, ErrApprovalNotFound)
		return
	}
	canonical := r.Clone(r.Context())
	canonical.URL.Path = resource
	canonical.URL.RawPath = ""
	req, ok := s.authenticateHTTP(w, canonical)
	if !ok {
		return
	}
	caller, ok := callerAuthFrom(req.Context())
	if !ok || caller.anonymous {
		writeApprovalHTTPError(w, ErrApprovalNotFound)
		return
	}
	cc := &CallContext{ctx: req.Context(), req: req, Principal: caller.principal}
	// Resolve the same agent identity as the MCP call before checking virtual
	// server access. Runtime claims can be the sole authorized agent selector.
	if d := parseAgent(s, cc); d != nil {
		writeApprovalHTTPError(w, ErrApprovalNotFound)
		return
	}
	if d := resolveTenant(s, cc); d != nil {
		writeApprovalHTTPError(w, ErrApprovalUnavailable)
		return
	}
	if d := resolveVirtualServer(s, cc); d != nil {
		writeApprovalHTTPError(w, ErrApprovalNotFound)
		return
	}
	service := s.ApprovalService()
	if service == nil {
		writeApprovalHTTPError(w, ErrApprovalUnavailable)
		return
	}
	rec, err := service.Poll(req.Context(), id, cc.Principal, cc.VirtualServer)
	if err != nil {
		writeApprovalHTTPError(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	_ = json.NewEncoder(w).Encode(rec)
}
func writeApprovalHTTPError(w http.ResponseWriter, err error) {
	status, code := http.StatusServiceUnavailable, "approval_unavailable"
	if errors.Is(err, ErrApprovalNotFound) {
		status, code = http.StatusNotFound, "approval_not_found"
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": code})
}
