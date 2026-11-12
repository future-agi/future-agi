package logging

import (
	"github.com/futureagi/agentcc-gateway/internal/plugins/otel/redaction"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// redactToolArguments decodes the JSON document carried inside arguments before
// matching patterns. Scanning the serialized string misses escaped characters.
// Invalid or partial stream arguments cannot be interpreted safely, so suppress
// them rather than retain a recoverable fragment. Only this record's copy changes.
func redactToolArguments(arguments string, r *privacy.Redactor, mode string) string {
	return redaction.RedactJSON(arguments, r, mode)
}
