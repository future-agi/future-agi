package wire

import (
	"encoding/json"
	"fmt"
)

// SetMeta returns params, a JSON object (or empty), with entries merged into
// its _meta object: an entry replaces a key already there.
func SetMeta(params json.RawMessage, entries map[string]json.RawMessage) (json.RawMessage, error) {
	if len(entries) == 0 {
		return params, nil
	}
	fields := map[string]json.RawMessage{}
	if len(params) > 0 && string(params) != "null" {
		if err := json.Unmarshal(params, &fields); err != nil {
			return nil, fmt.Errorf("params are not a JSON object: %w", err)
		}
	}
	meta := map[string]json.RawMessage{}
	if raw, ok := fields["_meta"]; ok && string(raw) != "null" {
		if err := json.Unmarshal(raw, &meta); err != nil {
			return nil, fmt.Errorf("params._meta is not a JSON object: %w", err)
		}
	}
	for k, v := range entries {
		meta[k] = v
	}
	raw, err := json.Marshal(meta)
	if err != nil {
		return nil, err
	}
	fields["_meta"] = raw
	return json.Marshal(fields)
}

// MetaOf returns params' _meta object, or nil.
func MetaOf(params json.RawMessage) map[string]json.RawMessage {
	var p struct {
		Meta map[string]json.RawMessage `json:"_meta"`
	}
	if json.Unmarshal(params, &p) != nil {
		return nil
	}
	return p.Meta
}
