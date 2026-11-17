package otel

import (
	"encoding/json"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// JSON strings are text unless a protocol identifies a structured tool envelope.
// In particular a user/assistant content string or a text block may legitimately
// quote image-part JSON. Schema declarations are not content parts either.
type imageContext uint8

const (
	imageStructured imageContext = iota
	imageContent
	imageToolEnvelope
	imageOpaque
)

// maskImageJSON replaces only image parts. Full is deliberately a byte-for-byte
// no-op, leaving the existing size limits and text redaction in charge. Decode
// into an owned tree so neither provider input nor parallel observers change.
func maskImageJSON(v string, mode models.ContentMode) string {
	return maskImageJSONInContext(v, mode, imageContent)
}

func maskImageJSONInContext(v string, mode models.ContentMode, context imageContext) string {
	if mode == models.ContentModeFull {
		return v
	}
	masked, _ := maskImageJSONValue(v, mode, context)
	return masked
}

func maskImageJSONValue(v string, mode models.ContentMode, context imageContext) (string, bool) {
	trimmed := strings.TrimSpace(v)
	if trimmed == "" || !strings.ContainsAny(trimmed[:1], "{[\"") || !json.Valid([]byte(v)) {
		return v, false
	}
	dec := json.NewDecoder(strings.NewReader(v))
	dec.UseNumber() // do not round tool arguments or other non-image numbers
	var tree any
	if dec.Decode(&tree) != nil {
		return v, false
	}
	tree, changed := maskImageValue(tree, mode, context)
	if !changed {
		return v, false
	}
	b, err := json.Marshal(tree)
	if err != nil {
		// The tree came from JSON and replacements are JSON scalars. Do not
		// return original image content if this invariant ever changes.
		return `"[image]"`, true
	}
	return string(b), true
}

func maskImageValue(v any, mode models.ContentMode, context imageContext) (any, bool) {
	if context == imageOpaque {
		return v, false
	}
	switch value := v.(type) {
	case map[string]any:
		if image, ok := imagePartInfo(value, context != imageStructured); ok {
			return map[string]any{"type": "text", "text": image.placeholder(mode)}, true
		}
		changed := false
		for k, child := range value {
			masked, didChange := maskImageValue(child, mode, imageChildContext(value, k, context))
			if didChange {
				value[k] = masked
				changed = true
			}
		}
		return value, changed
	case []any:
		changed := false
		for i, child := range value {
			masked, didChange := maskImageValue(child, mode, context)
			if didChange {
				value[i] = masked
				changed = true
			}
		}
		return value, changed
	case string:
		if context == imageToolEnvelope {
			return maskImageJSONValue(value, mode, imageToolEnvelope)
		}
	}
	return v, false
}

func imageChildContext(parent map[string]any, key string, context imageContext) imageContext {
	typ, _ := parent["type"].(string)
	role, _ := parent["role"].(string)
	switch key {
	case "text":
		// Literal strings (including valid text blocks) are opaque, but tool
		// data may use this name for objects/arrays containing image parts.
		if _, ok := parent[key].(string); ok {
			return imageOpaque
		}
	case "tools", "functions", "functionDeclarations", "function_declarations", "response_format":
		// Definitions are schemas, not tool invocations. Do not exempt a
		// field merely named "parameters" inside real tool arguments/results.
		if context == imageStructured {
			return imageOpaque
		}
	case "content":
		if role == "tool" || role == "function" || typ == "tool_result" {
			return imageToolEnvelope
		}
		return imageContent
	case "parts", "images":
		return imageContent
	case "function", "function_call", "functionCall", "function_response", "functionResponse":
		return imageToolEnvelope
	case "arguments", "args":
		if context == imageToolEnvelope || typ == "function_call" {
			return imageToolEnvelope
		}
	case "input":
		if typ == "tool_use" {
			return imageToolEnvelope
		}
	case "response", "result", "output":
		if context == imageToolEnvelope || typ == "function_call_output" || typ == "computer_call_output" {
			return imageToolEnvelope
		}
	}
	// Containers inside content/tool results may hold native untyped image
	// parts, but their arbitrary string fields remain opaque text.
	if context == imageContent || context == imageToolEnvelope {
		return imageContent
	}
	return imageStructured
}
