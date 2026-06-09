package server

import (
	"crypto/subtle"
	"log/slog"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/cluster"
)

// ClusterHandlers provides admin endpoints for cluster management.
type ClusterHandlers struct {
	manager    *cluster.Manager
	adminToken string
}

// NewClusterHandlers creates cluster admin handlers.
func NewClusterHandlers(manager *cluster.Manager, adminToken string) *ClusterHandlers {
	return &ClusterHandlers{
		manager:    manager,
		adminToken: adminToken,
	}
}

func (h *ClusterHandlers) requireAdmin(w http.ResponseWriter, r *http.Request) bool {
	if h.adminToken == "" {
		slog.Warn("admin request denied: no admin token configured")
		http.Error(w, `{"error":"admin token not configured"}`, http.StatusForbidden)
		return false
	}
	token := r.Header.Get("Authorization")
	expected := "Bearer " + h.adminToken
	if subtle.ConstantTimeCompare([]byte(token), []byte(expected)) != 1 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnauthorized)
		w.Write([]byte(`{"error":"unauthorized"}`))
		return false
	}
	return true
}

// ListNodes returns all active nodes in the cluster.
func (h *ClusterHandlers) ListNodes(w http.ResponseWriter, r *http.Request) {
	if !h.requireAdmin(w, r) {
		return
	}

	nodes := h.manager.ListNodes()

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"node_id":    h.manager.NodeID(),
		"node_count": len(nodes),
		"nodes":      nodes,
	})
}
