package server

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// KeyRevocationPublisher can broadcast key revocations to other replicas.
type KeyRevocationPublisher interface {
	Publish(ctx context.Context, keyID string)
}

// KeyHandlers provides admin CRUD for API keys.
type KeyHandlers struct {
	keyStore            *auth.KeyStore
	adminToken          string
	revocationPublisher KeyRevocationPublisher // nil = single-instance mode
}

// NewKeyHandlers creates key admin handlers.
func NewKeyHandlers(keyStore *auth.KeyStore, adminToken string) *KeyHandlers {
	return &KeyHandlers{
		keyStore:   keyStore,
		adminToken: adminToken,
	}
}

// SetRevocationPublisher attaches a pub/sub broadcaster for multi-replica revocation.
func (h *KeyHandlers) SetRevocationPublisher(pub KeyRevocationPublisher) {
	h.revocationPublisher = pub
}

func (h *KeyHandlers) requireAdmin(w http.ResponseWriter, r *http.Request) bool {
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

// ListKeys handles GET /-/keys.
func (h *KeyHandlers) ListKeys(w http.ResponseWriter, r *http.Request) {
	if !h.requireAdmin(w, r) {
		return
	}

	keys := h.keyStore.List()

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"object": "list",
		"data":   keys,
	})
}

// CreateKey handles POST /-/keys.
func (h *KeyHandlers) CreateKey(w http.ResponseWriter, r *http.Request) {
	if !h.requireAdmin(w, r) {
		return
	}

	var req gatewayadmin.CreateKeyRequest
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&req); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON: "+err.Error()))
		return
	}

	if req.Name == "" {
		models.WriteError(w, models.ErrBadRequest("missing_name", "name is required"))
		return
	}

	owner := ""
	if req.Owner != nil {
		owner = *req.Owner
	}
	key, rawKey := h.keyStore.Create(req.Name, owner, req.Models, req.Providers, req.Metadata)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"id":         key.ID,
		"key":        rawKey, // Full key shown only on creation.
		"key_prefix": key.KeyPrefix,
		"name":       key.Name,
		"owner":      key.Owner,
		"status":     key.Status,
		"models":     key.AllowedModels,
		"providers":  key.AllowedProviders,
		"created_at": key.CreatedAt,
	})
}

// ImportKeys handles POST /-/keys/sync: the control plane pushes keys it holds
// that this gateway lacks, such as after a restart without startup sync. Keys
// travel by hash; the raw key never leaves the control plane. Additive: a key
// whose hash is already loaded is left as it is, revoked or not.
func (h *KeyHandlers) ImportKeys(w http.ResponseWriter, r *http.Request) {
	if !h.requireAdmin(w, r) {
		return
	}

	var req gatewayadmin.ImportKeysRequest
	decoder := json.NewDecoder(io.LimitReader(r.Body, 10<<20))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&req); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON: "+err.Error()))
		return
	}
	keys := make([]auth.SyncedKey, len(req.Keys))
	for i, k := range req.Keys {
		key, err := auth.SyncedKeyFromContract(k)
		if err != nil {
			models.WriteError(w, models.ErrBadRequest("invalid_key", fmt.Sprintf("keys[%d]: %v", i, err)))
			return
		}
		keys[i] = key
	}

	loaded := h.keyStore.LoadFromHashes(keys)
	slog.Info("keys imported from control plane", "received", len(keys), "loaded", loaded)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(gatewayadmin.ImportKeysResponse{Received: len(keys), Loaded: loaded})
}

// GetKey handles GET /-/keys/{key_id}.
func (h *KeyHandlers) GetKey(w http.ResponseWriter, r *http.Request) {
	if !h.requireAdmin(w, r) {
		return
	}

	keyID := r.URL.Query().Get("key_id")
	if keyID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_key_id", "key_id is required"))
		return
	}

	key := h.keyStore.Get(keyID)
	if key == nil {
		models.WriteError(w, models.ErrNotFound("key_not_found", "Key not found: "+keyID))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	resp := map[string]interface{}{
		"id":         key.ID,
		"key_prefix": key.KeyPrefix,
		"name":       key.Name,
		"owner":      key.Owner,
		"status":     key.Status,
		"key_type":   key.KeyType,
		"created_at": key.CreatedAt,
		"updated_at": key.UpdatedAt,
	}
	if lastUsed := key.GetLastUsedAt(); lastUsed != nil {
		resp["last_used_at"] = lastUsed
	}
	if key.IsManaged() {
		resp["credit_balance"] = key.BalanceUSD()
	}
	if len(key.AllowedModels) > 0 {
		resp["allowed_models"] = key.AllowedModels
	}
	if len(key.AllowedProviders) > 0 {
		resp["allowed_providers"] = key.AllowedProviders
	}
	if len(key.Metadata) > 0 {
		resp["metadata"] = key.Metadata
	}
	json.NewEncoder(w).Encode(resp)
}

// RevokeKey handles DELETE /-/keys/{key_id}.
func (h *KeyHandlers) RevokeKey(w http.ResponseWriter, r *http.Request) {
	if !h.requireAdmin(w, r) {
		return
	}

	keyID := r.URL.Query().Get("key_id")
	if keyID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_key_id", "key_id is required"))
		return
	}

	if !h.keyStore.Revoke(keyID) {
		models.WriteError(w, models.ErrNotFound("key_not_found", "Key not found: "+keyID))
		return
	}

	// Broadcast revocation to all other replicas via Redis pub/sub.
	if h.revocationPublisher != nil {
		h.revocationPublisher.Publish(r.Context(), keyID)
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write([]byte(`{"status":"revoked"}`))
}

// UpdateKey handles PUT /-/keys/{key_id}.
func (h *KeyHandlers) UpdateKey(w http.ResponseWriter, r *http.Request) {
	if !h.requireAdmin(w, r) {
		return
	}

	keyID := r.URL.Query().Get("key_id")
	if keyID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_key_id", "key_id is required"))
		return
	}

	var req gatewayadmin.UpdateKeyRequest
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&req); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON: "+err.Error()))
		return
	}

	key, ok := h.keyStore.Update(keyID, req.Name, req.Owner, req.Models, req.Providers, req.Metadata)
	if !ok {
		models.WriteError(w, models.ErrNotFound("key_not_found", "Key not found: "+keyID))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	resp := map[string]interface{}{
		"id":         key.ID,
		"key_prefix": key.KeyPrefix,
		"name":       key.Name,
		"owner":      key.Owner,
		"status":     key.Status,
		"key_type":   key.KeyType,
		"created_at": key.CreatedAt,
		"updated_at": key.UpdatedAt,
	}
	if lastUsed := key.GetLastUsedAt(); lastUsed != nil {
		resp["last_used_at"] = lastUsed
	}
	if len(key.AllowedModels) > 0 {
		resp["allowed_models"] = key.AllowedModels
	}
	if len(key.AllowedProviders) > 0 {
		resp["allowed_providers"] = key.AllowedProviders
	}
	if len(key.Metadata) > 0 {
		resp["metadata"] = key.Metadata
	}
	json.NewEncoder(w).Encode(resp)
}

// AddCredits handles POST /-/keys/{key_id}/credits.
func (h *KeyHandlers) AddCredits(w http.ResponseWriter, r *http.Request) {
	if !h.requireAdmin(w, r) {
		return
	}

	keyID := r.URL.Query().Get("key_id")
	if keyID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_key_id", "key_id is required"))
		return
	}

	var req struct {
		Amount float64 `json:"amount"` // USD
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON: "+err.Error()))
		return
	}
	if req.Amount <= 0 {
		models.WriteError(w, models.ErrBadRequest("invalid_amount", "amount must be positive"))
		return
	}

	newBalance, ok := h.keyStore.AddCredits(keyID, req.Amount)
	if !ok {
		key := h.keyStore.Get(keyID)
		if key == nil {
			models.WriteError(w, models.ErrNotFound("key_not_found", "Key not found: "+keyID))
		} else {
			models.WriteError(w, models.ErrBadRequest("not_managed", "Key is not a managed key"))
		}
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"key_id":         keyID,
		"added":          req.Amount,
		"credit_balance": newBalance,
	})
}
