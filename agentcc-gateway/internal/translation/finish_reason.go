package translation

// Format-scoped finish-reason tables. Each map converts a provider-native
// finish_reason string into the corresponding OpenAI finish_reason value.
//
// We keep separate tables per format to avoid key collisions (e.g. MAX_TOKENS
// appears in both Gemini and Cohere with the same meaning, but Cohere also has
// COMPLETE where Gemini has STOP — a single flat map would be ambiguous for
// any key that means something different in two formats).

var anthropicFinishReasons = map[string]string{
	"end_turn":      "stop",
	"max_tokens":    "length",
	"tool_use":      "tool_calls",
	"stop_sequence": "stop",
}

var googleFinishReasons = map[string]string{
	// Standard terminal reasons.
	"STOP":      "stop",
	"MAX_TOKENS": "length",

	// Safety / content-policy reasons all map to content_filter.
	"SAFETY":              "content_filter",
	"RECITATION":          "content_filter",
	"BLOCKLIST":           "content_filter",
	"PROHIBITED_CONTENT":  "content_filter",
	"SPII":                "content_filter",

	// Degenerate / unspecified reasons fall back to "stop".
	"MALFORMED_FUNCTION_CALL":   "stop",
	"FINISH_REASON_UNSPECIFIED": "stop",
	"OTHER":                     "stop",
}

// cohereFinishReasons maps Cohere-native finish reasons for future use.
// The /v1/chat/completions Cohere inbound endpoint is out of scope for Phase 2
// but the table is defined here so it's available when Agent C picks this up.
var cohereFinishReasons = map[string]string{
	"COMPLETE":   "stop",
	"MAX_TOKENS": "length",
	"TOOL_CALL":  "tool_calls",
	"ERROR":      "stop",
	"ERROR_TOXIC": "content_filter",
	"USER_CANCEL": "stop",
}

// MapFinishReason converts a provider-native finish reason string into the
// corresponding OpenAI finish_reason value. format is the inbound api_format
// ("anthropic", "google", "cohere"). Returns "" if unmapped — callers should
// default to "stop" when they receive an empty string.
func MapFinishReason(format, native string) string {
	var table map[string]string
	switch format {
	case "anthropic":
		table = anthropicFinishReasons
	case "google":
		table = googleFinishReasons
	case "cohere":
		table = cohereFinishReasons
	default:
		return ""
	}
	return table[native]
}
