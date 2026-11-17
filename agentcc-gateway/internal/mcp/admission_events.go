package mcp

import "encoding/json"

// The GET stream's authority is metadata-only. Project only closed list-change
// notifications; content, elicitation and arbitrary logs need their own typed
// action/target envelope and cannot ride this stream as raw event-log bytes.
func admissionMetadataEvent(data []byte) ([]byte, error) {
	if len(data) > 65536 {
		return nil, errAdmissionDenied
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(data, &fields) != nil {
		return nil, errAdmissionDenied
	}
	for name := range fields {
		if name != "jsonrpc" && name != "method" && name != "params" {
			return nil, errAdmissionDenied
		}
	}
	var version, method string
	if json.Unmarshal(fields["jsonrpc"], &version) != nil || version != "2.0" || json.Unmarshal(fields["method"], &method) != nil {
		return nil, errAdmissionDenied
	}
	if method != "notifications/resources/list_changed" && method != "notifications/prompts/list_changed" {
		return nil, errAdmissionDenied
	}
	if params, ok := fields["params"]; ok {
		var object map[string]json.RawMessage
		if json.Unmarshal(params, &object) != nil || len(object) != 0 {
			return nil, errAdmissionDenied
		}
	}
	// Re-encode the closed shape so unknown or duplicate original values are
	// never reflected, even when a JSON decoder would otherwise ignore them.
	out, _ := json.Marshal(struct {
		JSONRPC string `json:"jsonrpc"`
		Method  string `json:"method"`
	}{"2.0", method})
	return out, nil
}
