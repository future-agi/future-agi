package mcp

import (
	"encoding/json"
	"sort"
	"strings"
)

const (
	searchNameBytes   = 1024
	searchSchemaBytes = 64 * 1024
	searchSchemaNodes = 256
)

func searchPrefix(text string, n int) string {
	return text[:min(len(text), n)]
}

// Index schema property names, not defaults/examples/enum values, $ref URLs or
// arbitrary JSON strings. Nested objects, arrays and schema alternatives are
// bounded by bytes, depth and nodes; references are never fetched or expanded.
func searchParameters(raw json.RawMessage) string {
	if len(raw) == 0 || len(raw) > searchSchemaBytes {
		return ""
	}
	var root any
	if json.Unmarshal(raw, &root) != nil {
		return ""
	}
	var names []string
	remaining := searchSchemaNodes
	var visit func(any, int)
	visit = func(value any, depth int) {
		if remaining <= 0 {
			return
		}
		remaining--
		if depth > 8 {
			return
		}
		schema, ok := value.(map[string]any)
		if !ok {
			return
		}
		if props, ok := schema["properties"].(map[string]any); ok {
			keys := make([]string, 0, len(props))
			for key := range props {
				keys = append(keys, key)
			}
			sort.Strings(keys)
			for _, key := range keys {
				if remaining <= 0 {
					break
				}
				names = append(names, searchPrefix(key, searchNameBytes))
				visit(props[key], depth+1)
			}
		}
		if items, ok := schema["items"]; ok {
			visit(items, depth+1)
		}
		for _, key := range []string{"allOf", "anyOf", "oneOf", "prefixItems"} {
			if alternatives, ok := schema[key].([]any); ok {
				for _, alternative := range alternatives {
					if remaining <= 0 {
						break
					}
					visit(alternative, depth+1)
				}
			}
		}
	}
	visit(root, 0)
	return strings.Join(names, " ")
}
