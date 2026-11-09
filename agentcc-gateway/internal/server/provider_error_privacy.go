package server

import (
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/capability"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func (h *Handlers) configureErrorRedactor(cfg config.PrivacyConfig) {
	if !cfg.Enabled {
		return
	}
	patterns := make([]privacy.PatternConfig, 0, len(cfg.Patterns))
	for _, p := range cfg.Patterns {
		patterns = append(patterns, privacy.PatternConfig{Name: p.Name, Pattern: p.Pattern})
	}
	h.errorRedactor = privacy.New(cfg.Mode, patterns)
}

// Bind an immutable policy before dispatch. Do not capture the pooled context in
// a closure that may outlive it. Match the deployed org-first/global fallback.
func (h *Handlers) bindErrorRedactor(rc *models.RequestContext) {
	if rc == nil {
		return
	}
	orgID := rc.Metadata[tenant.MetadataKeyOrgID]
	if orgID == "" {
		// Non-key entry points already carry the resolved org. Prefer the
		// authenticated key's org whenever both are available.
		orgID = rc.Metadata["org_id"]
	}
	r := h.tenantStore.Redactor(orgID)
	if r == nil || !r.ShouldRedact() {
		r = h.errorRedactor
	}
	mode := rc.Metadata["privacy_mode"]
	if orgMode := rc.Metadata["org_privacy_mode"]; orgMode != "" {
		mode = orgMode
	}
	rc.ErrorTextRedactor = nil
	if r != nil {
		rc.ErrorTextRedactor = func(text string) string { return r.RedactForMode(text, mode) }
	}
}

func publishClientProviderError(rc *models.RequestContext, original, attributed *models.APIError) *models.APIError {
	var redact func(string) string
	id := ""
	if rc != nil {
		id = rc.RequestID
		redact = rc.ErrorTextRedactor
	}
	status := original.ProviderStatus
	if status == 0 {
		status = original.UpstreamStatus
	}
	upstreamStatus := status
	if status == 0 {
		status = original.Status
	}
	owner := capability.PublicationOwner(rc, original)
	out := capability.PublishProviderError(attributed, owner, status, id, redact)
	if owner != models.CredentialBYOK {
		// Only provider-originated fields are replaced. Attribution above (or
		// nativeProviderFailure) may have assigned a gateway code. A different
		// code is such an assignment; the two equal-value cases below are the
		// fixed auth code and numeric status code, never arbitrary provider text.
		if attributed.Code != original.Code || attributed.Code == codeProviderAuthFailed || attributed.Code == fmt.Sprintf("provider_%d", upstreamStatus) {
			out.Code = attributed.Code
		}
		// This fixed type is also assigned by gateway attribution for upstream
		// failures. It cannot contain provider diagnostics or credentials.
		if attributed.Type == models.ErrTypeUpstream {
			out.Type = attributed.Type
		}
	}
	out.MarkClientPublished()
	return out
}

// The ordinary chat SSE writer did not use providerFailure. Normalize the
// publication copy here while leaving the error used by stream finalization.
func clientStreamError(rc *models.RequestContext, e *models.APIError, selected ...providers.Provider) *models.APIError {
	if e == nil {
		return nil
	}
	if isProviderFailure(e) || hasProviderMetadata(e.Metadata) {
		publication := *e
		if rc != nil && len(selected) > 0 && selected[0] != nil {
			stampProvider(&publication, rc.Provider, credentialSourceOf(selected[0], rc.Metadata["org_id"]))
		}
		return providerFailure(rc, &publication).(*models.APIError)
	}
	var redact func(string) string
	if rc != nil {
		redact = rc.ErrorTextRedactor
	}
	return capability.RedactPublishedError(e, redact)
}
