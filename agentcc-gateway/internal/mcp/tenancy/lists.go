package tenancy

import (
	"encoding/json"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

func decodeAll[T any](raws []json.RawMessage) []T {
	out := make([]T, 0, len(raws))
	for _, raw := range raws {
		var v T
		if err := json.Unmarshal(raw, &v); err == nil {
			out = append(out, v)
		}
	}
	return out
}

func sameTools(a, b []mcp.Tool) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i].Name != b[i].Name || string(a[i].Raw) != string(b[i].Raw) {
			return false
		}
	}
	return true
}

func sameResources(a, b []mcp.Resource) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i].URI != b[i].URI || a[i].Name != b[i].Name ||
			a[i].Description != b[i].Description || a[i].MIMEType != b[i].MIMEType ||
			string(a[i].Raw) != string(b[i].Raw) {
			return false
		}
	}
	return true
}

func samePrompts(a, b []mcp.Prompt) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i].Name != b[i].Name || a[i].Description != b[i].Description ||
			len(a[i].Arguments) != len(b[i].Arguments) || string(a[i].Raw) != string(b[i].Raw) {
			return false
		}
		for j := range a[i].Arguments {
			aa, bb := a[i].Arguments[j], b[i].Arguments[j]
			if aa != bb {
				return false
			}
		}
	}
	return true
}
