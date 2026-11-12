package contentpolicy

import (
	"bytes"
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/mcp/contentbounds"
)

// Result selects actual MCP payload arrays only after the entire object is
// validated. Unknown fields remain byte-preserved and subject to envelope
// byte/node/depth bounds. All selected items share one aggregate budget.
func Result(raw []byte, inherited int64) error {
	if err := Object(raw, inherited); err != nil {
		return err
	}
	var object map[string]json.RawMessage
	if err := json.Unmarshal(raw, &object); err != nil {
		return ErrShape
	}
	limits := Limits(inherited)
	acc, err := contentbounds.NewAccumulator(limits)
	if err != nil {
		return err
	}
	for _, key := range []string{"tools", "resources", "prompts", "content", "contents", "messages"} {
		value, present := object[key]
		if !present {
			continue
		}
		// Existing typed empty slices may marshal as null; preserve that
		// representation as an empty result, never as an unbounded bypass.
		if bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
			continue
		}
		summary, err := contentbounds.Scan(bytes.NewReader(value), limits)
		if err != nil {
			return err
		}
		if summary.Kind() != contentbounds.KindArray {
			return ErrShape
		}
		var items []json.RawMessage
		if err := json.Unmarshal(value, &items); err != nil {
			return ErrShape
		}
		for _, item := range items {
			if err := AddItem(acc, item, limits); err != nil {
				return err
			}
		}
	}
	return nil
}

// Envelope scans before any JSON decoder can collapse duplicate keys or
// replace malformed UTF-8. A success result gets its own selected-item pass.
func Envelope(raw []byte, inherited int64) error {
	if err := Object(raw, inherited); err != nil {
		return err
	}
	var object map[string]json.RawMessage
	if err := json.Unmarshal(raw, &object); err != nil {
		return ErrShape
	}
	if result, ok := object["result"]; ok {
		return Result(result, inherited)
	}
	return nil
}
