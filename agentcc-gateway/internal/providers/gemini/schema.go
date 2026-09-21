package gemini

import "encoding/json"

// normalizeToolSchema removes JSON Schema keywords that Vertex Gemini does not accept in
// function declarations. Claude Code includes these in its built-in tool definitions.
func normalizeToolSchema(raw json.RawMessage) json.RawMessage {
	if len(raw) == 0 {
		return raw
	}
	var schema any
	if err := json.Unmarshal(raw, &schema); err != nil {
		return raw
	}
	if !stripUnsupportedSchemaKeywords(schema) {
		return raw
	}
	normalized, err := json.Marshal(schema)
	if err != nil {
		return raw
	}
	return normalized
}

func stripUnsupportedSchemaKeywords(value any) bool {
	changed := false
	switch node := value.(type) {
	case map[string]any:
		if kinds, ok := node["type"].([]any); ok {
			for _, kind := range kinds {
				if text, isString := kind.(string); isString && text != "null" {
					node["type"] = text
					changed = true
					break
				}
			}
		}
		if enum, ok := node["enum"].([]any); ok {
			withoutNull := make([]any, 0, len(enum))
			for _, item := range enum {
				if item != nil {
					withoutNull = append(withoutNull, item)
				}
			}
			if len(withoutNull) != len(enum) {
				node["enum"] = withoutNull
				changed = true
			}
		}
		if node["type"] == "array" {
			if _, ok := node["items"]; !ok {
				// JSON Schema permits an omitted items field (any item). Vertex requires the
				// field to be present, and an empty schema preserves the same meaning.
				node["items"] = map[string]any{}
				changed = true
			}
		}
		for _, key := range []string{
			"$schema", "$id", "$defs", "definitions", "$ref", "propertyNames",
			"patternProperties", "additionalProperties", "unevaluatedProperties",
			"unevaluatedItems", "contains", "minContains", "maxContains", "dependencies",
			"dependentRequired", "dependentSchemas", "if", "then", "else",
			"not", "allOf", "contentEncoding", "contentMediaType", "examples",
			"default", "readOnly", "writeOnly", "const", "exclusiveMinimum",
			"exclusiveMaximum", "multipleOf", "minProperties", "maxProperties",
		} {
			if _, ok := node[key]; ok {
				delete(node, key)
				changed = true
			}
		}
		// A properties map is keyed by user-defined argument names. Those names may
		// themselves be JSON Schema keywords (for example, "default"), so visit
		// only its schema values rather than treating the map as a schema.
		if properties, ok := node["properties"].(map[string]any); ok {
			for _, propertySchema := range properties {
				changed = stripUnsupportedSchemaKeywords(propertySchema) || changed
			}
		}
		for _, key := range []string{"items", "anyOf", "oneOf"} {
			if child, ok := node[key]; ok {
				changed = stripUnsupportedSchemaKeywords(child) || changed
			}
		}
	case []any:
		for _, child := range node {
			changed = stripUnsupportedSchemaKeywords(child) || changed
		}
	}
	return changed
}
