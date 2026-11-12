package toolview

import "github.com/futureagi/agentcc-gateway/internal/models"

// nativeBuiltinIdentity describes only known native declaration envelopes.
// The policy identity is the exact wire type, including its version (as with
// models.Tool.Capability), never the Messages invocation name. New versions,
// foreign-protocol types and computer-use declarations require their own schema;
// a generic capability alias or a type prefix cannot admit them here.
//
// Provider options do not change this identity. Leave their validation to the
// provider and retain their original spans, rather than decoding/re-encoding
// payloads or treating option strings as function/capability names.
func nativeBuiltinIdentity(protocol models.NativeProtocol, tool *nativeNode) (string, bool) {
	if tool == nil || tool.kind != '{' {
		return "", false
	}
	// A built-in must not also declare a custom function identity/schema.
	for _, key := range []string{"function", "input_schema", "parameters"} {
		if tool.field(key) != nil {
			return "", false
		}
	}
	typ := tool.field("type").str()
	switch protocol {
	case models.NativeResponses:
		if tool.field("name") != nil {
			return "", false
		}
		switch typ {
		case "web_search", "web_search_2025_08_26",
			"web_search_preview", "web_search_preview_2025_03_11",
			"file_search", "code_interpreter", "image_generation":
			return typ, true
		}
	case models.NativeMessages:
		var name string
		switch typ {
		case "web_search_20250305":
			name = "web_search"
		case "web_fetch_20250910":
			name = "web_fetch"
		case "code_execution_20250522", "code_execution_20250825":
			name = "code_execution"
		default:
			return "", false
		}
		// These versioned schemas prescribe a fixed invocation name, not an
		// application-defined function name that may override the wire type.
		return typ, tool.field("name").str() == name
	}
	return "", false
}
