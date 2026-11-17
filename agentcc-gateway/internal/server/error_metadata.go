package server

import "encoding/json"

// hasProviderMetadata recognizes attribution even when the provider name is
// empty. The marker, not the mapped status/type, makes attribution idempotent.
func hasProviderMetadata(metadata any) bool {
	switch meta := metadata.(type) {
	case nil:
		return false
	case map[string]any:
		_, ok := meta["provider"]
		return ok
	case map[string]string:
		_, ok := meta["provider"]
		return ok
	default:
		_, ok := copyErrorMetadata(metadata)["provider"]
		return ok
	}
}

// copyErrorMetadata merges attribution into a new object, never into the
// adapter's map. APIError also permits typed JSON metadata objects.
func copyErrorMetadata(metadata any) map[string]any {
	out := make(map[string]any)
	switch meta := metadata.(type) {
	case nil:
	case map[string]any:
		for k, v := range meta {
			out[k] = v
		}
	case map[string]string:
		for k, v := range meta {
			out[k] = v
		}
	default:
		data, err := json.Marshal(meta)
		if err == nil && json.Unmarshal(data, &out) == nil && out != nil {
			return out
		}
		// Non-object metadata cannot be merged by field. Preserve it as
		// detail rather than silently dropping it during attribution.
		out = map[string]any{"detail": meta}
	}
	return out
}
