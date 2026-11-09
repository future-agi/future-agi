package capability

import (
	"bytes"
	"encoding/json"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// PublicationOwner accepts only the concrete provider-instance marker, never a
// billing label. A multi-attempt request cannot bind its final marker to an
// earlier error, even when both attempts used the same routing provider id.
// Undecidable ownership intentionally has no BYOK exception.
func PublicationOwner(rc *models.RequestContext, e *models.APIError) models.CredentialType {
	if owner, bound := e.PublicationCredentialOwner(); bound {
		return owner
	}
	if rc != nil && e != nil && rc.CredentialSource == "org" && rc.Provider != "" && (e.Provider == "" || e.Provider == rc.Provider) && len(rc.SnapshotV2().Attempts) <= 1 {
		return models.CredentialBYOK
	}
	return models.CredentialManaged
}

// PublishProviderError implements the option-B (iii) fallback on a private
// publication copy. Callers retain the original for retry, routing and billing.
// The caller supplies actual provider status separately from attributed status.
func PublishProviderError(e *models.APIError, owner models.CredentialType, status int, id string, redact func(string) string) *models.APIError {
	if e == nil {
		return nil
	}
	out := *e
	out.BindPublicationCredentialOwner(owner)
	if owner != models.CredentialBYOK && e.Origin() != models.OriginGateway {
		// Always use a nonempty input: a provider may put its entire diagnostic
		// (including a credential) in code/type while leaving message empty.
		out.Message = ProjectErrorMessage(ErrorTextPlatformOnly, owner, status, id, "provider error")
		out.Type, _, _ = strings.Cut(out.Message, ":")
		out.Code = out.Type
		out.Param = nil
		out.Metadata = nil
	}
	return RedactPublishedError(&out, redact)
}

// RedactPublishedError applies both deployed credential redactors, followed by
// the effective org/global policy, to every JSON-published error text leaf.
func RedactPublishedError(e *models.APIError, redact func(string) string) *models.APIError {
	if e == nil {
		return nil
	}
	scrub := func(text string) string {
		text = models.RedactCredentials(text)
		text = privacy.RedactCredentials(text, func(name string) string { return "[REDACTED:" + name + "]" })
		if redact != nil {
			text = redact(text)
		}
		return text
	}
	out := *e
	out.Message, out.Type, out.Code = scrub(e.Message), scrub(e.Type), scrub(e.Code)
	if e.Param != nil {
		p := scrub(*e.Param)
		out.Param = &p
	}
	if e.Metadata != nil {
		out.Metadata = nil
		// Attribution builds map[string]any metadata with typed values (an int
		// provider_status). Scrub that map in place on a copy so a published
		// error keeps the same Go value types as the original; anything else
		// (structs, typed maps, values JSON cannot encode) takes the
		// normalizing path below.
		if m, ok := e.Metadata.(map[string]any); ok {
			copied := redactErrorValue(m, scrub)
			if _, err := json.Marshal(copied); err == nil {
				out.Metadata = copied
				return &out
			}
		}
		// Metadata includes structs as well as maps; normalize only this publication
		// copy, keeping JSON number precision and dropping unmarshalable data.
		out.Metadata = normalizeErrorJSON(e.Metadata, scrub)
	}
	return &out
}

// normalizeErrorJSON round-trips value through JSON (keeping number precision)
// and scrubs every string leaf and key. Unmarshalable data is dropped (nil).
func normalizeErrorJSON(value any, scrub func(string) string) any {
	raw, err := json.Marshal(value)
	if err != nil {
		return nil
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var decoded any
	if dec.Decode(&decoded) != nil {
		return nil
	}
	return redactErrorJSON(decoded, scrub)
}

// redactErrorValue copies value, scrubbing strings and keys and keeping
// primitive Go types. Any other type is normalized through JSON, so no
// unscrubbed text can hide inside a struct or typed map.
func redactErrorValue(value any, scrub func(string) string) any {
	switch v := value.(type) {
	case nil, bool, int, int8, int16, int32, int64, uint, uint8, uint16, uint32, uint64, float32, float64, json.Number:
		return v
	case string:
		return scrub(v)
	case []any:
		out := make([]any, len(v))
		for i, item := range v {
			out[i] = redactErrorValue(item, scrub)
		}
		return out
	case map[string]any:
		out := make(map[string]any, len(v))
		for key, item := range v {
			out[scrub(key)] = redactErrorValue(item, scrub)
		}
		return out
	default:
		return normalizeErrorJSON(v, scrub)
	}
}

func redactErrorJSON(value any, scrub func(string) string) any {
	switch v := value.(type) {
	case string:
		return scrub(v)
	case []any:
		out := make([]any, len(v))
		for i, item := range v {
			out[i] = redactErrorJSON(item, scrub)
		}
		return out
	case map[string]any:
		out := make(map[string]any, len(v))
		for key, item := range v {
			out[scrub(key)] = redactErrorJSON(item, scrub)
		}
		return out
	default:
		return value
	}
}
