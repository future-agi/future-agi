package server

import (
	"crypto/subtle"
	"encoding/json"
	"log/slog"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/rotation"
)

// RotationHandlers provides admin endpoints for provider key rotation.
type RotationHandlers struct {
	manager    *rotation.Manager
	adminToken string
}

// NewRotationHandlers creates rotation admin handlers.
func NewRotationHandlers(manager *rotation.Manager, adminToken string) *RotationHandlers {
	return &RotationHandlers{
		manager:    manager,
		adminToken: adminToken,
	}
}

func (h *RotationHandlers) requireAdmin(w http.ResponseWriter, r *http.Request) bool {
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
		w.Write([]byte(`{"error":"unauthorized - admin token required"}`))
		return false
	}
	return true
}

// StartRotation handles POST /-/admin/providers/{id}/rotate.
func (h *RotationHandlers) StartRotation(w http.ResponseWriter, r *http.Request) {
	if !h.requireAdmin(w, r) {
		return
	}

	providerID := r.URL.Query().Get("id")
	if providerID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "provider id required"})
		return
	}

	var req struct {
		NewKey string `json:"new_key"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON"})
		return
	}

	if err := h.manager.StartRotation(providerID, req.NewKey); err != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": err.Error()})
		return
	}

	state, _ := h.manager.GetStatus(providerID)
	writeJSON(w, http.StatusOK, state.MaskedState())
}

// GetRotationStatus handles GET /-/admin/providers/{id}/rotation.
func (h *RotationHandlers) GetRotationStatus(w http.ResponseWriter, r *http.Request) {
	if !h.requireAdmin(w, r) {
		return
	}

	providerID := r.URL.Query().Get("id")
	if providerID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "provider id required"})
		return
	}

	state, err := h.manager.GetStatus(providerID)
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, state.MaskedState())
}

// PromoteRotation handles POST /-/admin/providers/{id}/rotate/promote.
func (h *RotationHandlers) PromoteRotation(w http.ResponseWriter, r *http.Request) {
	if !h.requireAdmin(w, r) {
		return
	}

	providerID := r.URL.Query().Get("id")
	if providerID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "provider id required"})
		return
	}

	if err := h.manager.Promote(providerID); err != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": err.Error()})
		return
	}

	state, _ := h.manager.GetStatus(providerID)
	writeJSON(w, http.StatusOK, state.MaskedState())
}

// RollbackRotation handles POST /-/admin/providers/{id}/rotate/rollback.
func (h *RotationHandlers) RollbackRotation(w http.ResponseWriter, r *http.Request) {
	if !h.requireAdmin(w, r) {
		return
	}

	providerID := r.URL.Query().Get("id")
	if providerID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "provider id required"})
		return
	}

	if err := h.manager.Rollback(providerID); err != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": err.Error()})
		return
	}

	state, _ := h.manager.GetStatus(providerID)
	writeJSON(w, http.StatusOK, state.MaskedState())
}

func writeJSON(w http.ResponseWriter, status int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}
