package auth

import (
	"context"
	"log/slog"
	"net"
	"strings"

	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/plugins/ipacl"
)

// Plugin implements API key authentication as a pipeline plugin.
// Priority 20: runs after IP ACL, before all identity-dependent plugins.
type Plugin struct {
	keyStore *authpkg.KeyStore
	enabled  bool
}

// New creates a new auth plugin.
func New(keyStore *authpkg.KeyStore, enabled bool) *Plugin {
	return &Plugin{
		keyStore: keyStore,
		enabled:  enabled,
	}
}

func (p *Plugin) Name() string  { return "auth" }
func (p *Plugin) Priority() int { return 20 } // After IP ACL (10), before RBAC/budget/guardrails.

// ProcessRequest validates the API key from the Authorization header.
func (p *Plugin) ProcessRequest(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !p.enabled {
		return pipeline.ResultContinue()
	}

	// Extract Bearer token from Authorization header.
	// The header is stored in request context metadata by the handler.
	authHeader := rc.Metadata["authorization"]
	if authHeader == "" {
		return pipeline.ResultError(models.ErrUnauthorized(
			"Invalid or missing API key",
		))
	}

	rawKey := extractBearerToken(authHeader)
	if rawKey == "" {
		return pipeline.ResultError(models.ErrUnauthorized(
			"Invalid or missing API key",
		))
	}

	// Authenticate the key.
	apiKey := p.keyStore.Authenticate(rawKey)
	if apiKey == nil {
		return pipeline.ResultError(models.ErrUnauthorized(
			"Invalid or missing API key",
		))
	}

	// Check key status.
	if apiKey.Status == "revoked" {
		return pipeline.ResultError(models.ErrUnauthorized(
			"API key has been revoked",
		))
	}

	if apiKey.IsExpired() {
		return pipeline.ResultError(models.ErrUnauthorized(
			"API key has expired",
		))
	}

	if !apiKey.IsActive() {
		return pipeline.ResultError(models.ErrUnauthorized(
			"API key is not active",
		))
	}

	// Check model access.
	if rc.Model != "" && !apiKey.CanAccessModel(rc.Model) {
		return pipeline.ResultError(models.ErrForbidden(
			"API key does not have access to model: " + rc.Model,
		))
	}

	// Per-key IP restriction.
	if len(apiKey.AllowedIPs) > 0 {
		clientIP := net.ParseIP(rc.Metadata["client_ip"])
		if clientIP == nil || !ipacl.CheckIP(clientIP, apiKey.AllowedIPs) {
			return pipeline.ResultError(models.ErrForbidden(
				"API key is not authorized from this IP address",
			))
		}
	}

	// Note: provider ACL (CanAccessProvider) is not checked here because
	// the auth plugin runs before provider resolution. Provider-level access
	// control is enforced after routing resolves the provider.

	// Store allowed providers for downstream enforcement after routing.
	if len(apiKey.AllowedProviders) > 0 {
		rc.Metadata["auth_allowed_providers"] = strings.Join(apiKey.AllowedProviders, ",")
	}

	// Store per-key tool restrictions for tool policy plugin.
	if len(apiKey.AllowedTools) > 0 {
		rc.Metadata["auth_allowed_tools"] = strings.Join(apiKey.AllowedTools, ",")
	}
	if len(apiKey.DeniedTools) > 0 {
		rc.Metadata["auth_denied_tools"] = strings.Join(apiKey.DeniedTools, ",")
	}

	// Populate RequestContext with key info.
	rc.Metadata["auth_key_id"] = apiKey.ID
	rc.Metadata["auth_key_name"] = apiKey.Name
	rc.Metadata["auth_key_owner"] = apiKey.Owner
	rc.Metadata["key_type"] = apiKey.KeyType

	// Copy key metadata to request context.
	slog.Debug("auth plugin: key metadata",
		"key_id", apiKey.ID,
		"key_name", apiKey.Name,
		"metadata_len", len(apiKey.Metadata),
	)
	for k, v := range apiKey.Metadata {
		rc.Metadata["key_"+k] = v
	}

	// Set user ID from key owner if not already set.
	if rc.UserID == "" {
		rc.UserID = apiKey.Owner
	}

	return pipeline.ResultContinue()
}

// ProcessResponse is a no-op for auth.
func (p *Plugin) ProcessResponse(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

func extractBearerToken(header string) string {
	if !strings.HasPrefix(header, "Bearer ") {
		return ""
	}
	return strings.TrimPrefix(header, "Bearer ")
}
