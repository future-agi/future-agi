// Package redaction shares decoded JSON masking between span export and logging.
// It does not import either plugin: logging's in-package tests import otel.
package redaction

import (
	"encoding/json"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// RedactJSON decodes a JSON document before matching patterns, including the
// document serialized inside tool-call arguments. Invalid or partial documents
// are suppressed under masking rather than retaining a recoverable fragment.
// This is the request log's decoded-argument algorithm; only owned decoded
// values are changed, and untouched numbers retain their exact lexemes.
func RedactJSON(content string, r *privacy.Redactor, mode string) string {
	if content == "" || r == nil {
		return content
	}
	if r.Mode() == privacy.ModeFull || mode == privacy.ModeFull {
		return r.RedactForMode(content, mode)
	}
	if r.Mode() != privacy.ModePatterns && mode != privacy.ModePatterns {
		return content
	}
	if !json.Valid([]byte(content)) {
		return "[REDACTED]"
	}
	decoder := json.NewDecoder(strings.NewReader(content))
	decoder.UseNumber() // Do not round untouched integer arguments through float64.
	var value any
	if err := decoder.Decode(&value); err != nil {
		return "[REDACTED]"
	}
	encoded, err := json.Marshal(redactArgumentValue(value, r, mode))
	if err != nil {
		return "[REDACTED]"
	}
	return string(encoded)
}

func redactArgumentValue(value any, r *privacy.Redactor, mode string) any {
	switch v := value.(type) {
	case string:
		return r.RedactForMode(v, mode)
	case json.Number:
		if redacted := r.RedactForMode(v.String(), mode); redacted != v.String() {
			return redacted
		}
	case []any:
		for i := range v {
			v[i] = redactArgumentValue(v[i], r, mode)
		}
	case map[string]any:
		out := make(map[string]any, len(v))
		for key, child := range v {
			out[r.RedactForMode(key, mode)] = redactArgumentValue(child, r, mode)
		}
		return out
	}
	return value
}
