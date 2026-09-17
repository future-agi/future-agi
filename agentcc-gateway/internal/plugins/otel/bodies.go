package otel

import (
	"encoding/json"
	"log/slog"
	"unicode/utf8"

	"github.com/futureagi/agentcc-gateway/internal/models"
	otelpkg "github.com/futureagi/agentcc-gateway/internal/otel"
	"github.com/futureagi/agentcc-gateway/internal/payloads"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// maxBodyAttrBytes caps a single prompt or completion attribute. Observed
// production prompts run to a few hundred KB with outliers in the megabytes;
// this passes the ordinary ones intact and truncates only the extremes, which
// keeps one span from dominating a batch.
const maxBodyAttrBytes = 1 << 20

// attachBodies puts the request and response on the span as input.value and
// output.value — the keys the platform lifts into its input/output columns.
//
// Content is redacted before it is truncated. The other order can cut through
// the middle of a match and export the leading half of the very thing the
// pattern exists to remove.
func (p *Plugin) attachBodies(span *otelpkg.Span, rc *models.RequestContext) {
	redactor := p.redactorFor(rc)
	mode := privacyMode(rc)

	p.attachMessages(span, rc, redactor, mode)

	in, out := bodyJSON(rc)
	if in != "" {
		setBodyAttr(span, "input", prepareBody(in, redactor, mode))
	}
	if out != "" {
		setBodyAttr(span, "output", prepareBody(out, redactor, mode))
	}
}

// bodyJSON renders whatever this request was — chat, embedding, image, audio,
// OCR, rerank, search — into request and response JSON. Non-chat endpoints go
// through the same builder the request log uses, so a span and a log row of
// the same request never disagree about what was sent.
func bodyJSON(rc *models.RequestContext) (in, out string) {
	if req, resp := payloads.ForEndpoint(rc); req != nil || resp != nil {
		return string(req), string(resp)
	}

	if rc.Request != nil {
		if v, ok := encodeBody(rc.Request); ok {
			in = v
		}
	}
	// A streamed response with no choices is the pre-assembly husk; exporting
	// it would claim an empty completion rather than an absent one.
	if resp := rc.Response; resp != nil && len(resp.Choices) > 0 {
		if v, ok := encodeBody(resp); ok {
			out = v
		}
	}
	return in, out
}

// preparedBody is a body ready to go on a span.
type preparedBody struct {
	value         string
	originalBytes int
	truncated     bool
}

// prepareBody redacts and then truncates, in that order.
//
// The order is the whole point of this function existing. Truncating first can
// cut through the middle of a match — the tail that made it recognisable is
// gone, the pattern no longer fires, and the leading half of the secret is
// exported. Redacting the complete text first means a match is replaced whole,
// and truncation only ever removes already-safe content.
func prepareBody(v string, r *privacy.Redactor, mode string) preparedBody {
	// Inline images and audio go first: removing them outright is safer than
	// redacting them, and it leaves far less text for the patterns to scan.
	v = payloads.ElideInlineData(v)
	v = redact(v, r, mode)

	if len(v) <= maxBodyAttrBytes {
		return preparedBody{value: v}
	}
	return preparedBody{value: truncateAtRune(v, maxBodyAttrBytes), originalBytes: len(v), truncated: true}
}

// truncateAtRune cuts to at most max bytes on a rune boundary. An OTLP
// attribute is a proto string and proto.Marshal rejects the whole message, so
// one value cut mid-rune takes its entire batch of unrelated spans with it.
func truncateAtRune(v string, max int) string {
	if len(v) <= max {
		return v
	}
	cut := max
	for cut > 0 && !utf8.RuneStart(v[cut]) {
		cut--
	}
	return v[:cut]
}

// privacyMode prefers the org's setting over the caller's, as the log does.
func privacyMode(rc *models.RequestContext) string {
	if orgMode := rc.Metadata["org_privacy_mode"]; orgMode != "" {
		return orgMode
	}
	return rc.Metadata["privacy_mode"]
}

// redactorFor prefers the org's redactor over the gateway-wide one, matching
// how the request log resolves it.
func (p *Plugin) redactorFor(rc *models.RequestContext) *privacy.Redactor {
	if p.tenantStore != nil {
		if r := p.tenantStore.Redactor(rc.Metadata[tenant.MetadataKeyOrgID]); r != nil {
			return r
		}
	}
	return p.redactor
}

func redact(content string, r *privacy.Redactor, mode string) string {
	if r == nil || !r.ShouldRedact() {
		return content
	}
	if mode != "" {
		return r.RedactForMode(content, mode)
	}
	return r.Redact(content)
}

func encodeBody(v any) (string, bool) {
	b, err := json.Marshal(v)
	if err != nil {
		slog.Debug("otel: body not serializable, omitted from span", "error", err)
		return "", false
	}
	return string(b), true
}

// setBodyAttr writes the value plus its mime type, and — when the value had to
// be cut — flags it so a consumer does not try to parse a truncated document
// as JSON.
func setBodyAttr(span *otelpkg.Span, kind string, b preparedBody) {
	if b.truncated {
		span.SetAttribute("agentcc."+kind+"_original_bytes", b.originalBytes)
		span.SetAttribute("agentcc."+kind+"_truncated", true)
	} else {
		span.SetAttribute(kind+".mime_type", "application/json")
	}
	span.SetAttribute(kind+".value", b.value)
}
