package telemetry

import (
	"bytes"
	"encoding/json"
	"io"
	"regexp"
	"strings"
	"unicode"

	"github.com/futureagi/agentcc-gateway/internal/mcp/meter"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

const redactedContent = "[redacted]"
const maxContentDepth = 32

// Normalize separators, case and numeric suffixes. Substring classification
// deliberately also covers x-/vendor prefixes and camel-case compound names.
func normalizedField(name string) string {
	decoded, ok := canonicalText(name)
	if !ok {
		return "credential"
	}
	name = decoded
	return strings.Map(func(r rune) rune {
		if unicode.IsLetter(r) {
			return unicode.ToLower(r)
		}
		return -1
	}, name)
}

func isCredentialField(name string) bool {
	field := normalizedField(name)
	for _, term := range []string{"cookie", "jwt", "assertion", "bearer", "auth", "token", "key", "secret", "password", "passwd", "passphrase", "pwd", "jwk", "signature", "session", "credential", "subscription"} {
		if strings.Contains(field, term) {
			return true
		}
	}
	return false
}

// Content applies the mandatory, non-networking sanitizer before the shared
// organization redactor, serialization and byte limit. Closed modes keep no body.
func Content(raw json.RawMessage, mode string, r *privacy.Redactor) string {
	if mode != meter.ModeFull || len(raw) == 0 {
		return ""
	}
	if len(raw) > maxTextBytes {
		return `"[redacted]"`
	}
	var value any
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	if dec.Decode(&value) != nil || dec.Decode(new(any)) != io.EOF {
		return ""
	}
	clean, err := json.Marshal(scrubValue(value, r, 0))
	if err != nil {
		return ""
	}
	return string(meter.Content(clean, mode, r))
}

var scalarShape = regexp.MustCompile(`^[A-Za-z0-9_.:/-]{1,256}$`)

// SanitizeText admits bounded identifier-shaped scalar dimensions only, in
// every capture mode. Never truncate a rejected value into an approved prefix.
func SanitizeText(text string, r *privacy.Redactor) string {
	if text == "" {
		return ""
	}
	if !scalarShape.MatchString(text) || scrubText(text, 0) != text {
		return redactedContent
	}
	if r != nil && r.ShouldRedact() && r.Redact(text) != text {
		return redactedContent
	}
	return text
}

func scrubValue(v any, r *privacy.Redactor, depth int) any {
	// Fail closed instead of returning an unvisited subtree at the recursion cap.
	if depth >= maxContentDepth {
		return redactedContent
	}
	switch x := v.(type) {
	case string:
		return scrubText(x, depth+1)
	case []any:
		if len(x) == 2 {
			if name, ok := x[0].(string); ok && isCredentialField(name) {
				return []any{scrubText(name, depth+1), redactedContent}
			}
		}
		scrubTextChunks(x, depth+1)
		for i, e := range x {
			x[i] = scrubValue(e, r, depth+1)
		}
		return x
	case map[string]any:
		return scrubObject(x, r, depth, false)
	default:
		return v
	}
}
