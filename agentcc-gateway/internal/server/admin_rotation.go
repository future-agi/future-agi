package server

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/rotation"
)

// RotationRestartNote is said with every rotation answer: a rotation lives in
// the gateway's memory, and the deployment config's key is the one a
// restarted or newly rolled-out replica starts with.
const RotationRestartNote = "update the deployment config with the new key before the next restart or rollout"

// rotationValidateTimeout bounds the one upstream call that validates a new
// key before it is promoted.
const rotationValidateTimeout = 15 * time.Second

// RotationHandlers provides admin endpoints for provider key rotation.
type RotationHandlers struct {
	manager    *rotation.Manager
	adminToken string
}

// rotationAnswer is what every rotation endpoint answers: the state, keys
// masked, and the restart note.
type rotationAnswer struct {
	rotation.KeyState
	AppliesUntil string `json:"applies_until"`
	Note         string `json:"note"`
}

func newRotationAnswer(s *rotation.KeyState) rotationAnswer {
	return rotationAnswer{KeyState: s.MaskedState(), AppliesUntil: "restart", Note: RotationRestartNote}
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

// rotationErrorStatus maps a manager error to its HTTP status: 404 for a
// provider with no rotation, 409 for a rotation in the wrong state.
func rotationErrorStatus(err error) int {
	if errors.Is(err, rotation.ErrNotRegistered) {
		return http.StatusNotFound
	}
	return http.StatusConflict
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
		writeJSON(w, rotationErrorStatus(err), map[string]string{"error": err.Error(), "note": RotationRestartNote})
		return
	}

	state, _ := h.manager.GetStatus(providerID)
	writeJSON(w, http.StatusOK, newRotationAnswer(state))
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

	writeJSON(w, http.StatusOK, newRotationAnswer(state))
}

// PromoteRotation handles POST /-/admin/providers/{id}/rotate/promote: one
// upstream call validates the pending key, then every new call uses it. A key
// that fails validation answers 422 and stays pending; nothing changes.
func (h *RotationHandlers) PromoteRotation(w http.ResponseWriter, r *http.Request) {
	if !h.requireAdmin(w, r) {
		return
	}

	providerID := r.URL.Query().Get("id")
	if providerID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "provider id required"})
		return
	}

	method, err := h.manager.Promote(r.Context(), providerID)
	if err != nil {
		status := rotationErrorStatus(err)
		if errors.Is(err, rotation.ErrValidation) {
			status = http.StatusUnprocessableEntity
		}
		writeJSON(w, status, map[string]string{"error": err.Error(), "validated_by": method, "status": "not promoted"})
		return
	}

	state, _ := h.manager.GetStatus(providerID)
	writeJSON(w, http.StatusOK, newRotationAnswer(state))
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
		writeJSON(w, rotationErrorStatus(err), map[string]string{"error": err.Error()})
		return
	}

	state, _ := h.manager.GetStatus(providerID)
	writeJSON(w, http.StatusOK, newRotationAnswer(state))
}

func writeJSON(w http.ResponseWriter, status int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

// RotationBroadcaster tells the other replicas about a promote (Redis pub/sub,
// rotation.Broadcaster).
type RotationBroadcaster interface {
	Publish(ctx context.Context, providerID, key string) error
}

// newRotationManager builds the key rotation manager for the config.yaml
// providers whose rotation.enabled is set, each with its own drain_period,
// wired to the registry: a promote validates the new key with one upstream
// call, then swaps the provider's instance for one built with it.
func newRotationManager(cfg *config.Config, registry *providers.Registry) *rotation.Manager {
	m := rotation.NewManager(rotation.DefaultDrainPeriod, nil)
	m.SetHooks(rotationHooks(registry, nil))
	for name, pCfg := range cfg.Providers {
		if !pCfg.Rotation.Enabled {
			continue
		}
		if pCfg.APIFormat == "bedrock" {
			// Bedrock signs with AWS credentials; api_key is not sent.
			slog.Warn("providers.<id>.rotation.enabled ignored: bedrock authenticates with AWS credentials, not api_key; rotate them in the deployment config", "provider", name)
			continue
		}
		m.RegisterProviderWithDrain(name, pCfg.APIKey, pCfg.Rotation.DrainPeriod)
	}
	return m
}

// rotationHooks connects a rotation manager to registry, and, when b is
// non-nil, tells the other replicas about every promote through b.
func rotationHooks(registry *providers.Registry, b RotationBroadcaster) rotation.Hooks {
	h := rotation.Hooks{
		Validate: func(ctx context.Context, providerID, key string) (string, error) {
			return validateProviderKey(ctx, registry, providerID, key)
		},
		Apply: func(providerID, key string) (func(), error) {
			old, err := registry.RotateProviderKey(providerID, key)
			if err != nil {
				return nil, err
			}
			return func() {
				if err := old.Close(); err != nil {
					slog.Warn("closing the rotated-out provider instance", "provider", providerID, "error", err)
				}
			}, nil
		},
	}
	if b != nil {
		h.Promoted = func(providerID, key string) {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			if err := b.Publish(ctx, providerID, key); err != nil {
				slog.Warn("provider key rotation: broadcast to the other replicas failed; they keep the old key until promoted there or restarted",
					"provider", providerID, "error", err)
			}
		}
	}
	return h
}

// SetRotationBroadcaster makes every promote on this replica reach the other
// replicas through b. Call after New and before Start.
func (s *Server) SetRotationBroadcaster(b RotationBroadcaster) {
	if s.rotation == nil || b == nil {
		return
	}
	s.rotation.SetHooks(rotationHooks(s.registry, b))
}

// ApplyRemoteRotation applies a promote another replica broadcast.
func (s *Server) ApplyRemoteRotation(providerID, key string) {
	if s.rotation == nil {
		return
	}
	if _, err := s.rotation.ApplyRemote(providerID, key); err != nil {
		slog.Warn("provider key rotation from another replica not applied", "provider", providerID, "error", err)
	}
}

// validateProviderKey makes one upstream call to providerID with key, built
// as the provider's own instance would be, and returns which call it made.
// openai and azure list models (GET .../models), which costs nothing; the
// other formats list nothing upstream, so they send a one-token chat
// completion to the provider's first configured model.
func validateProviderKey(ctx context.Context, registry *providers.Registry, providerID, key string) (string, error) {
	candidate, apiFormat, modelIDs, err := registry.RotationCandidate(providerID, key)
	if err != nil {
		return "", err
	}
	defer candidate.Close()
	ctx, cancel := context.WithTimeout(ctx, rotationValidateTimeout)
	defer cancel()

	suffix := ""
	switch apiFormat {
	case "openai", "azure":
		method := "list models"
		_, err := candidate.ListModels(ctx)
		if err == nil {
			return method, nil
		}
		if !noModelsEndpoint(err) || len(modelIDs) == 0 {
			return method, keyFreeError(err, key)
		}
		// An OpenAI-compatible server without a models endpoint: a 404
		// says nothing about the key, so a one-token chat decides.
		suffix = " (no models list)"
	}
	if len(modelIDs) == 0 {
		return "", fmt.Errorf("provider %q (api_format %s) lists no models: the new key is validated with a one-token chat completion to the first of providers.%s.models", providerID, apiFormat, providerID)
	}
	method := "chat completion, max_tokens 1, model " + modelIDs[0] + suffix
	one := 1
	content, _ := json.Marshal("ping")
	if _, err := candidate.ChatCompletion(ctx, &models.ChatCompletionRequest{
		Model:     modelIDs[0],
		Messages:  []models.Message{{Role: "user", Content: content}},
		MaxTokens: &one,
	}); err != nil {
		return method, keyFreeError(err, key)
	}
	return method, nil
}

// noModelsEndpoint reports whether a list-models error says the provider has
// no models endpoint (404 or 405) rather than refusing the key (401, 403).
// The openai and azure adapters answer "list models returned status N".
func noModelsEndpoint(err error) bool {
	msg := err.Error()
	return strings.HasSuffix(msg, "returned status 404") || strings.HasSuffix(msg, "returned status 405")
}

// keyFreeError describes a failed validation call without repeating what the
// upstream said: an upstream may repeat the key it refused (OpenAI's 401 shows
// its first and last characters; others echo it whole), and the description
// is logged and answered. A provider's answer keeps its status, type and code.
// A call that got no answer (nothing listening, a dial or TLS error, the
// provider's timeout) says so, so an unreachable provider never reads as one
// that refused the key; the adapters' own error for it keeps its status,
// type and code, and a transport error its text with the key, should it
// appear, replaced. Any other error is kept the same way.
func keyFreeError(err error, key string) error {
	var apiErr *models.APIError
	if errors.As(err, &apiErr) {
		if apiErr.Code == "provider_0" || apiErr.Type == models.ErrTypeGatewayTimeout {
			// The adapters' error for a call with no response:
			// ErrUpstreamProvider(0, ...) or ErrGatewayTimeout.
			return fmt.Errorf("the validation call got no answer from the provider (status %d, type %s, code %s: no connection, or the provider's timeout): the key was not checked",
				apiErr.Status, apiErr.Type, apiErr.Code)
		}
		return fmt.Errorf("the provider refused the call: status %d, type %s, code %s (its message is not repeated: it can carry the key)",
			apiErr.Status, apiErr.Type, apiErr.Code)
	}
	text := strings.ReplaceAll(err.Error(), key, "[the new key]")
	var urlErr *url.Error
	if errors.As(err, &urlErr) || errors.Is(err, context.DeadlineExceeded) {
		return fmt.Errorf("the validation call got no answer from the provider (%s): the key was not checked", text)
	}
	return errors.New(text)
}
