package server

import (
	"strings"

	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
)

// Request metadata keys holding the presented key's ACL, written by
// peekKeyOrgID before routing in the form the auth plugin writes
// auth_allowed_providers (plugins/auth/auth.go). Callers cannot set them:
// caller metadata refuses the auth_ prefix (isBlockedMetadataKey).
const (
	metaKeyAllowedProviders = "auth_allowed_providers"
	metaKeyAllowedModels    = "auth_allowed_models"
)

// recordKeyACL copies the key's allowed providers and models into rc as
// comma lists, so the provider-ACL exits and eligibility see them before
// the provider is called. A key with no restriction writes nothing.
func recordKeyACL(rc *models.RequestContext, key *authpkg.APIKey) {
	if len(key.AllowedProviders) > 0 {
		rc.Metadata[metaKeyAllowedProviders] = strings.Join(key.AllowedProviders, ",")
	}
	if len(key.AllowedModels) > 0 {
		rc.Metadata[metaKeyAllowedModels] = strings.Join(key.AllowedModels, ",")
	}
}

// keyProviderACL is the per-key provider ACL of an endpoint that routes to
// a provider: once the provider is resolved and before it is
// called, a key whose allowed_providers does not name rc.Provider is
// refused with 403. A restricted key is refused when no provider was
// recorded at all: every resolution records one, and a path that
// stops doing so must not let the key through. Internal service keys are
// exempt, as on chat (resolveRoute).
func (h *Handlers) keyProviderACL(rc *models.RequestContext) *models.APIError {
	if rc.Metadata["key_type"] == "internal" {
		return nil
	}
	allowed, ok := rc.Metadata[metaKeyAllowedProviders]
	switch {
	case !ok:
		return nil
	case rc.Provider == "":
		return models.ErrForbidden("API key does not have access to an unresolved provider")
	case eligibility.CSVContains(allowed, rc.Provider):
		return nil
	}
	return errKeyProviderDenied(rc.Provider)
}

// errKeyProviderDenied is the 403 for a key that may not use providerID,
// with the message every provider-ACL exit uses.
func errKeyProviderDenied(providerID string) *models.APIError {
	return models.ErrForbidden("API key does not have access to provider: " + providerID)
}
