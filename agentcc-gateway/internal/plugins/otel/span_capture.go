package otel

import (
	"maps"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	otelpkg "github.com/futureagi/agentcc-gateway/internal/otel"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Keep the uncapped inputs with the span: fallback-policy endpoints resolve
// their org policy at export, after a config update may have tightened it.
// Re-parsing an already truncated attribute cannot reliably remove an image.
type pendingSpan struct {
	span        *otelpkg.Span
	dimensions  *models.RequestContext
	contentMode models.ContentMode
}

func (p *Plugin) captureDimensions(rc *models.RequestContext) *models.RequestContext {
	metadata := make(map[string]string, len(rc.CustomMetadataKeys)+3)
	for _, key := range rc.CustomMetadataKeys {
		if value, ok := rc.Metadata[key]; ok {
			metadata[key] = value
		}
	}
	// The dimension writers also use these to select their text redactor.
	for _, key := range []string{tenant.MetadataKeyOrgID, "org_privacy_mode", "privacy_mode"} {
		if value, ok := rc.Metadata[key]; ok {
			metadata[key] = value
		}
	}
	headers := make(http.Header)
	if p.captureHeaders != nil {
		for name, values := range rc.RequestHeaders {
			if p.captureHeaders.match(strings.ToLower(name)) {
				headers[name] = append([]string(nil), values...)
			}
		}
	}
	return &models.RequestContext{
		Metadata:            metadata,
		CustomMetadataKeys:  append([]string(nil), rc.CustomMetadataKeys...),
		CallerExtras:        maps.Clone(rc.CallerExtras), // handler admits scalars only
		CallerExtrasDropped: rc.CallerExtrasDropped,
		RequestHeaders:      headers,
	}
}

func (p *Plugin) attachDimensions(span *otelpkg.Span, rc *models.RequestContext, mode models.ContentMode) {
	p.attachMetadata(span, rc, mode)
	p.attachBodyExtras(span, rc, mode)
	p.attachRequestHeaders(span, rc, mode)
}
