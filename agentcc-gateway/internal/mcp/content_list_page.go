package mcp

import (
	"bytes"
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/mcp/contentpolicy"
)

// decodeContentListPage enforces the method-specific raw shape on every page,
// including the terminal page, before discovery decodes any typed list items.
// exchange has already validated the bounded result and JSON-RPC envelope.
// Generic result compatibility with null does not apply to required list arrays.
func decodeContentListPage(raw json.RawMessage, key string) ([]json.RawMessage, string, error) {
	raw = bytes.TrimSpace(raw)
	if len(raw) == 0 || raw[0] != '{' {
		return nil, "", contentpolicy.ErrShape
	}
	var page map[string]json.RawMessage
	if err := json.Unmarshal(raw, &page); err != nil {
		return nil, "", contentpolicy.ErrShape
	}
	value, present := page[key]
	if !present {
		return nil, "", contentpolicy.ErrShape
	}
	value = bytes.TrimSpace(value)
	if len(value) == 0 || value[0] != '[' {
		return nil, "", contentpolicy.ErrShape
	}
	var next string
	if cursor, present := page["nextCursor"]; present {
		cursor = bytes.TrimSpace(cursor)
		if len(cursor) == 0 || cursor[0] != '"' || json.Unmarshal(cursor, &next) != nil {
			return nil, "", contentpolicy.ErrShape
		}
	}
	var items []json.RawMessage
	if err := json.Unmarshal(value, &items); err != nil {
		return nil, "", contentpolicy.ErrShape
	}
	return items, next, nil
}
