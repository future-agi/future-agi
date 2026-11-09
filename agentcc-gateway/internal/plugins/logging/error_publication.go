package logging

import (
	"errors"

	"github.com/futureagi/agentcc-gateway/internal/capability"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Both the request.trace emitter and webhook consume record after this step.
// This is a publication copy; RequestContext and retry errors stay untouched.
func (p *Plugin) redactPublishedError(record *TraceRecord, rc *models.RequestContext, content models.ContentMode) {
	if content == models.ContentModeNone {
		record.ErrorMessage = ""
		record.ErrorCode = ""
		return
	}
	if len(rc.Errors) == 0 {
		return
	}
	r := p.tenantStore.Redactor(rc.Metadata[tenant.MetadataKeyOrgID])
	if r == nil || !r.ShouldRedact() {
		r = p.redactor
	}
	mode := rc.Metadata["privacy_mode"]
	if org := rc.Metadata["org_privacy_mode"]; org != "" {
		mode = org
	}
	var redact func(string) string
	if r != nil {
		redact = func(s string) string { return r.RedactForMode(s, mode) }
	}
	var original *models.APIError
	if !errors.As(rc.Errors[len(rc.Errors)-1], &original) {
		original = &models.APIError{Status: record.StatusCode, Type: models.ErrTypeUpstream, Message: record.ErrorMessage, Code: record.ErrorCode}
	}
	var published *models.APIError
	// Provider-shaped errors are projected unless a gateway constructor built
	// them (an empty stream, a refusal). Untyped non-provider errors keep the
	// option-B behaviour: redacted text and their code on this copy; the
	// flusher's projectTraceError still decides exported text by origin.
	if original.Origin() != models.OriginGateway &&
		(original.ProviderStatus > 0 || original.UpstreamStatus > 0 || original.Type == models.ErrTypeUpstream) {
		status := original.ProviderStatus
		if status == 0 {
			status = original.UpstreamStatus
		}
		if status == 0 {
			status = original.Status
		}
		published = capability.PublishProviderError(original, capability.PublicationOwner(rc, original), status, rc.RequestID, redact)
	} else {
		published = capability.RedactPublishedError(original, redact)
	}
	record.ErrorMessage, record.ErrorCode = published.Message, published.Code
	if original.Origin() == models.OriginGateway {
		record.errorOrigin = models.OriginGateway
		record.errorOriginText = published.Error()
	}
}
