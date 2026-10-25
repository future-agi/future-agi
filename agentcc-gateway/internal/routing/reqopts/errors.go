package reqopts

import "github.com/futureagi/agentcc-gateway/internal/models"

// The 400 codes this package returns. Each names the offending field in the
// error's param.
const (
	// CodeInvalidProviderPreference: provider or providerOptions.gateway is
	// malformed, or carries a key the gateway does not know.
	CodeInvalidProviderPreference = "invalid_provider_preference"
	// CodeInvalidRoutingField: models, fallbacks, route, plugins, transforms,
	// preset, session_id or caching is malformed, too long, or sent together
	// with a field it excludes.
	CodeInvalidRoutingField = "invalid_routing_field"
	// CodeNotYetSupported: a field the gateway interprets but does not
	// support, so it is refused (nothing interpreted is accepted and then
	// ignored).
	CodeNotYetSupported = "routing_field_not_yet_supported"
	// CodeUnsupportedRoutingField: plugins, route or transforms on a managed
	// request.
	CodeUnsupportedRoutingField = "unsupported_routing_field"
	// CodeUnsupportedFieldForManaged: a managed request parameter outside the
	// allowlist (managed_allowlist.go).
	CodeUnsupportedFieldForManaged = "unsupported_field_for_managed"
	// CodeExtraBodyConflict: an extra_body key disagrees with the same key at
	// the top level.
	CodeExtraBodyConflict = "extra_body_conflict"
	// CodeInvalidExtraBody: extra_body is not a JSON object, or nests one.
	CodeInvalidExtraBody = "invalid_extra_body"
)

// fieldError is the 400 for param, with a message that says what is wrong.
func fieldError(code, param, message string) *models.APIError {
	e := models.ErrRoutingField(code, param)
	e.Message = message
	return e
}
