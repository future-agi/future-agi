package auth

import (
	"context"
	"log/slog"
	"net"
	"strings"

	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
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

	if middleware.IsLicenseAuthorized(ctx) {
		rc.Metadata["key_type"] = "internal"
		rc.Metadata["key_access_groups"] = "internal"
		if claims := middleware.GetLicenseClaims(ctx); claims != nil {
			rc.Metadata["license_id"] = claims.LicenseID
			rc.Metadata["customer_id"] = claims.CustomerID
			rc.Metadata["instance_id"] = claims.InstanceID
			rc.Metadata["auth_type"] = "license"
		}
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

	// Per-key IP restriction. KeyAuth applies the same rule on every key-gated
	// path, including the routes that never run this pipeline.
	if err := ipacl.CheckKeyIP(net.ParseIP(rc.Metadata["client_ip"]), apiKey.AllowedIPs); err != nil {
		return pipeline.ResultError(err)
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

	// Copy the key's metadata labels to the request context, then write the
	// key's identity and authority after them.
	slog.Debug("auth plugin: key metadata",
		"key_id", apiKey.ID,
		"key_name", apiKey.Name,
		"metadata_len", len(apiKey.Metadata),
	)
	// Read once: KeyStore.Update swaps in a new map, and the labels and the
	// authority below must come from the same one.
	metadata := apiKey.Metadata
	for k, v := range metadata {
		// A reserved name is never a label: key_type and the key's id, name and
		// owner always come from the key itself. Authority entries are written
		// below.
		if authpkg.IsReservedMetadataKey(k) {
			continue
		}
		rc.Metadata["key_"+k] = v
	}
	writeKeyAuthority(rc, apiKey, metadata)

	// Set user ID from key owner if not already set.
	if rc.UserID == "" {
		rc.UserID = apiKey.Owner
	}

	return pipeline.ResultContinue()
}

// authorityMetadataKeys are the reserved key metadata entries the gateway
// reads as authority (org_id, access_groups, role, team, allow_policy_override).
var authorityMetadataKeys = authpkg.AuthorityMetadataKeys()

// writeKeyAuthority writes what the request knows about its key from the key
// itself, after the metadata labels, so no label can stand in for it. Each
// authority entry is the key's own value, or absent when the key has none:
// nothing written earlier for another key survives.
func writeKeyAuthority(rc *models.RequestContext, apiKey *authpkg.APIKey, metadata map[string]string) {
	rc.Metadata["auth_key_id"] = apiKey.ID
	rc.Metadata["auth_key_name"] = apiKey.Name
	rc.Metadata["auth_key_owner"] = apiKey.Owner
	rc.Metadata["key_type"] = apiKey.KeyType
	for _, name := range authorityMetadataKeys {
		if v, ok := metadata[name]; ok {
			rc.Metadata["key_"+name] = v
		} else {
			delete(rc.Metadata, "key_"+name)
		}
	}
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
