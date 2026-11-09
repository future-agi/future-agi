package server

import (
	"errors"
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Provider failures as the client gets them (DX-ERRORS-1). An error a
// provider answered with, or a provider that could not be reached, reaches
// the caller naming that provider, with a well-formed code and the
// provider's own message under metadata.provider_message, so it is never
// mistaken for the gateway's own refusal. A provider that rejects the
// credential the gateway called it with (HTTP 401 or 403) is answered 502
// provider_auth_failed: the caller's gateway key was accepted, and a 401
// would send them rotating it (and carry the gateway's own Bearer
// challenge).
//
// This changes a client publication copy: retries, failover, the circuit
// breaker and billing retain the adapter's error. Trace/request-log publication
// independently projects its own copy before the emitter and flusher.

// codeProviderAuthFailed is the code of a provider's 401 or 403.
const codeProviderAuthFailed = "provider_auth_failed"

// stampProvider records on err, a provider call's error, the ID of the
// provider that was called, when err is that provider's own failure and no
// provider is recorded on it yet. It runs where the call was made, so the
// answer names the provider that failed even after a fallback hop has moved
// rc.Provider elsewhere. The error is the adapter's, made for this call and
// not yet shared.
func stampProvider(err error, providerID string, source ...string) {
	var apiErr *models.APIError
	if providerID == "" || !errors.As(err, &apiErr) || !isProviderFailure(apiErr) {
		return
	}
	if apiErr.Provider != "" && apiErr.Provider != providerID {
		return
	}
	apiErr.Provider = providerID
	if len(source) != 0 {
		owner := models.CredentialManaged
		if source[0] == "org" {
			owner = models.CredentialBYOK
		}
		apiErr.BindPublicationCredentialOwner(owner)
	}
}

// isProviderFailure reports whether e is a provider's failure: the
// provider's own error answer (ProviderStatus), or an upstream_error the
// gateway made for a provider that answered badly or not at all.
func isProviderFailure(e *models.APIError) bool {
	return e.ProviderStatus > 0 || e.Type == models.ErrTypeUpstream
}

// providerFailure is err as the client gets it: a provider's failure
// (isProviderFailure) rewritten to name the provider, rc.Provider when the
// call did not record one; any other error unchanged. The result is a new
// error; err itself is never modified.
func providerFailure(rc *models.RequestContext, err error) error {
	var apiErr *models.APIError
	if err == nil || !errors.As(err, &apiErr) {
		return err
	}
	if apiErr.IsClientPublished() {
		return err // including an existing routing wrapper
	}
	if !isProviderFailure(apiErr) && !hasProviderMetadata(apiErr.Metadata) {
		return err
	}
	name := apiErr.Provider
	if name == "" && rc != nil {
		name = rc.Provider
	}
	attributed := attributeProviderFailure(apiErr, name, credentialOwner(rc))
	return publishClientProviderError(rc, apiErr, attributed)
}

// Whose credential a provider call was made with, for the message of a
// provider_auth_failed.
const (
	credentialOrg     = "org"      // the organization's own provider key
	credentialManaged = "managed"  // Future AGI's key (Future AGI credits)
	credentialGateway = "platform" // the gateway's configured key
)

// credentialOwner is whose credential rc's provider call used.
func credentialOwner(rc *models.RequestContext) string {
	if rc == nil {
		return credentialGateway
	}
	switch {
	case rc.Metadata["billing_mode"] == billingManaged:
		return credentialManaged
	case rc.Metadata["billing_mode"] == billingBYOK || rc.CredentialSource == "org":
		return credentialOrg
	}
	return credentialGateway
}

// attributeProviderFailure is e answered as provider name's failure, made
// with owner's credential.
func attributeProviderFailure(e *models.APIError, name, owner string) *models.APIError {
	if e.IsClientPublished() || hasProviderMetadata(e.Metadata) {
		return e
	}
	status := e.ProviderStatus
	if status == 0 {
		status = e.UpstreamStatus
	}
	if e.Origin() == models.OriginGateway {
		// Retain the constructor's text and origin integrity. Synthetic status
		// is not evidence of an upstream HTTP refusal, even on native surfaces.
		out := *e
		meta := copyErrorMetadata(e.Metadata)
		meta["provider"], meta["provider_status"] = name, 0
		out.Metadata = meta
		return &out
	}
	providerMessage := models.RedactCredentials(e.Message)
	// UpstreamStatus can be the gateway's synthetic 502 (for example an
	// empty stream). Only ProviderStatus proves an HTTP error from upstream.
	meta := copyErrorMetadata(e.Metadata)
	meta["provider"], meta["provider_status"], meta["provider_message"] = name, e.ProviderStatus, providerMessage
	if e.ProviderStatus > 0 {
		if e.Type != "" {
			meta["provider_type"] = e.Type
		}
		if wellFormedProviderCode(e.Code) {
			meta["provider_code"] = e.Code
		}
	}
	out := &models.APIError{
		Status:        e.Status,
		Type:          e.Type,
		Code:          e.Code,
		Param:         e.Param,
		Metadata:      meta,
		RetryAfterSec: e.RetryAfterSec,
	}
	who := providerLabel(name)

	if status == http.StatusUnauthorized || status == http.StatusForbidden {
		out.Status, out.Type, out.Code = http.StatusBadGateway, models.ErrTypeUpstream, codeProviderAuthFailed
		out.Message = providerAuthMessage(name, owner, status, providerMessage)
		return out
	}

	if out.Status >= http.StatusInternalServerError || out.Type == "" {
		out.Type = models.ErrTypeUpstream
	}
	if !wellFormedProviderCode(out.Code) {
		out.Code = fmt.Sprintf("provider_%d", status)
	}
	// A 404 from the org's own provider is most often its base_url or
	// api_format, not the model: say where to look.
	hint := ""
	if status == http.StatusNotFound && owner == credentialOrg {
		hint = " (check that provider's base_url and api_format on the Gateway Providers page)"
	}
	switch {
	case e.ProviderStatus > 0:
		out.Message = fmt.Sprintf("%s returned HTTP %d%s: %s", who, status, hint, providerMessage)
	case status == 0:
		out.Message = fmt.Sprintf("%s could not be reached: %s", who, providerMessage)
	default:
		out.Message = fmt.Sprintf("%s failed%s: %s", who, hint, providerMessage)
	}
	return out
}

// providerAuthMessage says that provider name rejected owner's credential
// with status, and that the caller's gateway key is not the problem.
func providerAuthMessage(name, owner string, status int, providerMessage string) string {
	var b strings.Builder
	switch owner {
	case credentialOrg:
		yours := "Your provider"
		if name != "" {
			yours = fmt.Sprintf("Your provider %q", name)
		}
		fmt.Fprintf(&b, "%s rejected the API key saved for it (HTTP %d). Update that key on the Gateway Providers page; your gateway API key is valid.", yours, status)
	case credentialManaged:
		fmt.Fprintf(&b, "%s rejected Future AGI's credential for it (HTTP %d). This is not a problem with your API key; retry later, and contact Future AGI support if it persists.", providerLabel(name), status)
	default:
		fmt.Fprintf(&b, "%s rejected the credential this gateway is configured with for it (HTTP %d). This is not a problem with your API key; the gateway's operator must update that provider's key.", providerLabel(name), status)
	}
	if providerMessage != "" {
		fmt.Fprintf(&b, " The provider said: %s", providerMessage)
	}
	return b.String()
}

// providerLabel names provider id in a sentence.
func providerLabel(id string) string {
	if id == "" {
		return "The provider"
	}
	return fmt.Sprintf("Provider %q", id)
}

// wellFormedProviderCode reports whether code is a code a client can match
// on: not empty, and not a bare "provider_" naming nothing.
func wellFormedProviderCode(code string) bool {
	return code != "" && code != "provider_"
}
