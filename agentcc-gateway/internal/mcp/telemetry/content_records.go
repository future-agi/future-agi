package telemetry

import (
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"strings"
)

func scrubObject(x map[string]any, r *privacy.Redactor, depth int, jwk bool) any {
	if depth >= maxContentDepth {
		return redactedContent
	}
	credentialHeader := false
	headerRecord := false
	for k := range x {
		if field := normalizedField(k); field == "value" || field == "values" {
			headerRecord = true
		}
	}
	for k, e := range x {
		field := normalizedField(k)
		if field == "kty" {
			jwk = true
		}
		if name, ok := e.(string); ok && (field == "name" || field == "key" || field == "header") && isCredentialField(name) {
			credentialHeader = true
		}
	}
	out := make(map[string]any, len(x))
	for k, e := range x {
		if k == "_meta" {
			continue
		}
		cleanKey := scrubText(k, depth+1)
		if r != nil && r.ShouldRedact() {
			cleanKey = r.Redact(cleanKey)
		}
		field := normalizedField(k)
		if name, ok := e.(string); ok && headerRecord && (field == "name" || field == "key" || field == "header") && safeHeaderDescriptor(name) {
			out[cleanKey] = scrubValue(e, r, depth+1)
		} else if obj, ok := e.(map[string]any); ok && field == "jwk" {
			out[cleanKey] = scrubObject(obj, r, depth+1, true)
		} else if isCredentialField(k) || (credentialHeader && (field == "value" || field == "values")) || (jwk && privateJWKMember(field)) {
			out[cleanKey] = redactedContent
		} else {
			out[cleanKey] = scrubValue(e, r, depth+1)
		}
	}
	return out
}

// Do not mistake an arbitrary credential stored in a field named key for a
// header descriptor merely because an unrelated value field is present.
func safeHeaderDescriptor(name string) bool {
	switch normalizedField(name) {
	case "accept", "acceptencoding", "acceptlanguage", "contenttype", "contentlength", "useragent", "host":
		return true
	}
	return false
}

func privateJWKMember(field string) bool {
	switch field {
	case "k", "d", "p", "q", "dp", "dq", "qi":
		return true
	}
	return false
}

// MCP adjacent text items form one logical text stream. Sanitize the bounded
// group before visiting individual records, so a split header keeps its context.
// If changed, retain the cleaned group in its first item and empty the following
// text slots; non-text records and metadata are not coalesced.
func scrubTextChunks(items []any, depth int) {
	for start := 0; start < len(items); {
		if _, ok := chunkText(items[start]); !ok {
			start++
			continue
		}
		end := start
		var joined strings.Builder
		overflow := false
		for end < len(items) {
			text, ok := chunkText(items[end])
			if !ok {
				break
			}
			if len(text) > maxTextBytes-joined.Len() {
				overflow = true
			} else if !overflow {
				joined.WriteString(text)
			}
			end++
		}
		if end-start > 1 {
			clean := redactedContent
			if !overflow {
				clean = scrubText(joined.String(), depth+1)
			}
			if overflow || clean != joined.String() {
				for i := start; i < end; i++ {
					setChunkText(items, i, "")
				}
				setChunkText(items, start, clean)
			}
		}
		start = end
	}
}

func chunkText(v any) (string, bool) {
	if s, ok := v.(string); ok {
		return s, true
	}
	if m, ok := v.(map[string]any); ok && m["type"] == "text" {
		s, ok := m["text"].(string)
		return s, ok
	}
	return "", false
}

func setChunkText(items []any, i int, text string) {
	if m, ok := items[i].(map[string]any); ok {
		m["text"] = text
	} else {
		items[i] = text
	}
}
