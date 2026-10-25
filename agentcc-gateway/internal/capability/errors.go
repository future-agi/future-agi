package capability

import (
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Error codes of the capability core.
const (
	CodeManagedCapabilityUnavailable  = "managed_capability_unavailable"
	CodeCapabilityProviderUnavailable = "capability_provider_unavailable"
	CodePolicyTermsViolation          = "policy_terms_violation"
	CodeEndpointNotConfigured         = "endpoint_not_configured"
)

// errNotConfigured is the 501 for a capability this gateway does not serve.
func errNotConfigured(capability string) *models.APIError {
	return &models.APIError{Status: http.StatusNotImplemented, Type: models.ErrTypeServer, Code: CodeEndpointNotConfigured,
		Message:  "The capability '" + capability + "' is not configured on this gateway.",
		Metadata: map[string]any{"capability": capability}}
}

// policyRefusal is the 403 for a call the org's tools policy or a card's
// terms leave no vendor for.
func policyRefusal(capability, reason string) *models.APIError {
	return &models.APIError{Status: http.StatusForbidden, Type: models.ErrTypeInvalidRequest, Code: CodePolicyTermsViolation,
		Message:  "No vendor for '" + capability + "' satisfies your organization's tools policy (" + reason + ").",
		Metadata: map[string]any{"capability": capability, "reason": reason}}
}

// managedUnavailable is the 503 for a call that only a managed vendor could
// serve and none is admitted: reason is byok_only, no_managed_credential,
// not_enabled, managed_tools_disabled or owner_price_pending.
func managedUnavailable(capability, reason string) *models.APIError {
	return &models.APIError{Status: http.StatusServiceUnavailable, Type: models.ErrTypeServer, Code: CodeManagedCapabilityUnavailable,
		Message:  "No managed vendor is available for '" + capability + "' (" + reason + "). Add your own vendor key, or ask an admin to enable managed tools.",
		Metadata: map[string]any{"capability": capability, "reason": reason}}
}

// attemptRef is one attempt as a refusal lists it: no latency or health
// figure, so no_perf_disclosure holds on the error too.
type attemptRef struct {
	AttemptID      string `json:"attempt_id"`
	Provider       string `json:"provider"`
	CredentialType string `json:"credential_type"`
	Outcome        string `json:"outcome"`
	StatusCode     int    `json:"status_code,omitempty"`
	ErrorClass     string `json:"error_class,omitempty"`
}

// providerUnavailable is the 503 when every candidate failed, or a no_divert
// or managed vendor failed and nothing may follow it.
func providerUnavailable(capability string, attempts []models.Attempt) *models.APIError {
	refs := make([]attemptRef, 0, len(attempts))
	for _, a := range attempts {
		refs = append(refs, attemptRef{AttemptID: a.AttemptID, Provider: a.Provider, CredentialType: string(a.CredentialType),
			Outcome: string(a.Outcome), StatusCode: a.StatusCode, ErrorClass: string(a.ErrorClass)})
	}
	return &models.APIError{Status: http.StatusServiceUnavailable, Type: models.ErrTypeServer, Code: CodeCapabilityProviderUnavailable,
		Message:  "No vendor could serve '" + capability + "'.",
		Metadata: map[string]any{"capability": capability, "attempts": refs}}
}

// noCandidate is the refusal when the selector left nothing: a managed
// refusal when only a managed vendor could have served, else a policy
// refusal, else no vendor at all.
func noCandidate(capability string, why refusals) *models.APIError {
	switch {
	case why.managed != "":
		return managedUnavailable(capability, why.managed)
	case why.policy != "":
		return policyRefusal(capability, why.policy)
	}
	return providerUnavailable(capability, nil)
}
