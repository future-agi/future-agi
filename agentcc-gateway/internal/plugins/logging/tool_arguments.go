package logging

import (
	"encoding/json"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// redactToolArguments decodes the JSON document carried inside arguments before
// matching patterns. Scanning the serialized string misses escaped characters.
// Invalid or partial stream arguments cannot be interpreted safely, so suppress
// them rather than retain a recoverable fragment. Only this record's copy changes.
func redactToolArguments(arguments string, r *privacy.Redactor, mode string) string {
	if arguments == "" || r == nil {
		return arguments
	}
	if r.Mode() == privacy.ModeFull || mode == privacy.ModeFull {
		return r.RedactForMode(arguments, mode)
	}
	if r.Mode() != privacy.ModePatterns && mode != privacy.ModePatterns {
		return arguments
	}
	if !json.Valid([]byte(arguments)) {
		return "[REDACTED]"
	}
	decoder := json.NewDecoder(strings.NewReader(arguments))
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
